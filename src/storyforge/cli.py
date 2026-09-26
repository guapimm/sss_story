"""命令行入口 —— 一期的唯一操作接口（决策 D-05：不做前端）。

`doctor` 自检的实现已拆到 `storyforge/doctor.py`（依据是**职责** ——
它是"体检项集合"，与"命令分发"是两件事；当年还叠加了规则 10 的 800 行上限，
该上限已于 2026-09-26 取消，见 `doc/07` §2.1.1）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from storyforge import __version__
from storyforge.commands.learn import add_learn_subparsers, cmd_flows, cmd_research
from storyforge.config import REPO_ROOT, get_settings, python_child_env, reset_settings
from storyforge.doctor import run_doctor


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def cmd_doctor(args: argparse.Namespace) -> int:
    ok, results = run_doctor(verbose=args.verbose)
    width = max(len(r["check"]) for r in results) if results else 10
    for r in results:
        # ★ ok 三态：True/False/None(跳过)。SKIP 必须显式可见，不得混入 OK
        # （"跳过被当成通过"是本项目明令禁止的静默降级同型病）
        mark = "SKIP" if r["ok"] is None else ("OK  " if r["ok"] else "FAIL")
        print(f"  [{mark}] {r['check']:<{width}}  {r['detail']}")
    print()
    print(f"doctor: {'PASS' if ok else 'FAIL'}  (storyforge {__version__})")
    return 0 if ok else 1




# --------------------------------------------------------------------------- #
# init-db
# --------------------------------------------------------------------------- #


def cmd_init_db(args: argparse.Namespace) -> int:
    from storyforge.infra.storage.db import Database

    s = get_settings()
    s.ensure_dirs()
    db = Database.from_settings(s)
    db.create_all()
    print(f"数据库已就绪：{s.db_path}")
    print(f"  表：{len(db.list_tables())} 张")
    print(f"  外键强制：{db.foreign_keys_enabled()}")
    print(f"  journal_mode：{db.journal_mode()}")
    return 0


# --------------------------------------------------------------------------- #
# arch-check（架构不变量）
# --------------------------------------------------------------------------- #


def cmd_arch_check(args: argparse.Namespace) -> int:
    """运行架构契约测试（AST 检查）。"""
    import subprocess

    # ★ 经 config.python_child_env() 钉死子进程 UTF-8（详见该函数 docstring）：
    # 父进程按 utf-8 解码、子进程被注入 PYTHONIOENCODING=utf-8，
    # 与调用方控制台是否 GBK 无关。契约规则 12：cli.py 不得直读 os.environ。
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/contract", "-q", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=python_child_env(),
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    print("\n".join(tail))
    return proc.returncode


# --------------------------------------------------------------------------- #
# score（打分卡：对真实文本打分）
# --------------------------------------------------------------------------- #


def _score_learn(texts: list[str], *, window: int) -> list:
    """用 learn 打分卡给教学章打分（零 LLM）。

    ## 平替修辞维度需要**已确认的映射表**

    `term_substitution` / `bare_term` 两个维度必须知道"哪些术语该被替换"。
    数据来源是本书的 `learning/map.json`（L-1/L-2 已确认的部分）。

    ⚠️ **取不到映射表时不假装**：`LearnMetrics` 留空 →
    相关维度变成 `None`（**无法评估**，记 0 分）而不是"通过"。
    这与 `VerificationOut.accuracy()` 的空结果口径一致 ——
    本项目反复吃亏于"空结果当成通过"的假绿。

    ★ 打印一行说明为什么那些维度无法评估，而不是静默给 0 分
    （静默给 0 会让"没数据"看起来像"质量差"）。
    """
    from storyforge.scorecard.learn import (
        LearnMetrics,
        count_bare_terms,
        count_term_usage,
        evaluate_learn,
    )

    mappings: list[tuple[str, str]] = []
    try:
        from storyforge.infra.storage.artifacts import ArtifactStore

        store = _artifacts()
        # 遍历已有书目录找 learning/map.json（一期无 book_id 参数时的兜底）
        for p in sorted((store.root / "books").glob("*/learning/map.json")):
            import json as _json

            data = _json.loads(p.read_text(encoding="utf-8"))
            for m in data.get("mappings") or []:
                if m.get("accepted") is True and m.get("term"):
                    mappings.append(
                        (str(m["term"]), str(m.get("replacement") or ""))
                    )
            if mappings:
                print(f"平替修辞：用书 {p.parent.parent.name} 的 "
                      f"{len(mappings)} 条**已确认**映射")
                break
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：读取平替修辞表失败（{type(exc).__name__}）—— "
              f"平替相关维度将记为「无法评估」）")

    if not mappings:
        print("（提示：没有**已确认**的平替修辞映射 → "
              "`term_substitution` / `bare_term` 两维记「无法评估」）")

    cards = []
    for i, t in enumerate(texts):
        bare, _hits = count_bare_terms(t, mappings)
        hit, expected = count_term_usage(t, mappings)
        cards.append(evaluate_learn(
            t, global_no=i + 1,
            prev_texts=texts[max(0, i - window):i],
            metrics=LearnMetrics(
                bare_term_hits=bare,
                substitution_hits=hit, substitution_expected=expected,
                # ⚠️ 知识点/解释/递进/复习四类的输入来自**教学账本与学习地图**，
                #    需章节与知识点的对应关系（步骤 ⑥ 才建立）→ 一期留空，
                #    对应维度记「无法评估」，并已在上方提示。
            ),
        ))
    return cards


def cmd_score(args: argparse.Namespace) -> int:
    """对目录下的章节文本打分（零 LLM、可复现）。

    用法：
        storyforge score --dir <章节目录> --pattern "v1-*.txt"
        storyforge score --dir <目录> --baseline     # 与旧 baseline 对比
        storyforge score --dir <目录> --learn        # ★ 用 learn（知识小说）打分卡

    ## ★ `--learn` 为什么存在（实测驱动）

    用 story 打分卡评**教学章**会**误杀**：`dialogue_low` 是硬触发，
    而教学章「不要对话」才是正确写法（`score=0.51 / passed=False`）。
    那是**维度集合错配**，不是调参问题 —— 故 learn 有自己的卡。

    两种卡的**度量原语共享**（跨章重叠/章内冗余/对话/比喻），
    但**维度集合与方向不同**（对话在 story 是下限、在 learn 是上限）。
    """
    import statistics
    from collections import Counter
    from pathlib import Path as _Path

    d = _Path(args.dir)
    if not d.exists():
        print(f"目录不存在：{d}", file=sys.stderr)
        return 2
    files = sorted(d.glob(args.pattern))
    if not files:
        print(f"未匹配到文件：{d / args.pattern}", file=sys.stderr)
        return 2

    texts = [f.read_text(encoding="utf-8", errors="ignore") for f in files]
    window = get_settings().scorecard.cross_window

    if getattr(args, "learn", False):
        cards = _score_learn(texts, window=window)
        print("打分卡：**learn**（知识小说）—— 维度集合与 story 不同")
    else:
        from storyforge.scorecard import evaluate

        cards = [
            evaluate(t, global_no=i + 1, prev_texts=texts[max(0, i - window) : i])
            for i, t in enumerate(texts)
        ]
        print("打分卡：story（小说）")

    print(f"章节数：{len(cards)}")
    scores = [c.score for c in cards]
    print(
        f"总分：min={min(scores):.3f} "
        f"p50={statistics.median(scores):.3f} max={max(scores):.3f}"
    )
    need = sum(1 for c in cards if c.needs_review)
    forced = sum(1 for c in cards if c.forced_review)
    print(f"需审校：{need}/{len(cards)} = {100 * need / len(cards):.0f}%"
          f"（其中强制 {forced}）")

    trig = Counter(t.value for c in cards for t in c.triggers)
    if trig:
        print("\n触发分布：")
        for k, v in trig.most_common():
            print(f"  {k:24} {v:4}")

    fail = Counter()
    total = Counter()
    for c in cards:
        for dim in c.dimensions:
            total[dim.name] += 1
            if dim.passed is False:
                fail[dim.name] += 1
    print("\n维度失败率：")
    for name, n in total.most_common():
        f = fail.get(name, 0)
        bar = "#" * int(24 * f / n) if n else ""
        print(f"  {name:24} {f:3}/{n:3} {100 * f / n:5.0f}%  {bar}")

    if args.json:
        import json as _json

        out = _Path(args.json)
        out.write_text(
            _json.dumps([c.to_dict() for c in cards], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n逐章明细已写入：{out}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """复现旧项目 baseline（验证口径一致性）。

    这是 P2 的验收命令：若口径漂移，此命令会明确报出差异。
    """
    import statistics
    from collections import Counter
    from pathlib import Path as _Path

    from storyforge.scorecard.literal import (
        analyze,
        classify_ending,
        triple_parallelism_count,
    )

    d = _Path(args.dir)
    if not d.exists():
        print(f"目录不存在：{d}", file=sys.stderr)
        return 2
    files = sorted(
        d.glob(args.pattern),
        key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0),
    )
    if not files:
        print("未匹配到文件", file=sys.stderr)
        return 2
    texts = [f.read_text(encoding="utf-8", errors="ignore") for f in files]
    ms = [analyze(t, prev_texts=texts[max(0, i - 5) : i]) for i, t in enumerate(texts)]

    def p50(xs):
        vals = [x for x in xs if x is not None]
        return statistics.median(vals) if vals else float("nan")

    rows = [
        ("chars p50", p50([m.chars for m in ms]), 5065.0),
        ("dialogue p50", p50([m.dialogue_ratio for m in ms]), 0.1058),
        ("intra p50", p50([m.intra_redundancy for m in ms]), 0.0621),
        ("cross p50", p50([m.cross_chapter_overlap for m in ms]), 0.0985),
        ("triple total", float(sum(m.triple_parallelism for m in ms)), 60.0),
        ("triple chapters",
         float(sum(1 for m in ms if m.triple_parallelism)), 33.0),
    ]
    print(f"章节数：{len(ms)}")
    print(f"{'指标':<18}{'本实现':>12}{'旧baseline':>12}{'差异':>12}  判定")
    ok = True
    for name, ours, want in rows:
        tol = 1.0 if want > 1 else 0.0015
        good = abs(ours - want) <= tol
        ok &= good
        print(f"{name:<18}{ours:>12.4f}{want:>12.4f}{ours - want:>+12.4f}  "
              f"{'OK' if good else '**DIFF**'}")

    ek = Counter(classify_ending(t).value for t in texts)
    want_ek = {"mid_action": 23, "omen": 2, "other": 21, "dialogue": 1}
    ematch = all(ek.get(k, 0) == v for k, v in want_ek.items())
    print(f"\n章末分布：{dict(ek)}")
    print(f"旧 baseline：{want_ek}")
    print(f"判定：{'OK' if ematch else '**DIFF**'}")
    ok &= ematch

    print(f"\n===> {'口径一致（baseline 复现成功）' if ok else '口径存在差异，需排查'}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# plan（规划链路：圣经 → 全卷台阶 → 骨架 → 细纲）
# --------------------------------------------------------------------------- #


def _make_client(args: argparse.Namespace | None = None) -> Any:
    """构造接真实模型的 LLMClient（LangChain 传输层）。

    ★ 这是"基于 langchain"的落地处：`LLMClient` 的 `transport` 走
    `ChatOpenAI`。缺 API key 时**显式报错**（不静默空跑）。

    ★ 预算闸门**只在显式传参时启用**（负责人 2026-09-26 裁决）：
    三个预算参数默认 `None`，不传就是旧行为。**刻意不设默认上限** ——
    真实模型运行的成本帽必须由人当场决定，不能由代码里一个占位值代替。
    """
    from storyforge.infra.llm.client import ClientConfig, LLMClient
    from storyforge.infra.llm.transport import make_transport

    s = get_settings()
    return LLMClient(
        make_transport(),
        config=ClientConfig(
            max_attempts=s.llm.max_attempts,
            timeout_s=s.llm.default_timeout_s,
            max_provider_requests=getattr(args, "max_requests", None),
            max_prompt_chars=getattr(args, "max_prompt_chars", None),
            max_configured_credits=getattr(args, "max_credits", None),
        ),
    )


def _add_budget_args(p: argparse.ArgumentParser) -> None:
    """给需要花钱的子命令挂上预算参数（`plan` / `write`）。

    ★ 全部默认 `None` = 不启用。要真实跑 S0 就**必须**显式给出上限，
    否则第一次越线时没有任何东西会停下来。
    """
    p.add_argument(
        "--max-requests", type=int, default=None,
        help="provider 请求数硬上限（含重试；默认不启用）。真实运行务必设置",
    )
    p.add_argument(
        "--max-prompt-chars", type=int, default=None,
        help="单次 prompt 字符数硬上限（默认不启用）",
    )
    p.add_argument(
        "--max-credits", type=float, default=None,
        help="MiMo 点数（CREDITS）上限；按返回 usage 事后累计，"
             "越线即停机（最后一次请求可能越线）。默认不启用",
    )


def _print_cost_summary(client: Any) -> None:
    """打印本次运行的调用与成本摘要（按**币种分开**，不跨单位相加）。

    ★ 为什么必须给出：没有它就无法回答"这次 S0 花了多少点数"，
    而成本正是 S0 验收的五个维度之一。
    """
    from storyforge.observability.report import cost_by_currency

    print()
    if getattr(client, "budget_stopped", False):
        print(f"■ 预算已停机：{client.budget_stop_reason}")
    if getattr(client, "unknown_usage", False):
        print("■ ⚠️ 出现过**无法记账**的调用（缺 usage / usage 非法）")
    if not client.records:
        print("LLM 调用 0 次（未产生任何费用）")
        return

    rep_client = client.records
    print(f"LLM 调用 {len(rep_client)} 次，"
          f"空输出率 {client.blank_rate():.1%}（旧项目实测 22.2%）")
    costs = cost_by_currency(rep_client)
    if not costs:
        print("成本：无可用单价（检查 config/providers.toml 的 pricing）")
        return
    # ★ 分币种展示：CREDITS 与 USD 是不同单位，**不相加**
    parts = [
        f"{amount:,.4f} {currency}" if currency == "CREDITS"
        else f"${amount:,.4f} {currency}"
        for currency, amount in sorted(costs.items())
    ]
    print(f"成本：{'  |  '.join(parts)}")
    if getattr(client, "configured_credits", 0.0):
        print(f"其中已记账点数：{client.configured_credits:,.4f} CREDITS")


def _artifacts():
    from storyforge.infra.storage.artifacts import ArtifactStore

    return ArtifactStore.from_settings()


def _register_book(*, book_id: int, topic: str, genre: str,
                   target_chapters: int | None = None) -> None:
    """把书登记进 `books` 表 —— **FK 根，必须先于任何子表写入存在**。

    ★ 为什么必须有这一步（S0 真实取证暴露的缺陷）：
    `entities.book_id` / `entity_relations.book_id` / `injuries.book_id`
    都有 `FOREIGN KEY -> books.id`，而 `books` 行**全仓没有任何生产写入点** ——
    `BookRepository.upsert()` 写好了，却只在测试里被调用。

    后果（实测）：S0 单章跑通、正文 4484 字已落盘，但实体/关系入库抛
    `IntegrityError: FOREIGN KEY constraint failed`，**抽取产物一条没进 DB**。
    这正是本项目反复出现的"设计了但没接线"同型缺陷。

    数据库不可用时**不阻断**（产物在盘上，写作本身不依赖 DB），
    但会打印一行说明，绝不静默降级。
    """
    try:
        from storyforge.infra.storage.db import Database
        from storyforge.infra.storage.repositories import BookRepository

        db = Database.open(get_settings().db_path)
        BookRepository(db).upsert(
            book_id=book_id, topic=topic, genre=genre,
            target_chapters=target_chapters or 400,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：书籍未登记（{type(exc).__name__}: {exc}），"
              f"实体/关系**无法入库**（FK 根缺失））", file=sys.stderr)


def _book_topic_from_store(store: Any, book_id: int) -> str:
    """从盘上已生成的世界观圣经反推主题（`write` 没有 `--topic` 参数）。

    圣经首行由 `plan` 写成 `# <topic>`（见 `bible_architect`），
    读不到时返回空串，由调用方兜底 —— 不编造主题。
    """
    try:
        head = (store.read_text(f"books/{book_id}/bible.md") or "").lstrip()
    except Exception:  # noqa: BLE001
        return ""
    for line in head.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()[:200]
        if line:
            break
    return ""


@contextlib.asynccontextmanager
async def _checkpointer():
    """打开 checkpoint 存储并交给调用方（不可用时 yield None）。

    ★ 为什么必须有（S0 后核实的断点）：
    `open_checkpointer()` 此前**全仓只被测试调用**，五个 `compile()` 都不传
    checkpointer —— 于是"崩溃恢复后已完成单元不重跑"（不变量 4）**在生产中
    从未成立**，`data/checkpoints.db` 也从未产生过。

    checkpoint 不可用**不阻断**（图仍能跑，只是没有恢复能力），
    但会打印一行说明 —— 不静默降级。
    """
    try:
        from storyforge.graph.graphs.books import open_checkpointer

        async with open_checkpointer() as saver:
            yield saver
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：checkpoint 不可用（{type(exc).__name__}: {exc}），"
              f"本次无断点恢复能力）", file=sys.stderr)
        yield None


async def _invoke_resumable(graph: Any, payload: dict[str, Any],
                            cfg: dict[str, Any]) -> Any:
    """**有未完成 checkpoint 就续跑，否则从 START 开始**。

    ★ 为什么不能永远传完整 dict（S0 后核实的断点）：
    LangGraph 的 `ainvoke(完整输入, cfg)` 会**从 START 重跑**，
    即使该 thread 已有 checkpoint —— 实测：

        ainvoke(None,  cfg)  -> seen=['a','b']          真 resume
        ainvoke(输入, cfg)   -> seen=['a','b','a','b']  重跑了！

    而 CLI 此前**永远传完整 dict**，于是 checkpointer 的意义被完全抵消
    （重放会让已完成的 LLM 调用按幂等缓存命中而*不*重复扣费，
    但节点逻辑、盘上读写、调度开销全部白做一遍）。

    判据用 `aget_state(cfg).next` 非空 = 有未完成工作。

    ⚠️ **图没有 checkpointer 时必须跳过探测**：`aget_state()` 在无 checkpointer
    时会抛 `ValueError: No checkpointer set`（实测），直接崩掉整条链路。
    故先用 `graph.checkpointer` 判空，退回"传完整输入"的旧行为。
    """
    if getattr(graph, "checkpointer", None) is None:
        # 无恢复能力（checkpoint 不可用 / 纯文件模式）→ 旧行为，不探测
        return await graph.ainvoke(payload, cfg)
    state = await graph.aget_state(cfg)
    if state is not None and getattr(state, "next", ()):
        print(f"     ↻ 检测到未完成断点（{state.next}），续跑而非重跑")
        return await graph.ainvoke(None, cfg)
    return await graph.ainvoke(payload, cfg)


def _graph_deps(*, client: Any = None, checkpointer: Any = None) -> Any:
    """构造 `GraphDeps` 并**接上仓储**（0-7 的生产接线点）。

    ★ 为什么必须在这里接线：伤势恢复链路的第 ③ 环是"调度器读得到真实伤势"，
    而它依赖 `GraphDeps.injuries` 非空。若只在测试里赋值，
    生产链路的 `health_beats_injected` 依然是恒 0 —— 正是修复前的状态。

    数据库不可用时**不阻断**：伤势是增强项（细纲没有它也能生成），
    但会打印一行说明（不静默降级）。
    """
    from storyforge.graph.graphs.books import GraphDeps
    from storyforge.infra.storage.db import Database
    from storyforge.infra.storage.repositories import InjuryRepository

    store = _artifacts()
    injuries = None
    try:
        s = get_settings()
        db = Database.open(s.db_path)
        injuries = InjuryRepository(db)
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：伤势仓储未接线（{type(exc).__name__}: {exc}），"
              f"本批次不注入恢复节拍）", file=sys.stderr)
    return GraphDeps(client=client, store=store, injuries=injuries,
                     checkpointer=checkpointer)


def _guard_api_key() -> int | None:
    """缺 key 时给出明确指引（返回退出码），否则 `None`。"""
    from storyforge.infra.llm.capability import load_registry

    reg = load_registry()
    missing = [
        p for p in reg.providers.values()
        if p.api_key_env and not p.resolve_api_key()
    ]
    # 只有当**默认 provider** 也拿不到 key 时才拦（其余 provider 可能用不上）
    default_name = getattr(reg, "default_provider", None)
    default_ok = True
    if default_name and default_name in reg.providers:
        default_ok = bool(reg.providers[default_name].resolve_api_key())
    if not default_ok:
        print("未配置 API key。二选一：", file=sys.stderr)
        print("  ① 写进 .env（推荐，已被 .gitignore 忽略）：", file=sys.stderr)
        for p in missing:
            print(f"       {p.api_key_env}=sk-...", file=sys.stderr)
        print("  ② 或临时设环境变量（仅当前 shell 有效）：", file=sys.stderr)
        for p in missing:
            print(f'       $env:{p.api_key_env} = "sk-..."', file=sys.stderr)
        print(f"\n  .env 位置：{REPO_ROOT / '.env'}", file=sys.stderr)
        print("  （模板见 .env.example；可运行 `storyforge doctor` 查看配置状态）",
              file=sys.stderr)
        return 2
    return None


async def _run_plan(args: argparse.Namespace, client: Any = None,
                  checkpointer: Any = None) -> int:
    from storyforge.graph.graphs.books import thread_config
    from storyforge.graph.graphs.books import (
        build_bible_graph,
        build_detail_batch_graph,
        build_skeleton_graph,
        build_volume_plan_graph,
    )

    store = _artifacts()
    # ★ 默认自己造；`cmd_plan` 传入自己那份，好让**预算状态与调用记录
    #   在 `_run_plan` 返回后仍可读**（否则摘要拿到的是空记录）。
    client = client if client is not None else _make_client(args)
    # ★ FK 根先落库：entities/relations/injuries 都指向 books.id
    _register_book(book_id=args.book_id, topic=args.topic, genre=args.genre,
                   target_chapters=args.book_chapters)
    # ★ 0-7：经 `_graph_deps()` 接线仓储（伤势恢复链路的 ③ 环）
    deps = _graph_deps(client=client, checkpointer=checkpointer)
    s = get_settings()

    book_id = args.book_id

    # ── ① 圣经 ──
    print(f"[1/4] 生成世界观圣经（book {book_id}）…")
    out = await _invoke_resumable(
        build_bible_graph(deps),
        {"book_id": book_id, "topic": args.topic, "genre": args.genre, "refs": {}},
        thread_config(f"b{book_id}:bible"),
    )
    if not out.get("ok"):
        print(f"圣经生成失败：{out.get('violations')}", file=sys.stderr)
        return 1
    bible_ref = out["refs"]["bible_ref"]
    print(f"      → {bible_ref}")

    # ── ② 全卷台阶（按**整本书**规划）──
    #
    # ★ `--chapters` 是「先写几章」，不是「全书几章」。
    #
    # 为什么：台阶的意义在于**横向扩张**——每卷开新地图、换新对手。
    # 若按"全书 25 章"规划，25 章本来就该发生在一个地方，
    # 于是恰恰测不出本项目要修的核心缺陷（旧项目 400 章困在一栋楼里）。
    # 因此按 `--book-chapters`（默认 400，旧项目实测量级）规划全部卷，
    # 只把正文先写前 `--chapters` 章。
    print(f"[2/4] 规划全部卷的升级台阶（全书 {args.book_chapters} 章）…")
    out = await _invoke_resumable(
        build_volume_plan_graph(deps),
        {
            "book_id": book_id, "topic": args.topic, "genre": args.genre,
            "target_chapters": args.book_chapters,
            "refs": {"bible_ref": bible_ref},
        },
        thread_config(f"b{book_id}:volume_plan"),
    )
    if not out.get("gate_ok"):
        print("结构闸门未通过：", file=sys.stderr)
        for i in out.get("gate_issues", [])[:20]:
            vol = f"（第{i.get('volume_no')}卷）" if i.get("volume_no") else ""
            print(f"  - [{i.get('code')}]{vol} {i.get('message')}", file=sys.stderr)
        print(
            "\n（台阶必须一次定稿全部卷 —— 旧项目只细化第 1 卷 → 400 章挤第 1 卷）",
            file=sys.stderr,
        )
        return 1
    print(f"      → {out['series_plan_ref']}（{out['total_volumes']} 卷，"
          f"修复轮次 {out['repair_round']}）")

    # ── ③ 骨架（本卷） ──
    print(f"[3/4] 生成本卷骨架（第 {args.volume} 卷）…")
    out = await _invoke_resumable(
        build_skeleton_graph(deps),
        {"book_id": book_id, "current_volume": args.volume, "batch_no": 1,
         "refs": {"bible_ref": bible_ref}},
        thread_config(f"b{book_id}:skeleton:v{args.volume}"),
    )
    if out.get("violations"):
        print(f"骨架未通过校验：{out['violations']}", file=sys.stderr)
        return 1
    print(f"      → {out['skeleton_ref']}")

    # ── ④ 细纲（首批） ──
    batch = s.pipeline.detail_batch_size
    end = min(args.chapters, batch)
    print(f"[4/4] 生成本卷细纲（第 1~{end} 章，含伤势节拍注入）…")
    out = await _invoke_resumable(
        build_detail_batch_graph(deps),
        {"book_id": book_id, "current_volume": args.volume,
         "batch_start": 1, "batch_end": end,
         "refs": {"bible_ref": bible_ref}},
        thread_config(f"b{book_id}:detail:v{args.volume}:1-{end}"),
    )
    # ★ 缺陷1修复：与 `multibook_e2e.py` 同口径 —— 空 lint 不等于成功，
    # 必须同时无 error 且产物数量对上（防两处判定漂移，D-15）。
    detail_refs = out.get("detail_refs") or []
    detail_failed = (bool(out.get("lint_issues"))
                     or bool(out.get("detail_error") or "")
                     or len(detail_refs) != end)
    if detail_failed:
        if out.get("detail_error"):
            print(f"细纲生成失败：{out['detail_error']}", file=sys.stderr)
        print("细纲未通过 lint：", file=sys.stderr)
        for i in (out.get("lint_issues") or [])[:20]:
            print(f"  - {i}", file=sys.stderr)
        return 1
    print(f"      → {len(detail_refs)} 章细纲已落盘，"
          f"注入伤势节拍 {out['health_beats_injected']} 条")
    if out.get("awaiting_confirm"):
        print(f"\n⚠️  到达人工确认点：{out['awaiting_confirm']}")
        print("   请审阅产物后再继续（`storyforge write`）。")

    print("\n规划完成。下一步：")
    print(f"  uv run storyforge write --book-id {book_id} --chapters {end}")
    return 0


def _persist_calls(client: Any) -> None:
    """把本次运行的 LLM 调用写进 `llm_calls` 表（受 `persist_calls` 开关控制）。

    ★ 为什么必须在 CLI 边界做：`record_to_row()` 此前**零生产调用点**，
    导致该表永远 0 行、费用摘要跨运行就查不到账 —— 而成本是 S0 验收维度之一。
    记录只活在 `client.records` 里，进程一退就没了，故收口点只能是这里。

    落库失败**不抛**（生成已完成且已付费，不该因观测失败而报错），
    但会打印出来 —— 不静默降级。
    """
    records = getattr(client, "records", None) or []
    if not records:
        return
    if not get_settings().observability.persist_calls:
        print(f"（提示：`persist_calls=false`，本次 {len(records)} 条调用未入库）",
              file=sys.stderr)
        return
    try:
        from storyforge.infra.storage.db import Database
        from storyforge.observability.report import persist_calls

        db = Database.open(get_settings().db_path)
        written = persist_calls(records, db)
        print(f"（调用记录已入库：{written}/{len(records)} 条 → llm_calls）")
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：调用记录未入库（{type(exc).__name__}: {exc}），"
              f"本次费用无法事后查询）", file=sys.stderr)


def _budget_exit(rc: int, client: Any) -> int:
    """预算停机必须产生**非零退出**。

    ★ 为什么不能只打印一行警告：脚本/CI 只看退出码。若预算停机仍返回 0，
    批量跑批会把"钱花光了、后半本没写"当成成功，继续下一本。

    退出码 `3` 与通用失败 `1` 区分开 —— S0 取证时要能一眼看出
    "是撞了预算"还是"生成失败"，这两者的后续动作完全不同。
    """
    if getattr(client, "budget_stopped", False) and rc == 0:
        print("预算停机 → 按失败处理（退出码 3）。", file=sys.stderr)
        return 3
    return rc


def cmd_plan(args: argparse.Namespace) -> int:
    import asyncio

    rc = _guard_api_key()
    if rc is not None:
        return rc
    client = _make_client(args)

    async def _go() -> int:
        # checkpointer 必须在整段执行期间保持打开（图持它的引用）
        async with _checkpointer() as saver:
            return await _run_plan(args, client, checkpointer=saver)

    try:
        rc = asyncio.run(_go())
    finally:
        # 无论成败都要报账 —— 失败的那次一样烧了钱
        _print_cost_summary(client)
        _persist_calls(client)
    return _budget_exit(rc, client)


# --------------------------------------------------------------------------- #
# write（单章闭环）
# --------------------------------------------------------------------------- #


async def _run_write(args: argparse.Namespace, client: Any = None,
                     checkpointer: Any = None) -> int:
    from storyforge.graph.graphs.chapter import ChapterDeps, build_chapter_graph
    from storyforge.graph.graphs.books import thread_config

    store = _artifacts()
    # ★ 同 `_run_plan`：由调用方传入，预算状态与记录才留得住
    client = client if client is not None else _make_client(args)

    # ★ 实体/关系仓储：**可选注入**，与 `_graph_deps` 里伤势仓储同样的纪律
    #   （数据库不可用不阻断写作，但要**明确说明**未入库，不静默降级）。
    entities = relations = None
    try:
        from storyforge.infra.storage.db import Database
        from storyforge.infra.storage.repositories import (
            EntityRepository,
            RelationRepository,
        )

        db = Database.open(get_settings().db_path)
        entities, relations = EntityRepository(db), RelationRepository(db)
    except Exception as exc:  # noqa: BLE001
        print(f"（提示：实体/关系仓储未接线（{type(exc).__name__}: {exc}），"
              f"本章抽取结果**不入库**）", file=sys.stderr)

    # ★ FK 根：`entities` / `entity_relations` 都指向 `books.id`。
    #   即使 `plan` 时已登记，这里也**幂等**再 upsert 一次 ——
    #   否则对"产物在盘上但 DB 无行"的既有书直接 write，仍会整批 FK 失败。
    #   `upsert` 已存在时只更新非空字段、**不重置进度**，重复调用安全。
    #
    #   topic/genre 从盘上**已生成的圣经**反推（write 没有这两个参数）：
    #   圣经首行是"# <标题>"，标题由 plan 用 topic 写入。
    _register_book(book_id=args.book_id,
                   topic=_book_topic_from_store(store, args.book_id) or f"book {args.book_id}",
                   genre="")

    deps = ChapterDeps(client=client, store=store,
                       entities=entities, relations=relations,
                       checkpointer=checkpointer)
    graph = build_chapter_graph(deps)

    bible_ref = f"books/{args.book_id}/bible.md"
    if not store.exists(bible_ref):
        print(f"缺少世界观圣经：{bible_ref}（请先运行 `storyforge plan`）",
              file=sys.stderr)
        return 2

    done = 0
    for no in range(args.start, args.start + args.chapters):
        try:
            store.read_text(f"books/{args.book_id}/plans/{no:05d}.json")
        except Exception:  # noqa: BLE001
            print(f"第{no}章细纲不存在，停止（请先运行 `storyforge plan`）",
                  file=sys.stderr)
            break

        print(f"第{no}章 …", end=" ", flush=True)
        out = await _invoke_resumable(
            graph,
            {
                "book_id": args.book_id, "global_no": no, "volume_no": args.volume,
                "detail_ready_through": no,
                "refs": {"bible_ref": bible_ref},
                "is_volume_end": False,
            },
            thread_config(f"b{args.book_id}:chapter:{no}"),
        )
        status = out.get("status", "?")
        if status == "committed":
            note = out.get("commit_note", "")
            print(f"OK  {out.get('prose_chars')} 字"
                  f"{'（已修订）' if out.get('revision_applied') else ''}"
                  + (f"\n     {note}" if note else ""))
            for p in (out.get("persist_problems") or [])[:3]:
                print(f"     ⚠ {p}")
            done += 1
        else:
            print(f"未完成：{status} {out.get('error', '')}")
            if status in ("write_failed", "blocked"):
                break

        # ★ 预算停机后**立刻停止后续章** —— 否则每章都会再撞一次，
        #   白跑一遍调度与盘上读写，且日志被同样的错误刷屏。
        if getattr(client, "budget_stopped", False):
            print("\n预算停机，停止后续章。", file=sys.stderr)
            break

    print(f"\n完成 {done} 章。")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    import asyncio

    rc = _guard_api_key()
    if rc is not None:
        return rc
    client = _make_client(args)

    async def _go() -> int:
        # checkpointer 必须在整段执行期间保持打开（图持它的引用）
        async with _checkpointer() as saver:
            return await _run_write(args, client, checkpointer=saver)

    try:
        rc = asyncio.run(_go())
    finally:
        # 无论成败都要报账 —— 失败的那次一样烧了钱
        _print_cost_summary(client)
        _persist_calls(client)
    return _budget_exit(rc, client)


# --------------------------------------------------------------------------- #
# report（Token 与成本报告）
# --------------------------------------------------------------------------- #


def cmd_report(args: argparse.Namespace) -> int:
    """打印模型单价与成本结构（对策⑦）。

    旧项目对照：命中 44% 但缓存命中只占成本 **0.35%**，输出占 **77.5%**
    —— 优化重点应在输出侧，而旧项目一直在调缓存。
    """
    from storyforge.observability.report import pricing_table

    table = pricing_table()
    if not table:
        print("未读到任何模型单价（检查 config/providers.toml 的 pricing）",
              file=sys.stderr)
        return 1
    print("模型单价（来源：config/providers.toml）")
    print(f"{'模型':<22}{'币种':<10}{'输入':>9}{'缓存命中':>11}{'输出':>9}")
    for model_id, p in table.items():
        print(f"{model_id:<22}{p.currency:<10}{p.input:>9}{p.cached_input:>11}{p.output:>9}")
    print()
    print("⚠️ 币种必须显式区分：Token Plan 用**点数（CREDITS）**，其余用美元。")
    print("   旧项目只有一套 MiMo 点数权重（hit=2/miss=100/output=200）却用在")
    print("   占 61% 流量的 deepseek 上 → 跨单位混算，成本完全失真。")
    print()
    print("成本结构提示（旧项目实测）：缓存 0.35% / 输出 77.5% / 输入 22.2%")
    print("   → 优化重点在**输出侧**（空输出率、思考预算），而非缓存命中率。")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storyforge", description="长篇小说生成系统")
    p.add_argument("--version", action="version", version=f"storyforge {__version__}")
    sub = p.add_subparsers(dest="command", required=False)

    d = sub.add_parser("doctor", help="自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("init-db", help="初始化数据库（建表 + 开启外键）")
    i.set_defaults(func=cmd_init_db)

    a = sub.add_parser("arch-check", help="运行架构不变量检查（AST）")
    a.set_defaults(func=cmd_arch_check)

    sc = sub.add_parser("score", help="对章节文本打分（零 LLM、可复现）")
    sc.add_argument("--dir", required=True, help="章节目录")
    sc.add_argument("--pattern", default="*.txt", help="文件名通配（默认 *.txt）")
    sc.add_argument("--json", help="把逐章明细写入该 JSON 文件")
    sc.add_argument(
        "--learn", action="store_true",
        help="★ 用 learn（知识小说）打分卡：维度集合与 story 不同"
             "（教学章「不要对话」是正确写法，story 卡会误杀）",
    )
    sc.set_defaults(func=cmd_score)

    bl = sub.add_parser("baseline", help="复现旧项目 baseline（验证口径一致性）")
    bl.add_argument("--dir", required=True, help="旧项目章节目录")
    bl.add_argument("--pattern", default="v1-*.txt", help="文件名通配")
    bl.set_defaults(func=cmd_baseline)

    pn = sub.add_parser(
        "plan", help="规划链路：圣经 → 全卷台阶 → 骨架 → 细纲（需 API key）"
    )
    pn.add_argument("--book-id", type=int, required=True)
    pn.add_argument("--topic", required=True, help="主题")
    pn.add_argument("--genre", default="suspense", help="题材（默认 suspense）")
    pn.add_argument(
        "--book-chapters", type=int, default=400,
        help="**全书**规划章数（默认 400，旧项目实测量级）。"
        "台阶按这个规模规划 —— 横向扩张必须按整本书来设计，"
        "否则「全书困在一处」这个核心缺陷测不出来",
    )
    pn.add_argument(
        "--chapters", type=int, default=25,
        help="本卷先生成几章细纲（默认 25）。**不是全书章数**",
    )
    pn.add_argument("--volume", type=int, default=1, help="先做第几卷（默认 1）")
    _add_budget_args(pn)
    pn.set_defaults(func=cmd_plan)

    w = sub.add_parser("write", help="单章闭环：写作 → 打分 → 审校 → 抽取（需 API key）")
    w.add_argument("--book-id", type=int, required=True)
    w.add_argument("--start", type=int, default=1, help="起始章号（默认 1）")
    w.add_argument("--chapters", type=int, default=1, help="写几章（默认 1）")
    w.add_argument("--volume", type=int, default=1)
    _add_budget_args(w)
    w.set_defaults(func=cmd_write)

    rp = sub.add_parser("report", help="Token 与成本报告")
    rp.set_defaults(func=cmd_report)

    # learn 侧的可视化命令（flows / research）——
    # 实现在 `commands/learn.py`（拆出的理由见该模块 docstring）
    add_learn_subparsers(sub)

    return p


def _force_utf8_console() -> None:
    """把 stdout/stderr 切到 UTF-8 —— Windows 控制台默认 GBK。

    ⚠️ 这不是洁癖：中文输出在 GBK 控制台上会直接抛 `UnicodeEncodeError`
    把命令打挂（实测 `report` 因一个 ⚠️ 字符整个崩掉）。
    本项目是中文优先的，因此入口处强制 UTF-8，而不是到处规避非 ASCII 字符。

    `errors="replace"` 是兜底：即使终端仍不支持，也只把个别字显示成 `?`，
    而不是让整条命令失败。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - 非 tty 场景
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    # ★ 从 .env 加载密钥（键名会回传，**值永不打印**）。
    # 真实环境变量优先，不被文件覆盖 —— 见 `load_dotenv` 的说明。
    from storyforge.config import load_dotenv

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    finally:
        reset_settings()


if __name__ == "__main__":
    raise SystemExit(main())
