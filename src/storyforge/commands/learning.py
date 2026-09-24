"""learn 侧的学习地图命令 —— 生成 / 查看 / **逐条确认** / 编辑 / 回退。

## 这是 2-3 的核心交付

用户要求 learn「全流程**可视化**，避免黑盒」，且确认粒度必须
「能**编辑 + 回退**」（用户明确）。本模块把 L-1/L-2 两个确认点
做成可操作的 CLI：

    learn-map gen      从研究结果生成学习地图（知识点 + 平替修辞）
    learn-map show     查看当前地图（含确认状态）
    learn-map accept   确认（L-1 知识点 / L-2 平替修辞）
    learn-map edit     逐条改（**术语 → 说法**，用户决策的粒度）
    learn-map reject   要求重做某条（记 revision，可单独重生成）
    learn-map rollback 回退到历史第 N 版（用户明确要求"能回退"）
    learn-map history  列出历史版本

## 版本化设计（"回退"的实现）

每次**改动前**先把当前地图存一份 `history/map_NNN.json`。
于是回退是"读回第 N 版"，而不是"凭记忆重做"——
后者在旧项目里表现为"改坏了只能重跑整条链路"。

## 确认状态的持久化

`accepted` 存在 `map.json` 里（不是内存态）。原因：用户确认可能
**跨越会话**（今天看知识点，明天再定说法），内存态会丢。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from storyforge.commands.learning_gen import (
    gen_with_llm,
    has_api_key,
    points_from_snippets,
)
from storyforge.domain.learning import (
    KnowledgePoint,
    LearningMap,
    TermMapping,
    validate_map,
)
from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore

#: 一次最多列出的条目数（防刷屏）
_MAX_LIST = 200


# --------------------------------------------------------------------------- #
# 读写（版本化）
# --------------------------------------------------------------------------- #


def _store() -> ArtifactStore:
    return ArtifactStore.from_settings()


def _load_map(store: ArtifactStore, book_id: int) -> LearningMap:
    ref = ArtifactStore.learning_map_key(book_id)
    try:
        raw = json.loads(store.read_text(ref))
    except ArtifactNotFound as exc:
        raise SystemExit(
            f"书 {book_id} 还没有学习地图 —— 先运行 "
            f"`storyforge learn-map gen --book-id {book_id} --topic <主题>`"
        ) from exc
    return _from_dict(raw)


def _from_dict(raw: dict) -> LearningMap:
    return LearningMap(
        topic=str(raw.get("topic", "")),
        scale=str(raw.get("scale", "standard")),
        points=[
            KnowledgePoint(
                point_id=str(p.get("point_id", "")),
                name=str(p.get("name", "")),
                summary=str(p.get("summary", "")),
                topic=str(p.get("topic", "")),
                difficulty=int(p.get("difficulty", 1)),
                source_urls=tuple(p.get("source_urls") or ()),
                evidence=str(p.get("evidence", "")),
            )
            for p in (raw.get("points") or [])
            if isinstance(p, dict)
        ],
        mappings=[
            TermMapping(
                term=str(m.get("term", "")),
                replacement=str(m.get("replacement", "")),
                rationale=str(m.get("rationale", "")),
                accepted=bool(m.get("accepted", False)),
                revision=int(m.get("revision", 0)),
            )
            for m in (raw.get("mappings") or [])
            if isinstance(m, dict)
        ],
        research_shortfall=str(raw.get("research_shortfall", "")),
    )


def _save_map(store: ArtifactStore, book_id: int, m: LearningMap, *,
              version_up: bool = True) -> str:
    """保存地图；`version_up=True` 时**先存档当前版本**（回退的基础）。

    返回存档的版本 ref（未存档时返回空串）。
    """
    archived = ""
    if version_up:
        archived = _archive(store, book_id, m).key
    store.write_json(ArtifactStore.learning_map_key(book_id), m.to_dict())
    return archived


def _archive(store: ArtifactStore, book_id: int, m: LearningMap) -> object:
    """把**当前盘上的版本**存进 history（改动前的快照）。"""
    n = _next_revision(store, book_id)
    return store.write_json(
        ArtifactStore.learning_map_backup_key(book_id, n), m.to_dict()
    )


def _next_revision(store: ArtifactStore, book_id: int) -> int:
    existing = [
        k for k in store.list_keys(f"books/{book_id}/learning/history/")
        if k.endswith(".json")
    ]
    nums: list[int] = []
    for k in existing:
        stem = Path(k).stem            # map_003
        _, _, tail = stem.partition("_")
        if tail.isdigit():
            nums.append(int(tail))
    return (max(nums) + 1) if nums else 1


def _list_revisions(store: ArtifactStore, book_id: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for k in sorted(store.list_keys(f"books/{book_id}/learning/history/")):
        if not k.endswith(".json"):
            continue
        _, _, tail = Path(k).stem.partition("_")
        if tail.isdigit():
            out.append((int(tail), k))
    return sorted(out)


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #


def cmd_learn_map(args: argparse.Namespace) -> int:
    """`learn-map` 的子命令分发。"""
    action = getattr(args, "action", None)
    handlers = {
        "gen": _do_gen,
        "show": _do_show,
        "accept": _do_accept,
        "edit": _do_edit,
        "reject": _do_reject,
        "rollback": _do_rollback,
        "history": _do_history,
        # 阶段 2-4：教学账本（艾宾浩斯）
        "ledger": _do_ledger,
        "teach": _do_teach,
        "due": _do_due,
    }
    if action not in handlers:
        print("需要子命令：gen/show/accept/edit/reject/rollback/history/"
              "ledger/teach/due", file=sys.stderr)
        return 2
    return handlers[action](args)


# --------------------------------------------------------------------------- #
# 教学账本（阶段 2-4）—— 艾宾浩斯复习调度
# --------------------------------------------------------------------------- #


def _load_ledger(store: ArtifactStore, book_id: int):
    """读账本；不存在则返回空账本（首次使用**不是错误**）。"""
    from storyforge.domain.teaching import DEFAULT_INTERVALS, TeachingLedger

    ref = ArtifactStore.teaching_ledger_key(book_id)
    if not store.exists(ref):
        return TeachingLedger(intervals=DEFAULT_INTERVALS)
    try:
        raw = json.loads(store.read_text(ref))
    except (ArtifactNotFound, ValueError):
        return TeachingLedger(intervals=DEFAULT_INTERVALS)
    return TeachingLedger.from_dict(raw)


def _save_ledger(store: ArtifactStore, book_id: int, ledger) -> None:
    store.write_json(ArtifactStore.teaching_ledger_key(book_id), ledger.to_dict())


def _do_ledger(args: argparse.Namespace) -> int:
    """查看教学账本（**可视化**：三态计数 + 逐点进度）。"""
    store = _store()
    ledger = _load_ledger(store, args.book_id)

    print("=" * 78)
    print(f"教学账本 · 书 {args.book_id}（艾宾浩斯复习调度）")
    print("=" * 78)
    print(f"复习间隔（章）：{list(ledger.intervals)}"
          f"    走完全部间隔 = 毕业")
    st = ledger.stats()
    print()
    print(f"知识点总数 {st['total']}")
    print(f"  未讲授  {st['未讲授']:>3}")
    print(f"  已讲授  {st['已讲授']:>3}（在复习周期内）")
    print(f"  已复用  {st['已复用']:>3}（**已毕业**，不再复习）")
    print(f"  累计顺延 {st['deferred_total']}")

    if args.verbose and ledger.records:
        print()
        print("── 逐点进度 ──")
        for pid, r in sorted(ledger.records.items(),
                             key=lambda kv: kv[1].next_review_chapter or 10**9):
            nxt = r.next_review_chapter if r.next_review_chapter is not None else "—"
            print(f"  {pid:<12} {r.status.value:<4} "
                  f"讲授@{r.taught_chapter:<4} 复习{r.review_count}次 "
                  f"下次@{nxt} 顺延{r.deferred_count}")
    else:
        print()
        print("逐点进度：`learn-map ledger --book-id N -v`")
    return 0


def _do_teach(args: argparse.Namespace) -> int:
    """记录"本章讲授了哪些知识点" → 排第一次复习。

    ★ 这是账本的**写入入口**：真实链路里由 write 节点调用，
    此处暴露给 CLI 以便**可视化验证**（用户要求"避免黑盒"）。
    """
    store = _store()
    ledger = _load_ledger(store, args.book_id)
    if not args.point_ids:
        print("需要 --point-ids（要记入讲授的知识点 id）", file=sys.stderr)
        return 2
    for pid in args.point_ids:
        ledger.record_taught(pid, args.chapter)
    _save_ledger(store, args.book_id, ledger)
    print(f"✓ 第 {args.chapter} 章讲授 {len(args.point_ids)} 个知识点")
    for pid in args.point_ids:
        r = ledger.get(pid)
        print(f"    {pid:<12} 下次复习 @第 {r.next_review_chapter} 章")
    return 0


def _do_due(args: argparse.Namespace) -> int:
    """查某章应复习哪些知识点（**到期挑选的三条规则都生效**）。"""
    from storyforge.domain.teaching import REVIEW_EXCLUDED_BEATS

    store = _store()
    ledger = _load_ledger(store, args.book_id)
    picked = ledger.due_reviews(
        args.chapter, beat=args.beat, has_new_points=args.has_new_points
    )
    if args.beat.lower() in REVIEW_EXCLUDED_BEATS:
        print(f"第 {args.chapter} 章是「{args.beat}」章 —— "
              f"**不注入复习**（避免打断场面；移植自旧项目的设计）")
        return 0
    if not picked:
        print(f"第 {args.chapter} 章无到期复习点")
        return 0
    print(f"第 {args.chapter} 章应复习 {len(picked)} 个"
          f"（最紧急优先；有新知识点时上限 1 个）：")
    for r in picked:
        print(f"    {r.point_id:<12} 第 {r.review_count + 1} 次复习"
              f"（原排期 @第 {r.next_review_chapter} 章）")
    return 0


# -- gen --------------------------------------------------------------------


def _do_gen(args: argparse.Namespace) -> int:
    """从研究结果生成学习地图。

    ## 两条路径（用户决策：先 CLI，能离线验证）

    | 路径 | 何时 | 产出 |
    |---|---|---|
    | **LLM** | 有 API key 且未加 `--offline` | 模块+知识点（结构化）+ 平替修辞**生成** |
    | **离线** | 无 key 或 `--offline` | 研究片段直接切分 + 平替修辞**种子** |

    ★ 离线路径**不是退而求其次的临时方案**：它让无 key 环境能跑完
    L-1/L-2 确认流程与可视化（用户要求的"全流程可视化"要能在
    任何环境下验证）。两条路径产出的 `LearningMap` 结构**完全相同**，
    下游（show/accept/edit/rollback）不区分它们。

    ⚠️ 离线路径用**种子**平替修辞、且知识点切分是启发式 ——
    界面上会显式标注，避免把种子当最终稿
    （`scenes.py:14`：名称必须按本书自创）。
    """
    import asyncio

    from storyforge.infra.retrieval import ResearchCache
    from storyforge.modes.mappings import load_mapping_seed

    store = _store()
    book_id = args.book_id

    # ── 读研究结果（两条路径都要）──
    cache = ResearchCache()
    entry = cache.get(args.topic) if args.topic else None
    raw_snippets: list[dict] = []
    shortfall = ""
    if entry:
        raw_snippets = [
            sn for sn in ((entry.get("result") or {}).get("snippets") or [])
            if isinstance(sn, dict)
        ]
    else:
        shortfall = (
            f"缓存里没有主题 {args.topic!r} 的研究结果 —— "
            f"先运行 `storyforge research -t {args.topic!r} -s {args.scale}`"
        )

    # ── 决定走哪条路径 ──
    #
    # ⚠️ 判据要先看**有没有资料**再看 key：无资料时 LLM 无从下手
    # （会编造）。初版先打"用 LLM 生成…"再判断，导致输出**误导**
    # （打印了 LLM 却走了离线路径）—— 实测发现后把提示移到分支内。
    #
    # ★ 生成逻辑已拆到 `learning_gen.py`（契约规则 10：单文件 ≤800 行，
    #   本文件 2026-09-23 涨到 806 行被拦下）。拆的是**生成**，
    #   本文件保留**读写与 CLI 操作**（show/accept/edit/rollback）。
    use_llm = not args.offline and has_api_key() and bool(raw_snippets)
    if not use_llm and not args.offline and has_api_key() and not raw_snippets:
        print("⚠ 无研究资料 → 走离线路径（LLM 没有依据会编造）")
    elif args.offline:
        print("（--offline：强制离线路径）")

    points: list[KnowledgePoint] = []
    mappings: list[TermMapping] = []
    used_seed = False
    calls = 0
    verify_note = ""

    if use_llm:
        print(f"用 LLM 生成学习地图与平替修辞 …（研究资料 {len(raw_snippets)} 条）")
        try:
            points, mappings, calls, verify_note = asyncio.run(
                gen_with_llm(
                    topic=args.topic, genre=args.genre, scale=args.scale,
                    snippets=raw_snippets, shortfall=shortfall, book_id=book_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"⚠ LLM 生成失败（{type(exc).__name__}: {str(exc)[:120]}）",
                  file=sys.stderr)
            print("  回落到离线路径（种子 + 启发式切分）", file=sys.stderr)
            use_llm = False

    if not use_llm:
        points = points_from_snippets(raw_snippets, topic=args.topic)
        seed = load_mapping_seed(args.genre)
        mappings = [
            TermMapping(term=m.term, replacement=m.replacement,
                        rationale=m.rationale)
            for m in seed.mappings
        ]
        used_seed = True

    m = LearningMap(topic=args.topic or "", scale=args.scale,
                    points=points, mappings=mappings,
                    research_shortfall=shortfall)
    archived = _save_map(store, book_id, m)

    print(f"学习地图已生成（书 {book_id}）")
    print(f"  主题      ：{m.topic or '（未指定）'}")
    print(f"  知识点    ：{len(m.points)} 个")
    if used_seed:
        print(f"  平替修辞  ：{len(m.mappings)} 条（**种子**，需人工调整）")
        print("              （离线路径：未调用 LLM；说法必须按本书自创）")
    else:
        print(f"  平替修辞  ：{len(m.mappings)} 条（LLM 生成，待 L-2 逐条确认）")
        print(f"  LLM 调用  ：{calls} 次")
        # ★ 核验结论必须落到用户眼前（它决定"地图能不能信"）。
        #   未核验时**如实说未核验**，不显示成通过。
        print(f"  准确性    ：{verify_note or '★ 未核验'}")
    if archived:
        print(f"  存档      ：{archived}")
    if shortfall:
        print(f"  ⚠ {shortfall}")
    print()
    print("下一步（两个确认点，用户决策：L-1 / L-2）：")
    print(f"  storyforge learn-map show   --book-id {book_id}")
    print(f"  storyforge learn-map accept --book-id {book_id} --points      # L-1")
    print(f"  storyforge learn-map accept --book-id {book_id} --mappings    # L-2")
    return 0


# -- show -------------------------------------------------------------------


def _do_show(args: argparse.Namespace) -> int:
    store = _store()
    m = _load_map(store, args.book_id)

    print("=" * 78)
    print(f"学习地图 · 书 {args.book_id}")
    print("=" * 78)
    print(f"主题：{m.topic}    档位：{m.scale}")
    print(f"知识点 {len(m.points)} 个    平替修辞 {len(m.mappings)} 条"
          f"（已确认 {len(m.accepted_mappings)}）")
    if m.research_shortfall:
        print(f"⚠ 研究缺口：{m.research_shortfall}")

    print()
    print("── L-1 知识点（要教什么）──")
    if not m.points:
        print("  （空）")
    for i, p in enumerate(m.points[:_MAX_LIST], 1):
        src = "有来源" if p.has_source else "★ 无来源"
        print(f"  {i:>3}. [{p.point_id}] {p.name}")
        if args.verbose and p.summary:
            print(f"       {p.summary[:90]}")
        print(f"       {src}")

    print()
    print("── L-2 平替修辞（怎么说）──")
    for i, mp in enumerate(m.mappings[:_MAX_LIST], 1):
        flag = "✓" if mp.accepted else "○"
        rev = f"  重做{mp.revision}次" if mp.revision else ""
        print(f"  {flag} {i:>3}. {mp.term} → {mp.replacement}{rev}")

    issues = validate_map(m)
    print()
    if issues:
        print(f"★ 校验发现 {len(issues)} 个问题：")
        for it in issues[:10]:
            print(f"    - {it}")
    else:
        print("✓ 校验通过")
    return 0


# -- accept -----------------------------------------------------------------


def _do_accept(args: argparse.Namespace) -> int:
    """确认知识点（L-1）/ 平替修辞（L-2）。

    ★ 确认前**必须过校验** —— 否则会把有问题的地图"盖章通过"，
    而它是后续世界观的输入（错误会放大）。
    """
    store = _store()
    m = _load_map(store, args.book_id)

    if not args.points and not args.mappings:
        print("需要 --points（确认 L-1）或 --mappings（确认 L-2）",
              file=sys.stderr)
        return 2

    if args.points:
        issues = [i for i in validate_map(m) if "知识点" in i or "来源" in i]
        if issues and not args.force:
            print("★ L-1 未通过校验，拒绝确认（用 --force 可强制）：", file=sys.stderr)
            for it in issues:
                print(f"    - {it}", file=sys.stderr)
            return 1
        _save_map(store, args.book_id, m)
        print(f"✓ L-1 知识点已确认（{len(m.points)} 个）")

    if args.mappings:
        targets = (
            [m.find_mapping(t) for t in args.mappings]
            if args.mappings is not True else m.mappings
        )
        # `--mappings` 也可以是"全部"（无值时）；`argparse` 用 `nargs="*"`
        missing = [t for t, got in zip(args.mappings or [], targets) if got is None]
        if missing:
            print(f"★ 找不到这些术语：{missing}", file=sys.stderr)
            return 1
        for mp in targets:
            if mp is not None:
                mp.accepted = True
        _save_map(store, args.book_id, m)
        print(f"✓ L-2 已确认 {len([t for t in targets if t])} 条")

    left = m.unconfirmed_mappings()
    print()
    print(f"L-2 剩余未确认：{len(left)} 条")
    if left:
        print("  未确认的条目不会进入 writer prompt（避免半成品术语进正文）")
    return 0


# -- edit -------------------------------------------------------------------


def _do_edit(args: argparse.Namespace) -> int:
    """逐条编辑（用户决策的粒度：**术语 → 说法**）。

    ★ 改动前自动存档 → 可 `rollback` 回退。
    """
    store = _store()
    m = _load_map(store, args.book_id)

    mp = m.find_mapping(args.term)
    if mp is None:
        print(f"★ 找不到术语 {args.term!r}；用 `show` 看现有条目", file=sys.stderr)
        return 1
    if not args.replacement:
        print("需要 --replacement", file=sys.stderr)
        return 2

    old = mp.replacement
    mp.replacement = args.replacement
    # 改动后**自动取消确认** —— 改过的条目需要重新过目（用户要求"能编辑"，
    # 但改完还标着"已确认"会让确认状态失去意义）
    mp.accepted = False
    archived = _save_map(store, args.book_id, m)

    print(f"✓ {mp.term}")
    print(f"    {old}")
    print(f" →  {mp.replacement}")
    print("   （已自动取消该条的确认状态 —— 改动需重新确认）")
    if archived:
        print(f"   存档：{archived}（可用 rollback 回退）")
    return 0


# -- reject -----------------------------------------------------------------


def _do_reject(args: argparse.Namespace) -> int:
    """标记某条需要重做（`revision += 1`）。

    ★ 与 `edit` 的区别：`edit` 是用户自己改，`reject` 是"这条不行，
    请重新生成"。两者都取消确认状态，但 `revision` 计数只记 reject
    （用于观测"哪些条目难产"）。
    """
    store = _store()
    m = _load_map(store, args.book_id)

    mp = m.find_mapping(args.term)
    if mp is None:
        print(f"★ 找不到术语 {args.term!r}", file=sys.stderr)
        return 1
    mp.revision += 1
    mp.accepted = False
    archived = _save_map(store, args.book_id, m)
    print(f"✓ {mp.term!r} 已标记重做（第 {mp.revision} 次）")
    print("   ★ 注意：一期**不会自动重生成**该条 ——")
    print("     重生成需要 LLM（属 `LearningMapProvider` 的职责，阶段 2-3 的 LLM 部分）")
    if archived:
        print(f"   存档：{archived}")
    return 0


# -- rollback ---------------------------------------------------------------


def _do_rollback(args: argparse.Namespace) -> int:
    """回退到历史第 N 版（用户明确要求"能回退"）。"""
    store = _store()
    book_id = args.book_id
    revs = _list_revisions(store, book_id)
    if not revs:
        print(f"★ 书 {book_id} 没有历史版本可回退", file=sys.stderr)
        return 1

    target = args.revision
    match = [k for n, k in revs if n == target]
    if not match:
        print(f"★ 没有第 {target} 版；可用：{[n for n, _ in revs]}", file=sys.stderr)
        return 1

    # 回退前先把**当前**版本也存档（回退本身也可回退）
    current = _load_map(store, book_id)
    _archive(store, book_id, current)

    raw = json.loads(store.read_text(match[0]))
    restored = _from_dict(raw)
    store.write_json(ArtifactStore.learning_map_key(book_id), restored.to_dict())

    print(f"✓ 已回退到第 {target} 版")
    print(f"   知识点 {len(restored.points)} 个 / "
          f"平替修辞 {len(restored.mappings)} 条"
          f"（已确认 {len(restored.accepted_mappings)}）")
    print("   （当前版本已存档，回退本身也可再回退）")
    return 0


# -- history ----------------------------------------------------------------


def _do_history(args: argparse.Namespace) -> int:
    store = _store()
    revs = _list_revisions(store, args.book_id)
    if not revs:
        print(f"书 {args.book_id} 暂无历史版本")
        return 0
    print(f"书 {args.book_id} 的学习地图历史（共 {len(revs)} 版）")
    for n, key in revs:
        try:
            raw = json.loads(store.read_text(key))
            np_ = len(raw.get("points") or [])
            nm = len(raw.get("mappings") or [])
            na = sum(1 for x in (raw.get("mappings") or [])
                     if isinstance(x, dict) and x.get("accepted"))
            print(f"  第{n:>3}版  {np_:>3} 知识点 / {nm:>3} 修辞（已确认 {na}）  {key}")
        except Exception:  # noqa: BLE001
            print(f"  第{n:>3}版  ★ 读取失败  {key}")
    return 0


__all__ = ["cmd_learn_map"]
