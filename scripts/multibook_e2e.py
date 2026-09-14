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
    """把失败信息归类 —— 用于判断"是通用缺陷还是单书特例"。"""
    t = text or ""
    for key, label in (
        ("世界观", "bible:worldview"),
        ("圣经过薄", "bible:too_thin"),
        ("硬规则", "bible:rules"),
        ("术语", "bible:terms"),
        ("无法解析", "bible:parse"),
        ("等其他字段", "bible:misnest"),
        ("结构闸门", "volume:gate"),
        ("新势力", "volume:new_factions"),
        ("新地图", "volume:new_locations"),
        ("卷", "volume:other"),
        ("未落实本卷台阶指定的新势力", "skeleton:new_factions"),
        ("未提及本卷新地图", "skeleton:new_locations"),
        ("故事线", "skeleton:arcs"),
        ("对立线", "skeleton:opposition"),
        ("关系线", "skeleton:relationship"),
        ("角色", "skeleton:characters"),
        ("段落", "skeleton:segments"),
        ("场景", "detail:scenes"),
        ("标题", "detail:title"),
        ("缺章", "detail:missing"),
        ("plot", "detail:plot"),
        ("细纲", "detail:lint"),
    ):
        if key in t:
            return label
    return "other"


async def run_one(client: LLMClient, store: ArtifactStore, spec: dict) -> BookResult:
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
        r.detail_ok = not out.get("lint_issues")
        r.detail_count = len(out.get("detail_refs") or [])
        r.health_beats = int(out.get("health_beats_injected") or 0)
        if not r.detail_ok:
            r.failures += [_classify(i) for i in (out.get("lint_issues") or [])]
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


def _mark(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", nargs="*", type=int, default=None)
    ap.add_argument("--out", default="doc/research/multibook-e2e.json")
    ap.add_argument("--clean", action="store_true", help="跑之前清掉这些书的产物")
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
    for spec in specs:
        print(f"\n{'=' * 74}")
        print(f"书 {spec['id']}｜{spec['genre']}｜{spec['book_chapters']} 章"
              f"｜先写 {spec['detail_chapters']} 章")
        print(f"  {spec['topic'][:64]}")
        print("-" * 74)
        try:
            r = await run_one(client, store, spec)
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
