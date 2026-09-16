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

load_dotenv()

from storyforge.graph.graphs.books import (  # noqa: E402
    GraphDeps,
    build_bible_graph,
    build_detail_batch_graph,
    build_skeleton_graph,
    build_volume_plan_graph,
    thread_config,
)
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
        ("未提及本卷新地图", "skeleton:new_locations"),
        # ⚠️ 历史规则：`BibleOut` 已改为**自动归位**（不再产生这条消息），
        #    但旧的 `_diag_*.json` 里存着它 —— 保留映射，历史报表仍可复现。
        #    新跑出来的书不可能再落到这一类；若又出现了，说明归位漏了某种形状。
        ("其他字段的内容", "bible:misnest"),
        ("字段名开头", "bible:misnest"),
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


async def run_one(client: LLMClient, store: ArtifactStore, spec: dict,
                  args: argparse.Namespace | None = None) -> BookResult:
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
            r.failures += [_classify(str(i.get("message", "")))
                           for i in (out.get("gate_issues") or [])]
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
    try:
        n = spec["detail_chapters"]
        out = await build_detail_batch_graph(deps).ainvoke(
            {"book_id": bid, "current_volume": 1, "batch_start": 1, "batch_end": n,
             "refs": {"bible_ref": bible_ref}},
            thread_config(f"b{bid}:detail:v1:1-{n}"),
        )
        # ★ 缺陷1修复：空 lint 不等于成功。三条件缺一即失败 ——
        # 首轮 LLM 异常会返回空 lint（`detail_planner.py`），若只看 lint
        # 就会误报 `detail_ok=true` 且 `detail_count=0`（book12/16 的形状）。
        # 磁盘产物以 `detail_refs` 为准（失败分支无此键，`out.get` 天然区分）。
        refs = out.get("detail_refs") or []
        r.detail_ok = (not out.get("lint_issues")
                       and not (out.get("detail_error") or "")
                       and len(refs) == n)
        r.detail_count = len(refs)
        r.health_beats = int(out.get("health_beats_injected") or 0)
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
                "lint_issues": out.get("lint_issues") or [],
                "detail_error": out.get("detail_error") or "",
                "detail_plans": out.get("detail_plans"),
                "detail_refs": refs,
                "expected": n,
                "adjacent_pairs": pairs,
                "transition_rate": (
                    sum(1 for p in pairs if p["b_is_transition"]) / len(pairs)
                    if pairs else None
                ),
            })
        if not r.detail_ok:
            if out.get("detail_error"):
                r.failures.append(f"detail:error:{out['detail_error'][:120]}")
            r.failures += [_classify(i) for i in (out.get("lint_issues") or [])]
            if not r.failures:
                # lint 为空、error 为空、refs 对不上 —— 未知形状，不得静默
                r.failures.append(
                    f"detail:unknown:refs={len(refs)}/{n} "
                    f"plans={out.get('detail_plans')}"
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
