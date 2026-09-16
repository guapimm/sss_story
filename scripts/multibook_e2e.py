"""多书端到端测试 —— 用**不同题材与规模**跑规划链路。

## 为什么必须多书（而不是反复用同一本）

真实运行中我已经把「旧档案馆值夜人」这一本书跑了十几次来迭代校验器。
这有明确的**过拟合风险**：针对某一本书的措辞去放宽匹配阈值，
换一本书就可能失效，而我看不出来。

因此本脚本用**互相差异极大**的题材与规模跑同一套链路：

    id  题材              全书章数   卷数预期   特点
    11  suspense 规则怪谈    400       4       现代都市、规则体系（已充分测过，作为基线）
    12  xianxia 仙侠修真     800       4       古典、境界体系、势力众多
    13  romance 都市言情     200       2~3     无战斗、关系驱动、现代职业
    14  scifi 硬科幻         600       3       太空、科技、非人类实体
    15  history 历史架空     300       2       朝代、官职、战争
    16  mystery 现代悬疑     150       1~2     小规模、单线

**判定标准**：如果某个失败在 **≥3 本书**上出现 → 通用缺陷，必须修在链路里；
只在 1 本书上出现 → 可能是该书的措辞特例，先记录不急着放宽校验器。

用法：
    uv run python scripts/multibook_e2e.py            # 全部
    uv run python scripts/multibook_e2e.py --books 12 13
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storyforge.config import get_settings, load_dotenv  # noqa: E402
from storyforge.domain.enums import GateErrorCode  # noqa: E402

load_dotenv()

from storyforge.graph.graphs.books import (  # noqa: E402
    GraphDeps,
    build_bible_graph,
    build_detail_batch_graph,
    build_skeleton_graph,
    build_volume_plan_graph,
    thread_config,
)
from storyforge.infra.llm.capability import ProviderConfigError  # noqa: E402
from storyforge.infra.llm.client import ClientConfig, LLMClient  # noqa: E402
from storyforge.infra.llm.transport import make_transport  # noqa: E402
from storyforge.infra.storage.artifacts import ArtifactStore  # noqa: E402

#: 测试用书：题材差异极大，规模各异
BOOKS: list[dict] = [
    {
        "id": 11, "genre": "suspense", "book_chapters": 400, "detail_chapters": 20,
        "topic": "规则怪谈：旧档案馆的值夜人，规则写在手册上，触犯者会被遗忘",
    },
    {
        "id": 12, "genre": "xianxia", "book_chapters": 800, "detail_chapters": 20,
        "topic": "仙侠修真：一个灵根尽毁的杂役弟子，靠解读残破功法在宗门夹缝中一步步登顶",
    },
    {
        "id": 13, "genre": "romance", "book_chapters": 200, "detail_chapters": 20,
        "topic": "都市言情：离婚律师与急诊科医生，两个不相信长期关系的人互相试探",
    },
    {
        "id": 14, "genre": "scifi", "book_chapters": 600, "detail_chapters": 20,
        "topic": "硬科幻：深空补给站的最后一名维护员，发现站内 AI 在伪造地球的通讯",
    },
    {
        "id": 15, "genre": "history", "book_chapters": 300, "detail_chapters": 20,
        "topic": "历史架空：边关小城的文书吏，在两国议和的前夜发现军报被人调换",
    },
    {
        "id": 16, "genre": "mystery", "book_chapters": 150, "detail_chapters": 15,
        "topic": "现代悬疑：一桩十二年前的旧案重开，唯一的目击者如今是负责此案的警官",
    },
]


@dataclass
class BookResult:
    book_id: int
    genre: str
    topic: str
    bible_ok: bool = False
    bible_calls: int = 0
    volume_ok: bool = False
    volumes: int = 0
    repair_rounds: int = 0
    skeleton_ok: bool = False
    detail_ok: bool = False
    detail_count: int = 0
    health_beats: int = 0
    seconds: float = 0.0
    #: 各阶段的失败原因（分类用）
    failures: list[str] = field(default_factory=list)


def _classify(text: str) -> str:
    """把失败信息归类 —— 用于判断"是通用缺陷还是单书特例"。

    ## ★ 排序即语义：广匹配必须排在窄匹配之后

    原先的实现在这里有个**真实 bug**（已修）：`("卷", "volume:other")` 这个
    **极宽**的匹配排在骨架的两条之前，而骨架消息里恰好含「本**卷**」二字：

        "骨架未落实本卷台阶指定的新势力：…"
        "骨架的故事线/段落未提及本卷新地图：…"

    → 两条骨架分支永远命中不到（**死代码**），书 13/15 的骨架失败被误标成
      `volume:other`。多书测试的整个价值就是"按类别数书数"，分类错了，
      `D-16` 的「≥3 本 = 通用缺陷」判据就跟着错。

    ## 修法：按**特异度降序**排列，而不是靠人肉维护顺序

    规则：
      1. 长的、具体的短语在前（`未提及本卷新地图` 比 `卷` 具体得多）
      2. 单字/双字宽匹配（`卷`、`角色`、`标题`…）一律垫底
      3. 每条匹配附一个 `specificity`（关键词长度），自动降序排 ——
         这样**将来加新规则不需要人工判断该插在哪一行**

    另外：`volume:other` 保留但改名为 `volume:unclassified`，
    让"没被细分类"这件事在报表里**看得见**，而不是伪装成一个具体类别。
    """
    t = text or ""
    # (关键词, 标签) —— 顺序无关，下面按关键词长度自动降序
    rules: tuple[tuple[str, str], ...] = (
        # ── 窄匹配（长短语，指哪打哪）──
        ("未落实本卷台阶指定的新势力", "skeleton:new_factions"),
        # ★ 现行措辞（`places` 字段引入后）
        ("骨架未落实本卷新地图", "skeleton:new_locations"),
        # ⚠️ 历史措辞：旧诊断 json 里存着它 —— 保留映射，历史报表仍可复现
        ("未提及本卷新地图", "skeleton:new_locations"),
        ("未提及本卷新地图", "skeleton:new_locations"),
        # ⚠️ 历史规则：`BibleOut` 已改为**自动归位**（不再产生这条消息），
        #    但旧的 `_diag_*.json` 里存着它 —— 保留映射，历史报表仍可复现。
        #    新跑出来的书不可能再落到这一类；若又出现了，说明归位漏了某种形状。
        ("其他字段的内容", "bible:misnest"),
        ("字段名开头", "bible:misnest"),
        # ★ 服务端故障必须与代码缺陷**分开**报。
        #
        # 实测：provider 的 `/chat/completions` 整体 500（而 `GET /models` 仍 200），
        # 于是每一轮 e2e 在**不同阶段**失败（圣经/台阶/骨架/细纲都出现过）。
        # 若把它归进 `bible:parse` / `volume:unclassified` 这类代码缺陷类别，
        # 就会误导人去"修"一个没坏的东西 —— 本轮已多次踩到
        # "看到的失败不是真失败"这一类问题。
        #
        # 用裸 httpx 直连确认过：与我们的 transport / 代码无关
        # （脚本 `scripts/diag_provider_raw.py`）。
        ("Internal Server Error", "provider:5xx"),
        ("Error code: 500", "provider:5xx"),
        ("Error code: 502", "provider:5xx"),
        ("Error code: 503", "provider:5xx"),
        ("Error code: 504", "provider:5xx"),
        ("Connection error", "provider:network"),
        ("APIConnectionError", "provider:network"),
        ("圣经过薄", "bible:too_thin"),
        ("无法解析", "bible:parse"),
        ("结构闸门", "volume:gate"),
        # ── 中等 ──
        ("故事线", "skeleton:arcs"),
        ("对立线", "skeleton:opposition"),
        ("关系线", "skeleton:relationship"),
        ("段落目标", "skeleton:segments"),
        ("新势力", "volume:new_factions"),
        ("新地图", "volume:new_locations"),
        ("世界观", "bible:worldview"),
        ("硬规则", "bible:rules"),
        ("术语", "bible:terms"),
        # ── 细纲 ──
        #
        # ⚠️ 实测发现：细纲的失败消息里**常常没有「细纲」二字** ——
        #    它们是「地点跳变…」「本批应输出 15 章…」「场景数 3 < 4」这类
        #    具体描述。原先只靠 ("细纲", "detail:lint") 匹配，整类都掉进
        #    `other`，等于细纲的失败原因完全没有分类。
        #    因此这里按**消息的真实措辞**登记关键词，而不是靠类别名。
        ("地点跳变", "detail:continuity"),
        ("场景数", "detail:scenes"),
        ("缺章", "detail:missing"),
        ("应输出", "detail:batch_size"),
        ("逐字重复", "detail:title_dup"),
        ("不可完整", "detail:scene_incomplete"),
        ("没有交代时间", "detail:continuity"),
        # ── 宽匹配（垫底：单/双字，容易误吞）──
        ("细纲", "detail:lint"),
        ("角色", "skeleton:characters"),
        ("标题", "detail:title"),
        ("plot", "detail:plot"),
        ("卷", "volume:unclassified"),
    )
    # 按关键词长度降序：越长越具体，优先命中
    for key, label in sorted(rules, key=lambda kv: -len(kv[0])):
        if key in t:
            return label
    return "other"


#: 台阶闸门 `code` → 报表标签。
#:
#: ★ 为什么不靠 `message` 的关键词猜，也不手写映射表：
#:
#: `GateErrorCode` 枚举**已经是权威来源**（闸门自己用的就是它）。
#: 本轮实测踩到：只传 `message` 去猜 → `milestones` 闸门
#: （消息「第 2 卷存在 49 章的里程碑真空」）没有对应关键词
#: → 落进 `volume:unclassified`，等于该闸门**没有分类**。
#:
#: 这与本轮另外两处错误同源（`_classify` 排序、e2e 绕过配置）：
#: **放着结构化事实不用，改用文本启发式**。
#: 因此这里直接从枚举生成，**枚举加值自动覆盖**，不可能漂移。
_GATE_CODE_LABELS: dict[str, str] = {
    code.value: f"volume:{code.value}" for code in GateErrorCode
}


def _classify_gate(issue: dict) -> str:
    """把一条 `gate_issue` 归类 —— **优先用 `code`**，措辞只作回退。

    `code` 是闸门自己给的权威分类（`GateErrorCode` 枚举）；
    `message` 是给人看的措辞，靠它做机器分类会在"换了说法"时静默失效。
    """
    code = str(issue.get("code") or "").strip()
    if code in _GATE_CODE_LABELS:
        return _GATE_CODE_LABELS[code]
    if code:
        # 出现了未登记的闸门码 —— 显式暴露，**不要**伪装成已分类
        return f"volume:code:{code}"
    return _classify(str(issue.get("message", "")))


async def run_one(
    client: LLMClient, store: ArtifactStore, spec: dict,
    args: argparse.Namespace | None = None,
) -> BookResult:
    diag = bool(getattr(args, "diag", False))
    deps = GraphDeps(client=client, store=store)
    r = BookResult(book_id=spec["id"], genre=spec["genre"], topic=spec["topic"])
    t0 = time.monotonic()
    bid = spec["id"]

    # ── ① 圣经 ──
    try:
        out = await build_bible_graph(deps).ainvoke(
            {"book_id": bid, "topic": spec["topic"], "genre": spec["genre"], "refs": {}},
            thread_config(f"b{bid}:bible"),
        )
        r.bible_ok = bool(out.get("ok"))
        if not r.bible_ok:
            # ★ 圣经失败必须落**原始 violations**（含完整异常文本）。
            #   否则只看到分类标签（如 `bible:parse`），不知道到底哪一步坏了 ——
            #   本轮实测：`bible_calls=0` + `bible:parse` 无法区分
            #   "模型给了坏 JSON" 还是 "provider 调用就失败了"。
            if diag:
                _write_diag(f"doc/research/_diag_bible_{bid}.json", {
                    "book_id": bid,
                    "ok": False,
                    "violations": out.get("violations") or [],
                    "error": out.get("error") or "",
                    "llm_calls": out.get("llm_calls"),
                })
            r.failures += [_classify(v) for v in (out.get("violations") or ["?"])]
            r.seconds = time.monotonic() - t0
            return r
    except Exception as exc:  # noqa: BLE001
        r.failures.append(f"bible:exception:{type(exc).__name__}")
        r.seconds = time.monotonic() - t0
        return r

    bible_ref = out["refs"]["bible_ref"]

    # ── ② 全卷台阶 ──
    try:
        out = await build_volume_plan_graph(deps).ainvoke(
            {"book_id": bid, "topic": spec["topic"], "genre": spec["genre"],
             "target_chapters": spec["book_chapters"],
             "refs": {"bible_ref": bible_ref}},
            thread_config(f"b{bid}:volume_plan"),
        )
        r.volume_ok = bool(out.get("gate_ok"))
        r.volumes = int(out.get("total_volumes") or 0)
        r.repair_rounds = int(out.get("repair_round") or 0)
        # ★ A-3 诊断（只加诊断不动逻辑）：落原始 gate_issues 全量（含 code）。
        # `_classify` 只看 message 子串，会把 7 种闸门码塌缩成 `volume:other`。
        if diag:
            _write_diag(f"doc/research/_diag_volume_{bid}.json", {
                "book_id": bid,
                "gate_ok": r.volume_ok,
                "repair_rounds": r.repair_rounds,
                "gate_issues": out.get("gate_issues") or [],
            })
        if not r.volume_ok:
            # ★ 优先用结构化的 `code`，而不是去猜 message 的措辞。
            #
            # ## 实测踩到（第三次同类问题）
            #
            # 原先只传 `message`，而 `_classify` 是靠关键词猜的 ——
            # 于是 `milestones` 闸门（消息是「第 2 卷存在 49 章的里程碑真空」）
            # 因为没有对应关键词而落进 `volume:unclassified`。
            #
            # 但闸门本来就**带着权威的分类字段** `code`（`GateIssue.code`），
            # 有结构化字段却去猜文本，等于自找分类错误。
            # （同一类错误：`_classify` 排序问题、e2e 绕过配置 —— 都是
            #   "放着结构化事实不用，改用文本启发式"。）
            r.failures += [_classify_gate(i) for i in (out.get("gate_issues") or [])]
            r.seconds = time.monotonic() - t0
            return r
    except Exception as exc:  # noqa: BLE001
        r.failures.append(f"volume:exception:{type(exc).__name__}")
        r.seconds = time.monotonic() - t0
        return r

    # ── ③ 骨架 ──
    try:
        out = await build_skeleton_graph(deps).ainvoke(
            {"book_id": bid, "current_volume": 1, "batch_no": 1,
             "refs": {"bible_ref": bible_ref}},
            thread_config(f"b{bid}:skeleton:v1"),
        )
        r.skeleton_ok = not out.get("violations")
        # ★ A-3 诊断（只加诊断不动逻辑）：落骨架 violations 全量 + 当轮骨架原文
        # 摘要，供判"真缺失 vs 措辞未命中"（`_mentions` 容错已两次放宽，D-16：
        # 2 本=待观察，不得再直接调阈值）。
        if diag:
            # ★ 优先用**当轮**未通过的骨架（失败时单独落盘的那个），
            #   读 `skeleton_ref` 在失败时只会读到上一轮的旧产物 ——
            #   那会让"真缺失 vs 措辞未命中"的判断整体失效（实测踩到）。
            sk_text = ""
            sk_src = "none"
            fail_ref = out.get("last_skeleton_ref") or ""
            if fail_ref:
                try:
                    sk_text = store.read_text(fail_ref)[:4000]
                    sk_src = "debug(当轮)"
                except Exception as exc:  # noqa: BLE001
                    sk_text = f"<读当轮骨架失败：{exc}>"
                    sk_src = "debug-error"
            if not sk_text or sk_src != "debug(当轮)":
                try:
                    from storyforge.graph.nodes.skeleton_planner import load_skeleton
                    sk = load_skeleton(store, bid, 1)
                    sk_text = sk.model_dump_json()[:4000]
                    sk_src = "artifact(读盘·可能是旧的)"
                except Exception as exc:  # noqa: BLE001
                    if not sk_text:
                        sk_text = f"<无骨架产物：{exc}>"
                        sk_src = "none"
            _write_diag(f"doc/research/_diag_skeleton_{bid}.json", {
                "book_id": bid,
                "skeleton_ok": r.skeleton_ok,
                "skeleton_source": sk_src,
                "violations": out.get("violations") or [],
                "skeleton_excerpt": sk_text,
            })
        if not r.skeleton_ok:
            r.failures += [_classify(v) for v in (out.get("violations") or [])]
            r.seconds = time.monotonic() - t0
            return r
    except Exception as exc:  # noqa: BLE001
        r.failures.append(f"skeleton:exception:{type(exc).__name__}")
        r.seconds = time.monotonic() - t0
        return r

    # ── ④ 细纲 ──
    #
    # ★ 必须按 `pipeline.detail_batch_size` **分批**，与 CLI 同口径。
    #
    # ## 实测踩到的口径漂移（本脚本一度比生产更严，反而制造了假失败）
    #
    # 原先这里直接 `batch_end = n`（15 章一批），**绕过了配置**。
    # 而 CLI 用的是 `min(chapters, detail_batch_size)`（5 章一批）。
    # 后果：配置从 15 改成 5 之后，e2e **仍然超时**，看起来像"改了没用" ——
    # 实际是 e2e 压根没读那个配置。
    #
    # 15 章一批实测需要 ~328s > 300s 超时上限（`scripts/diag_batch_model.py`），
    # 所以这里的失败是**脚本自造的**，不是链路的缺陷。
    try:
        n = spec["detail_chapters"]
        batch_size = get_settings().pipeline.detail_batch_size
        refs: list[str] = []
        lint: list[str] = []
        detail_error = ""
        beats = 0
        detail_plans = 0
        for start in range(1, n + 1, batch_size):
            end = min(start + batch_size - 1, n)
            out = await build_detail_batch_graph(deps).ainvoke(
                {"book_id": bid, "current_volume": 1,
                 "batch_start": start, "batch_end": end,
                 "refs": {"bible_ref": bible_ref}},
                thread_config(f"b{bid}:detail:v1:{start}-{end}"),
            )
            # ★ 缺陷1修复：空 lint 不等于成功。三条件缺一即失败 ——
            # 首轮 LLM 异常会返回空 lint（`detail_planner.py`），若只看 lint
            # 就会误报 `detail_ok=true` 且 `detail_count=0`（book12/16 的形状）。
            batch_refs = out.get("detail_refs") or []
            refs.extend(batch_refs)
            detail_plans += int(out.get("detail_plans") or 0)
            beats += int(out.get("health_beats_injected") or 0)
            if out.get("lint_issues"):
                lint += list(out["lint_issues"])
            if out.get("detail_error"):
                detail_error = str(out["detail_error"])
                break  # 一批失败即停（后续批次依赖前面，继续没意义）
            if len(batch_refs) != (end - start + 1):
                detail_error = (
                    f"批次 {start}~{end} 产物数不符："
                    f"{len(batch_refs)}/{end - start + 1}"
                )
                break

        r.detail_ok = not lint and not detail_error and len(refs) == n
        r.detail_count = len(refs)
        r.health_beats = beats
        # ★ A-2 诊断（只加诊断不动逻辑）：落原始 lint 全量，供修连续性前看数据。
        # D-16：先看是几本书的共性，再定放宽还是收紧。
        if diag:
            from storyforge.graph.nodes.detail_planner import load_plan
            pairs = []
            for no in range(1, n):
                try:
                    a = load_plan(store, bid, no)
                    b = load_plan(store, bid, no + 1)
                except Exception:
                    break
                pairs.append({
                    "a": no, "b": no + 1,
                    "exit_location": a.exit_state.location_name,
                    "entry_location": b.entry_state.location_name,
                    "b_is_transition": b.is_transition,
                    "shared_tokens": _shared_tokens(
                        a.exit_state.location_name, b.entry_state.location_name),
                })
            _write_diag(f"doc/research/_diag_detail_{bid}.json", {
                "book_id": bid,
                "lint_issues": lint,
                "detail_error": detail_error,
                "detail_plans": detail_plans,
                "detail_refs": refs,
                "expected": n,
                "batch_size": batch_size,
                "adjacent_pairs": pairs,
                "transition_rate": (
                    sum(1 for p in pairs if p["b_is_transition"]) / len(pairs)
                    if pairs else None
                ),
            })
        if not r.detail_ok:
            if detail_error:
                r.failures.append(f"detail:error:{detail_error[:120]}")
            r.failures += [_classify(i) for i in lint]
            if not r.failures:
                # lint 为空、error 为空、refs 对不上 —— 未知形状，不得静默
                r.failures.append(
                    f"detail:unknown:refs={len(refs)}/{n} plans={detail_plans}"
                )
    except Exception as exc:  # noqa: BLE001
        r.failures.append(f"detail:exception:{type(exc).__name__}")

    r.seconds = time.monotonic() - t0
    return r


def _write_results(results: list[BookResult], out_path: str) -> None:
    """落盘（每本书跑完就写一次，防中途崩掉丢失全部数据）。"""
    all_failures: dict[str, list[int]] = {}
    for r in results:
        for f in r.failures:
            all_failures.setdefault(f, []).append(r.book_id)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"results": [asdict(r) for r in results],
                    "failures": all_failures},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _write_diag(out_path: str, payload: dict) -> None:
    """写诊断文件（原始 issues / 产物原文，供修校验器前看数据用，D-16）。

    ★ 只在诊断跑时调用，不进正式结果 json。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )


def _shared_tokens(a: str, b: str) -> list[str]:
    """两串按字符切分的共享 Token（A-2 诊断用：看假阳性是否有公共子串）。"""
    sa = {c for c in (a or "") if c.strip()}
    sb = {c for c in (b or "") if c.strip()}
    return sorted(sa & sb)


def _mark(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


async def _provider_alive(client: LLMClient) -> tuple[bool, str]:
    """探测 provider 是否可用（发一次最小请求）。

    返回 `(是否可用, 说明)`。**不重试** —— 目的是尽快判断"现在能不能测"，
    而不是"能不能熬过去"（后者由真实调用自己的重试负责）。
    """
    try:
        res = await client.complete(
            node="scene_extractor",          # 抽取类：最省，且已在 toml 声明
            system="你是助手。",
            user="回复：OK",
        )
        text = (res.text or "").strip()[:30]
        if not text:
            return False, "provider 返回了**空输出**（没有任何正文）"
        return True, f"provider 正常（返回 {text!r}）"
    except ProviderConfigError as exc:
        return False, f"节点未在 providers.toml 声明：{exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:220]}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", nargs="*", type=int, default=None)
    ap.add_argument("--out", default="doc/research/multibook-e2e.json")
    ap.add_argument("--clean", action="store_true", help="跑之前清掉这些书的产物")
    ap.add_argument("--diag", action="store_true",
                    help="落诊断文件 doc/research/_diag_{volume,skeleton,detail}_{bid}.json"
                         "（原始 issues / 产物原文，供修校验器前看数据，D-16）")
    args = ap.parse_args()

    specs = [b for b in BOOKS if args.books is None or b["id"] in args.books]
    settings = get_settings()
    store = ArtifactStore.from_settings(settings)

    if args.clean:
        for b in specs:
            d = store.root / "books" / str(b["id"])
            if d.exists():
                shutil.rmtree(d)

    client = LLMClient(make_transport(), config=ClientConfig(
        max_attempts=settings.llm.max_attempts,
        timeout_s=settings.llm.default_timeout_s,
    ))

    # ── ★ 前置健康检查 ──
    #
    # ## 为什么必须有这一步（实测的严重教训）
    #
    # provider 的 `/chat/completions` 整体 500 时，每一轮 e2e 会在
    # **不同阶段**失败（圣经 / 台阶 / 骨架 / 细纲都出现过）。
    # 结果看起来像"链路到处是 bug"，实际全是服务端故障 ——
    # 很容易误导人去修没坏的东西。
    #
    # 健康的 provider 不该被这种事污染结论。因此**先探一次**：
    #   · 通 → 正常跑
    #   · 不通 → 明确报"provider 不可用"，**不出结论**（也不写结果文件）
    #
    # ⚠️ 用 `scene_extractor`（抽取类，最省）—— 节点必须已在
    #    providers.toml 声明（D-09 禁止硬编码模型）。
    health_ok, health_msg = await _provider_alive(client)
    if not health_ok:
        print("\n" + "!" * 74)
        print("provider 不可用，**本次不产出任何结论**")
        print("!" * 74)
        print(f"  {health_msg}")
        print()
        print("  这不是代码缺陷 —— 请先用下列脚本确认，再等恢复后重跑：")
        print("    uv run python scripts/diag_provider_health.py   # 采样成功率")
        print("    uv run python scripts/diag_provider_raw.py      # 裸 httpx 直连")
        print()
        print("  若在 provider 故障期间跑出失败报表，会把**服务端故障**"
              "误记成链路缺陷。")
        return 2

    results: list[BookResult] = []
    specs = list(specs)
    for idx, spec in enumerate(specs):
        print(f"\n{'=' * 74}")
        print(f"书 {spec['id']}｜{spec['genre']}｜{spec['book_chapters']} 章"
              f"｜先写 {spec['detail_chapters']} 章")
        print(f"  {spec['topic'][:64]}")
        print("-" * 74)
        try:
            r = await run_one(client, store, spec, args)
        except Exception as exc:  # noqa: BLE001
            # ★ 单本书的异常绝不能终止整轮 ——
            # 否则第一本崩了，后面五本的数据全都拿不到，
            # "跨书共性"就无从判断（多书测试的全部意义所在）。
            r = BookResult(
                book_id=spec["id"], genre=spec["genre"], topic=spec["topic"],
                failures=[f"fatal:{type(exc).__name__}:{str(exc)[:120]}"],
            )
        results.append(r)
        print(f"  圣经   {_mark(r.bible_ok)}")
        print(f"  台阶   {_mark(r.volume_ok)}  "
              f"{r.volumes} 卷 / 修复 {r.repair_rounds} 轮")
        print(f"  骨架   {_mark(r.skeleton_ok)}")
        print(f"  细纲   {_mark(r.detail_ok)}  {r.detail_count} 章 / "
              f"伤势节拍 {r.health_beats} 条")
        print(f"  耗时   {r.seconds:.0f}s")
        if r.failures:
            print(f"  失败   {r.failures[:4]}")
        # 每本都落盘一次，中途崩了也留得住已完成的部分
        try:
            _write_results(results, args.out)
        except OSError:
            pass

    # ── 汇总：哪些失败是**跨书共性** ──
    print(f"\n{'=' * 74}")
    print("汇总")
    print("-" * 74)
    print(f"{'书':>4} {'题材':<10} {'圣经':<6} {'台阶':<6} {'骨架':<6} "
          f"{'细纲':<6} {'卷数':>4} {'章数':>4} {'秒':>5}")
    for r in results:
        print(f"{r.book_id:>4} {r.genre:<10} {_mark(r.bible_ok):<6} "
              f"{_mark(r.volume_ok):<6} {_mark(r.skeleton_ok):<6} "
              f"{_mark(r.detail_ok):<6} {r.volumes:>4} {r.detail_count:>4} "
              f"{r.seconds:>5.0f}")

    all_failures: dict[str, list[int]] = {}
    for r in results:
        for f in r.failures:
            all_failures.setdefault(f, []).append(r.book_id)

    if all_failures:
        print("\n失败分类（出现书数 → 判定）：")
        for kind, bids in sorted(all_failures.items(),
                                 key=lambda kv: -len(kv[1])):
            verdict = ("★ 通用缺陷（必须修链路）" if len(bids) >= 3
                       else "单书特例（先记录）" if len(bids) == 1
                       else "待观察")
            print(f"  {kind:<34} {len(bids)} 本 {bids}  {verdict}")
    else:
        print("\n全部通过 —— 无失败分类")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_results(results, args.out)
    print(f"\n明细已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
