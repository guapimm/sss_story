"""learn 学习地图的**生成**逻辑（LLM 路径 + 离线路径）。

## 为什么从 `commands/learning.py` 拆出来

契约规则 10（单文件 ≤800 行）在 2026-09-23 被撞到（`learning.py` 涨到 806 行）。
**撞限时的正确反应是拆，不是放宽** ——
规则 10 是**为旧项目 `pipeline.py` 8,607 行立的**。

拆分依据是**职责**（不是凑行数）：

    commands/learning.py      地图的**读写与 CLI 操作**（show/accept/edit/rollback）
    commands/learning_gen.py  地图的**生成**（研究资料 → 知识点 + 平替修辞）

前者是"用户对已有地图做什么"，后者是"地图怎么来"。两者无共享状态。

## ⚠️ 上次拆分的教训（本次刻意规避）

阶段 2-2 拆 `doctor.py` 时，我**凭记忆重写**而非逐字搬运，
引入了两个真 bug（`reg.models.items()` 类型错、误删仍在用的 import），
靠 `doctor: FAIL` + traceback 才发现。

⇒ 本次**逐字搬运**，并补 `scripts/verify_doctor_parity.py` 式的等价性检查
（见 `tests/unit/test_learning_nodes.py` 对导入点的断言）。
"""

from __future__ import annotations

from storyforge.domain.learning import KnowledgePoint, TermMapping


def has_api_key() -> bool:
    """是否配置了 LLM key（决定 `gen` 走 LLM 还是离线路径）。"""
    from storyforge.infra.llm.capability import load_registry

    try:
        reg = load_registry()
    except Exception:  # noqa: BLE001
        return False
    name = getattr(reg, "default_provider", None)
    if name and name in reg.providers:
        return bool(reg.providers[name].resolve_api_key())
    # 没有显式默认 provider → 任一个可用即可
    return any(
        (not p.api_key_env) or p.resolve_api_key()
        for p in reg.providers.values()
    )


def resolve_evidence(
    idxs: list[int], snippets: list[dict],
) -> tuple[tuple[str, ...], str, list[int]]:
    """编号 → (URL 元组, 原文片段, 越界编号列表)。

    ## 为什么溯源要**逐条可核验**（2026-09-23，方案 Ⓐ）

    修的是两个真缺陷，此前都「全绿通过」：

    1. **假溯源**：旧实现把研究资料的**前 3 条 URL 全挂给每条知识点**
       （旧注释自承"不逐条对应某个 URL"）→ 实测 11 条知识点的
       `source_urls` **完全相同**，无法回答"这条依据的是哪一段"。
       `validate_map` 只查"URL 非空" → 查不出来。

    2. **`evidence` 语义错位**：旧为 `evidence=shortfall or ""` ——
       把「研究缺口提示」塞进了「支撑该知识点的原文片段」字段。

    ## 现在的做法

    模型在 `evidence_index` 里回**编号**（prompt 已给资料编号 `[N]`），
    代码据此取 URL 与原文 —— **结构化事实，不是相似度猜测**
    （项目规矩：能用结构性事实回答的问题，不要用文本启发式回答）。

    ⚠️ 编号越界**显式暴露**（返回在第三个值里），不静默丢弃：
    静默丢弃会让"模型乱标"看起来像"溯源正常"，正是禁止的「静默降级」。
    """
    urls: list[str] = []
    texts: list[str] = []
    out_of_range: list[int] = []
    for i in idxs:
        if 1 <= i <= len(snippets):
            sn = snippets[i - 1]
            u = str(sn.get("url", "")).strip()
            if u:
                urls.append(u)
            t = str(sn.get("text", "")).strip()
            if t:
                texts.append(f"[{i}] {t[:300]}")
        else:
            out_of_range.append(i)
    # 去重但保序（同一 URL 多次引用只留一条）
    seen_u: set[str] = set()
    uniq = tuple(u for u in urls if not (u in seen_u or seen_u.add(u)))
    return uniq, "\n".join(texts), out_of_range


async def gen_with_llm(
    *, topic: str, genre: str, scale: str, snippets: list[dict],
    shortfall: str, book_id: int,
) -> tuple[list[KnowledgePoint], list[TermMapping], int]:
    """LLM 路径：研究资料 → 学习地图 →（缺口自检）→ 平替修辞。

    返回 `(知识点, 平替修辞, LLM 调用次数)`。
    """
    from storyforge.config import get_settings
    from storyforge.graph.nodes.learning import (
        check_gaps,
        generate_learning_map,
        generate_term_mappings,
    )
    from storyforge.infra.llm.client import ClientConfig, LLMClient
    from storyforge.infra.llm.transport import make_transport
    from storyforge.modes.mappings import load_mapping_seed

    s = get_settings()
    client = LLMClient(
        make_transport(),
        config=ClientConfig(max_attempts=s.llm.max_attempts,
                            timeout_s=s.llm.default_timeout_s),
    )
    calls = 0

    # ① 学习地图
    map_out, c = await generate_learning_map(
        client, topic=topic, genre=genre, scale=scale, snippets=snippets,
        research_shortfall=shortfall, book_id=book_id,
        idempotency_key=f"learn:map:{book_id}:{topic}",
    )
    calls += c
    if map_out is None:
        raise RuntimeError("学习地图生成失败（空输出）")

    # ② 缺口自检（只报告，不自动补全 —— 补全成本由用户决定）
    gaps: list[str] = []
    try:
        gap_out, c2 = await check_gaps(
            client, topic=topic, map_out=map_out, book_id=book_id,
            idempotency_key=f"learn:gaps:{book_id}:{topic}",
        )
        calls += c2
        gaps = list(gap_out.gaps) if gap_out else []
    except Exception:  # noqa: BLE001
        gaps = []          # 缺口自检失败不该让整轮失败（它是增强项）
    if gaps:
        print(f"  ⚠ 缺口自检发现 {len(gaps)} 个盲区方向：{'、'.join(gaps[:3])}")
        print("    （一期只报告不自动补全 —— 可据此再跑 research 补充资料）")

    # ③ 知识点（**id 由代码生成**，不采信模型的命名 —— 防 id 漂移）
    points: list[KnowledgePoint] = []
    bad_index: list[str] = []          # 越界编号的知识点名（供显式告警）
    no_index: list[str] = []           # 完全未标依据的知识点名
    for mod in map_out.modules:
        for p in mod.knowledge_points:
            urls, evidence, oor = resolve_evidence(
                list(p.evidence_index), snippets
            )
            if oor:
                bad_index.append(f"{p.name}(编号{oor})")
            if not list(p.evidence_index):
                no_index.append(p.name)
            points.append(
                KnowledgePoint(
                    point_id=f"kp{len(points) + 1:03d}",
                    name=p.name.strip()[:60],
                    summary=p.summary.strip()[:120],
                    topic=mod.name,
                    difficulty=int(p.difficulty or 1),
                    source_urls=urls,
                    evidence=evidence,
                )
            )

    # ★ 溯源问题**显式暴露**（不静默）：这两类都不是"能跑就行"的小事 ——
    #   无依据的知识点可能是幻觉，而幻觉会一路带进正文。
    if no_index:
        print(f"  ⚠ {len(no_index)} 个知识点**未标注依据**（可能是幻觉）："
              f"{'、'.join(no_index[:5])}")
    if bad_index:
        print(f"  ⚠ {len(bad_index)} 个知识点的依据编号**越界**（模型乱标）："
              f"{'、'.join(bad_index[:5])}")

    # ④ 平替修辞（带种子当 few-shot —— 用户决策"流派根据已有的来"）
    seed = load_mapping_seed(genre)
    terms = [p.name for p in points]
    maps: list[TermMapping] = []
    if terms:
        batch, c3 = await generate_term_mappings(
            client, topic=topic, genre=genre, terms=terms,
            seed_examples=[
                {"term": m.term, "replacement": m.replacement,
                 "rationale": m.rationale}
                for m in seed.mappings[:5]
            ],
            book_id=book_id,
            idempotency_key=f"learn:mappings:{book_id}:{topic}",
        )
        calls += c3
        seen: set[str] = set()
        for mp in (batch.mappings if batch else []):
            t, r = mp.term.strip(), mp.replacement.strip()
            # 去重 + 拒绝"等于没平替"（与 validate_map 同口径，此处就近拦）
            if not t or not r or t == r or t in seen:
                continue
            seen.add(t)
            maps.append(TermMapping(term=t, replacement=r,
                                    rationale=mp.rationale.strip()))

    return points, maps, calls


def points_from_snippets(snippets: list[dict], *, topic: str) -> list[KnowledgePoint]:
    """离线路径：研究片段 → 知识点（**启发式**，不调 LLM）。

    ⚠️ 这是**确定性基线**，不是真语义抽取：按片段长度过滤 +
    标题作名字。它的价值是让无 key 环境能跑完确认流程；
    真实质量依赖 LLM 路径（`gen_with_llm`）。

    ★ 注意本路径**一直是正确的**溯源做法（逐条对应 URL + 填真实原文），
    反而是 LLM 路径此前不如它（全挂同一组 URL）。
    """
    points: list[KnowledgePoint] = []
    for sn in snippets:
        text = str(sn.get("text", ""))
        if len(text) < 80:
            continue
        points.append(
            KnowledgePoint(
                point_id=f"kp{len(points) + 1:03d}",
                name=str(sn.get("title", ""))[:60] or f"知识点{len(points) + 1}",
                summary=text[:120],
                topic=topic,
                source_urls=(str(sn.get("url", "")),) if sn.get("url") else (),
                evidence=text[:300],
            )
        )
    return points


__all__ = [
    "gen_with_llm",
    "has_api_key",
    "points_from_snippets",
    "resolve_evidence",
]
