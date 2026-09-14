"""命令行入口 —— 一期的唯一操作接口（决策 D-05：不做前端）。

包含 `doctor` 自检命令：检查配置、数据库、外键、LLM 连通性、
以及**架构不变量**，让"系统是否健康"变成一条命令。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storyforge import __version__
from storyforge.config import REPO_ROOT, Settings, configure, get_settings, reset_settings


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def run_doctor(*, verbose: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    """自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律。"""
    results: list[dict[str, Any]] = []

    # 1) 配置加载
    try:
        s = get_settings()
        s.ensure_dirs()
        results.append(_check("config", True, f"data_dir={s.data_dir}"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("config", False, f"{type(exc).__name__}: {exc}"))
        return False, results

    # 2) 结构闸门必须启用（旧项目 shadow=true 的教训）
    results.append(
        _check(
            "structure_gate_enabled",
            s.structure_gate.enabled,
            "必须 enabled（旧项目 shadow=true 导致 400 章挤第 1 卷）",
        )
    )

    # 3) 数据库 + 外键
    try:
        from storyforge.infra.storage.db import Database

        db = Database.from_settings(s)
        db.create_all()
        fk_on = db.foreign_keys_enabled()
        results.append(_check("database", True, f"{len(db.list_tables())} tables"))
        results.append(
            _check("foreign_keys_forced", fk_on, "SQLite 默认关闭，必须显式开启")
        )
        integrity = db.check_integrity()
        results.append(_check("integrity", integrity == "ok", integrity))

        # 每张从属表必须有 FK
        root_tables = {"books"}
        no_fk = [
            t
            for t in db.list_tables()
            if not t.startswith("sqlite_") and t not in root_tables
            and not db.foreign_key_targets(t)
        ]
        results.append(
            _check("all_tables_have_fk", not no_fk, f"缺 FK: {no_fk}" if no_fk else "ok")
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("database", False, f"{type(exc).__name__}: {exc}"))

    # 4) 能力声明与路由
    try:
        from storyforge.infra.llm.capability import load_registry

        reg = load_registry()
        results.append(
            _check("providers", True, f"{len(reg.models)} models / {len(reg.providers)} providers")
        )
        # 抽取类节点必须「尽量不思考」
        #
        # ⚠️ 实测：小米 MiMo v2.5 **关不掉思考**（enable_thinking/thinking 都被忽略），
        # 因此合格形态有三种：模型本身不思考 / 真能关闭 / 取到最低 effort 档。
        # 这里按「效果」判定，而不是按某个参数名 —— 参数是实现细节。
        bad: list[str] = []
        for node in ("scene_extractor", "entity_resolver", "relation_extractor"):
            try:
                m, _ = reg.route(node)
                policy = reg.thinking_policy_for(node, m)
                if not m.supports_reasoning:
                    continue
                if policy.get("enable_thinking") is False:
                    continue
                if policy.get("reasoning_effort") in ("minimal", "low"):
                    continue
                bad.append(f"{node}->{m.id} policy={policy}")
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{node}: {exc}")
        results.append(
            _check(
                "extractor_thinking_minimized",
                not bad,
                f"违规: {bad}" if bad
                else "抽取类节点均已取最低思考档（旧项目 entity_classify 空输出 100%）",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("providers", False, f"{type(exc).__name__}: {exc}"))

    # 5) API key 是否配置（不打印值）
    try:
        from storyforge.infra.llm.capability import load_registry as _lr

        reg = _lr()
        missing = [
            p.name for p in reg.providers.values()
            if p.api_key_env and not p.resolve_api_key()
        ]
        results.append(
            _check(
                "api_keys",
                True,  # 缺 key 不算失败：Mock 模式与离线测试不需要
                f"未配置: {missing}（Mock/离线模式不受影响）" if missing else "已配置",
            )
        )
    except Exception:  # noqa: BLE001
        pass

    # 6) 分层纪律（AST 快速版）
    src = REPO_ROOT / "src" / "storyforge"
    domain_bad: list[str] = []
    for p in (src / "domain").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "import langgraph" in text or "import langchain" in text:
            domain_bad.append(str(p.relative_to(src)))
    results.append(
        _check("domain_is_framework_free", not domain_bad, f"违规: {domain_bad}" if domain_bad else "ok")
    )

    ok = all(r["ok"] for r in results)
    return ok, results


def cmd_doctor(args: argparse.Namespace) -> int:
    ok, results = run_doctor(verbose=args.verbose)
    width = max(len(r["check"]) for r in results) if results else 10
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
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

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/contract", "-q", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    print("\n".join(tail))
    return proc.returncode


# --------------------------------------------------------------------------- #
# score（打分卡：对真实文本打分）
# --------------------------------------------------------------------------- #


def cmd_score(args: argparse.Namespace) -> int:
    """对目录下的章节文本打分（零 LLM、可复现）。

    用法：
        storyforge score --dir <章节目录> --pattern "v1-*.txt"
        storyforge score --dir <目录> --baseline     # 与旧 baseline 对比
    """
    import statistics
    from collections import Counter
    from pathlib import Path as _Path

    from storyforge.scorecard import evaluate

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
    cards = [
        evaluate(t, global_no=i + 1, prev_texts=texts[max(0, i - window) : i])
        for i, t in enumerate(texts)
    ]

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


def _make_client() -> Any:
    """构造接真实模型的 LLMClient（LangChain 传输层）。

    ★ 这是"基于 langchain"的落地处：`LLMClient` 的 `transport` 走
    `ChatOpenAI`。缺 API key 时**显式报错**（不静默空跑）。
    """
    from storyforge.infra.llm.client import ClientConfig, LLMClient
    from storyforge.infra.llm.transport import make_transport

    s = get_settings()
    return LLMClient(
        make_transport(),
        config=ClientConfig(
            max_attempts=s.llm.max_attempts,
            timeout_s=s.llm.default_timeout_s,
        ),
    )


def _artifacts():
    from storyforge.infra.storage.artifacts import ArtifactStore

    return ArtifactStore.from_settings()


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


async def _run_plan(args: argparse.Namespace) -> int:
    from storyforge.graph.graphs.books import GraphDeps, thread_config
    from storyforge.graph.graphs.books import (
        build_bible_graph,
        build_detail_batch_graph,
        build_skeleton_graph,
        build_volume_plan_graph,
    )

    store = _artifacts()
    client = _make_client()
    deps = GraphDeps(client=client, store=store)
    s = get_settings()

    book_id = args.book_id

    # ── ① 圣经 ──
    print(f"[1/4] 生成世界观圣经（book {book_id}）…")
    out = await build_bible_graph(deps).ainvoke(
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
    out = await build_volume_plan_graph(deps).ainvoke(
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
    out = await build_skeleton_graph(deps).ainvoke(
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
    out = await build_detail_batch_graph(deps).ainvoke(
        {"book_id": book_id, "current_volume": args.volume,
         "batch_start": 1, "batch_end": end,
         "refs": {"bible_ref": bible_ref}},
        thread_config(f"b{book_id}:detail:v{args.volume}:1-{end}"),
    )
    if out.get("lint_issues"):
        print("细纲未通过 lint：", file=sys.stderr)
        for i in out["lint_issues"][:20]:
            print(f"  - {i}", file=sys.stderr)
        return 1
    print(f"      → {len(out['detail_refs'])} 章细纲已落盘，"
          f"注入伤势节拍 {out['health_beats_injected']} 条")
    if out.get("awaiting_confirm"):
        print(f"\n⚠️  到达人工确认点：{out['awaiting_confirm']}")
        print("   请审阅产物后再继续（`storyforge write`）。")

    print("\n规划完成。下一步：")
    print(f"  uv run storyforge write --book-id {book_id} --chapters {end}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    import asyncio

    rc = _guard_api_key()
    if rc is not None:
        return rc
    return asyncio.run(_run_plan(args))


# --------------------------------------------------------------------------- #
# write（单章闭环）
# --------------------------------------------------------------------------- #


async def _run_write(args: argparse.Namespace) -> int:
    from storyforge.graph.graphs.chapter import ChapterDeps, build_chapter_graph
    from storyforge.graph.graphs.books import thread_config

    store = _artifacts()
    client = _make_client()
    deps = ChapterDeps(client=client, store=store)
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
        out = await graph.ainvoke(
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
            print(f"OK  {out.get('prose_chars')} 字"
                  f"{'（已修订）' if out.get('revision_applied') else ''}")
            done += 1
        else:
            print(f"未完成：{status} {out.get('error', '')}")
            if status in ("write_failed", "blocked"):
                break

    print(f"\n完成 {done} 章。")
    if client.records:
        blank = client.blank_rate()
        print(f"LLM 调用 {len(client.records)} 次，空输出率 {blank:.1%}"
              f"（旧项目实测 22.2%）")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    import asyncio

    rc = _guard_api_key()
    if rc is not None:
        return rc
    return asyncio.run(_run_write(args))


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
    pn.set_defaults(func=cmd_plan)

    w = sub.add_parser("write", help="单章闭环：写作 → 打分 → 审校 → 抽取（需 API key）")
    w.add_argument("--book-id", type=int, required=True)
    w.add_argument("--start", type=int, default=1, help="起始章号（默认 1）")
    w.add_argument("--chapters", type=int, default=1, help="写几章（默认 1）")
    w.add_argument("--volume", type=int, default=1)
    w.set_defaults(func=cmd_write)

    rp = sub.add_parser("report", help="Token 与成本报告")
    rp.set_defaults(func=cmd_report)

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
