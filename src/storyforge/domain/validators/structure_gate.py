"""卷级扩张度闸门（对策②，痛点 5）。

## 为什么必须存在，且必须**阻断**

旧项目 `ultra_structure_config.json` 的指标**设计正确**：

    { "min_arcs": 6, "min_core_arcs": 2, "min_relationship_arcs": 2,
      "min_opposition_arcs": 2, "min_beats_per_arc": 8, "max_beats_per_arc": 20,
      "min_arc_capacity": 300, "min_milestones": 9,
      "min_factions": 4, "min_characters": 3, "min_volumes": 3,
      "enabled": false, "shadow": true }        ← ★ 关键在这里

**但它 `enabled: false, shadow: true`** —— 从不阻断。于是书 23 以
「400 章全在第 1 卷、4 个里程碑、0 个势力实体」的状态**顺利开始写作并写了 400 章**。

> **设计对了却关着的校验器，等于没有。**

本模块的 `enabled` 默认 `True`，且失败**返回阻断结论**而非仅告警。

## 实测基线（本闸门要拦住的坏数据）

    书 23：400 章全在第 1 卷；卷 2~5 blueprint=missing；plot_nodes 仅 4 个
           （3 → 205 之间有 202 章真空）；势力实体 0 个
    书 24：447 章全在第 1 卷；volumes[0..2].main_line=""；卷 2 的
           vol_end_hook 是模板句「推向「首次记忆剥夺」的下一个阶段」
    地理熵：书 24/23/18 后半本新增地点 0/1/5

## 设计原则

1. **确定性**（零 LLM）—— 可复现、可回归
2. **失败给 error_code** —— 供**增量补缺**修复（旧项目实测：禁止全量重写，
   大输出必被截断，book23/25 两次 ROLLED_BACK 的根因）
3. **指标可解释** —— 每条都说明"防的是什么"
"""

from __future__ import annotations

from dataclasses import dataclass, field

from storyforge.domain.enums import GateErrorCode
from storyforge.domain.outline import SeriesPlan, VolumePlan

#: `GateErrorCode` 定义在 `domain/enums.py`（枚举单一来源，由 tests/contract 强制）。
#: 此处重新导出以便调用方从本模块导入。
__all__ = [
    "GateErrorCode",
    "GateIssue",
    "GateResult",
    "GateConfig",
    "evaluate_series",
    "evaluate_volume_reuse",
]


@dataclass(frozen=True)
class GateIssue:
    code: GateErrorCode
    message: str
    volume_no: int | None = None
    #: 供修复节点使用的定位信息
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化 —— 供图 state 与日志使用。

        ★ 图 state 只放**小标量**（契约规则 5/19），所以闸门问题在进 state 前
        必须转成 dict。这个方法就是那个转换点。

        （`GateResult` 本身不进 state：它含 metrics 与全部 issue，
        体积不固定；state 里只留 `gate_ok` + `gate_issues` 两个小字段。）
        """
        return {
            "code": self.code.value,
            "message": self.message,
            "volume_no": self.volume_no,
            "context": dict(self.context),
        }


@dataclass
class GateResult:
    """闸门结论。`ok=False` 表示**阻断**，必须修复后才能继续。"""

    ok: bool
    issues: list[GateIssue] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    #: 是否处于 shadow 模式（只报告不阻断）
    shadow: bool = False

    @property
    def blocking(self) -> bool:
        return not self.ok and not self.shadow

    def by_code(self) -> dict[str, list[GateIssue]]:
        out: dict[str, list[GateIssue]] = {}
        for i in self.issues:
            out.setdefault(i.code.value, []).append(i)
        return out

    def issues_to_state(self, *, limit: int = 20) -> list[dict]:
        """问题清单 → 可进图 state 的小 dict 列表（**有上限**）。

        ⚠️ 必须有上限：闸门可能一次报出几百项（旧项目实测 412 项 lint error），
        全部塞进 state 会让 checkpoint 膨胀，而 state 只应放小标量。
        超出的部分由 `metrics` 里的计数体现。
        """
        return [i.to_dict() for i in self.issues[:limit]]

    def summary(self) -> str:
        if self.ok:
            return "结构闸门通过"
        codes = ", ".join(sorted({i.code.value for i in self.issues}))
        prefix = "[SHADOW]" if self.shadow else "[BLOCKED]"
        return f"{prefix} 结构闸门失败（{len(self.issues)} 项）：{codes}"


@dataclass
class GateConfig:
    """闸门参数。默认值 = `config.settings.StructureGateSettings`。"""

    enabled: bool = True
    #: ⚠️ shadow 默认 **False** —— 旧项目正是 shadow=true 导致 400 章挤第 1 卷
    shadow: bool = False
    new_locations_per_volume_min: int = 2
    new_factions_per_volume_min: int = 2
    milestone_max_gap: int = 25
    location_reuse_ratio_max: float = 0.5

    # ── 卷规模：**相对区间** + 绝对下限 ──
    #
    # ★ 为什么不是绝对区间（150~250）（实测修正）
    #
    # 初版用绝对区间 [150, 250]，配合「各卷之和 = 目标章数」，
    # 会**把书锁死为固定卷数**：
    #
    #     目标 400 章 → 每卷 150~250 → 只可能 2 卷（200+200）
    #     目标 800 章 → 只可能 4 卷（200×4）
    #
    # 模型规划出 4 卷 × 100 章（结构上完全合理）时被拒，而它**无法知道**
    # 唯一合法解是"正好 2 卷" —— 这是**过约束**：闸门在替作者定卷数，
    # 而不是在检查质量。
    #
    # 更根本的是：旧项目的真实缺陷不是"每卷章数少"，而是**分配不均**
    # （400 章全挤第 1 卷）。那一点由下面独立的 `max_volume_share` 检查覆盖。
    #
    # 因此改为**相对区间**：
    #   · 每卷占比 ∈ [`volume_share_min`, `volume_share_max`]
    #   · 且每卷章数 ≥ `chapters_per_volume_floor`（防"每卷 3 章"的碎片化）
    #
    # 400 章时：4×100 章（25% each）→ 通过；1 卷 400 章（100%）→ 被拦。
    # 800 章时：4×200 → 通过；8×100 → 通过。不再锁死卷数。
    volume_share_min: float = 0.15
    volume_share_max: float = 0.60
    #: 每卷章数的**绝对下限**（防碎片化）。低于此值不是"卷"，是"章组"。
    chapters_per_volume_floor: int = 20


def evaluate_series(plan: SeriesPlan, cfg: GateConfig | None = None) -> GateResult:
    """评估全书规划的扩张度。"""
    c = cfg or GateConfig()
    issues: list[GateIssue] = []
    metrics: dict[str, float] = {}

    if not c.enabled:
        return GateResult(ok=True, metrics={"enabled": 0.0}, shadow=c.shadow)

    volumes = sorted(plan.volumes, key=lambda v: v.volume_no)

    # ── 1. 卷数 ──
    if plan.total_volumes and len(volumes) != plan.total_volumes:
        issues.append(
            GateIssue(
                GateErrorCode.VOLUME_COUNT,
                f"声明 {plan.total_volumes} 卷，实际规划 {len(volumes)} 卷"
                f"（旧项目后续卷 blueprint=missing 就是这样产生的）",
            )
        )

    # ── 2. 章数分配：**防 400 章挤第 1 卷** ──
    if plan.target_chapters and volumes:
        shares = [v.chapter_count / plan.target_chapters for v in volumes]
        metrics["max_volume_share"] = max(shares) if shares else 0.0
        # 单卷占比 > 60% 且总卷数 > 1 → 明显失衡
        if len(volumes) > 1 and max(shares) > 0.6:
            worst = max(volumes, key=lambda v: v.chapter_count)
            issues.append(
                GateIssue(
                    GateErrorCode.CHAPTER_DISTRIBUTION,
                    f"第 {worst.volume_no} 卷占全书 {max(shares):.0%} 章"
                    f"（{worst.chapter_count}/{plan.target_chapters}）—— "
                    f"旧项目书 23 正是 400 章全挤第 1 卷",
                    volume_no=worst.volume_no,
                    context={"chapters": worst.chapter_count,
                             "target": plan.target_chapters},
                )
            )
        total = plan.chapter_sum()
        metrics["chapter_sum_ratio"] = total / plan.target_chapters
        if abs(total - plan.target_chapters) > max(5, plan.target_chapters * 0.1):
            issues.append(
                GateIssue(
                    GateErrorCode.CHAPTER_DISTRIBUTION,
                    f"各卷章数之和 {total} 与目标 {plan.target_chapters} 偏差过大",
                    context={"sum": total, "target": plan.target_chapters},
                )
            )

    # ── 3. 逐卷检查升级台阶 ──
    seen_antagonists: set[str] = set()
    seen_locations: set[str] = set()
    seen_factions: set[str] = set()
    for v in volumes:
        _check_volume(
            v, c, issues, seen_antagonists, seen_locations, seen_factions,
            target_chapters=plan.target_chapters,
        )

    # ── 4. 全局指标 ──
    metrics["volumes"] = float(len(volumes))
    metrics["total_new_locations"] = float(len(seen_locations))
    metrics["total_new_factions"] = float(len(seen_factions))
    metrics["distinct_antagonists"] = float(len(seen_antagonists))
    metrics["milestones"] = float(sum(len(v.milestones) for v in volumes))

    ok = not issues
    return GateResult(ok=ok, issues=issues, metrics=metrics, shadow=c.shadow)


def _check_volume(
    v: VolumePlan,
    c: GateConfig,
    issues: list[GateIssue],
    seen_antagonists: set[str],
    seen_locations: set[str],
    seen_factions: set[str],
    *,
    target_chapters: int = 0,
) -> None:
    # 新地图
    locs = [x.strip() for x in v.new_locations if x.strip()]
    if len(locs) < c.new_locations_per_volume_min:
        issues.append(
            GateIssue(
                GateErrorCode.NEW_LOCATIONS,
                f"第 {v.volume_no} 卷只开 {len(locs)} 个新地图"
                f"（要求 ≥{c.new_locations_per_volume_min}）—— "
                f"旧项目实测书 24 后半本新增地点为 0",
                volume_no=v.volume_no,
                context={"have": locs, "need": c.new_locations_per_volume_min},
            )
        )
    dup = [x for x in locs if x in seen_locations]
    if dup:
        issues.append(
            GateIssue(
                GateErrorCode.NEW_LOCATIONS,
                f"第 {v.volume_no} 卷的「新地图」{dup} 在既往卷已出现过",
                volume_no=v.volume_no,
                context={"duplicated": dup},
            )
        )
    seen_locations.update(locs)

    # 新势力
    facs = [x.strip() for x in v.new_factions if x.strip()]
    if len(facs) < c.new_factions_per_volume_min:
        issues.append(
            GateIssue(
                GateErrorCode.NEW_FACTIONS,
                f"第 {v.volume_no} 卷只登场 {len(facs)} 个新势力"
                f"（要求 ≥{c.new_factions_per_volume_min}）—— "
                f"旧项目势力实体实测 0 个",
                volume_no=v.volume_no,
                context={"have": facs, "need": c.new_factions_per_volume_min},
            )
        )
    dup_f = [x for x in facs if x in seen_factions]
    if dup_f:
        issues.append(
            GateIssue(
                GateErrorCode.NEW_FACTIONS,
                f"第 {v.volume_no} 卷的「新势力」{dup_f} 在既往卷已出现过",
                volume_no=v.volume_no,
                context={"duplicated": dup_f},
            )
        )
    seen_factions.update(facs)

    # 反派必须各卷不同
    ant = v.antagonist.strip()
    if not ant:
        issues.append(
            GateIssue(
                GateErrorCode.ANTAGONIST_VARIETY,
                f"第 {v.volume_no} 卷未指定主要对手",
                volume_no=v.volume_no,
            )
        )
    elif ant in seen_antagonists:
        issues.append(
            GateIssue(
                GateErrorCode.ANTAGONIST_VARIETY,
                f"第 {v.volume_no} 卷的主要对手「{ant}」与既往卷重复"
                f"—— 长篇需要对手阶梯（层级随卷递增）",
                volume_no=v.volume_no,
                context={"antagonist": ant},
            )
        )
    else:
        seen_antagonists.add(ant)

    # 升级说明必须具体（非模板句）
    if v.is_template_escalation():
        issues.append(
            GateIssue(
                GateErrorCode.ESCALATION_QUALITY,
                f"第 {v.volume_no} 卷的升级说明是模板句或无实质内容："
                f"{v.escalation_from_prev!r}"
                f"（旧项目书 24 实测「推向「X」的下一个阶段」）",
                volume_no=v.volume_no,
                context={"escalation": v.escalation_from_prev},
            )
        )

    # 旧地图收尾
    if not v.old_map_closure.strip():
        issues.append(
            GateIssue(
                GateErrorCode.OLD_MAP_CLOSURE,
                f"第 {v.volume_no} 卷未说明旧地图如何收尾"
                f"（应有交代地离开，而非弃置）",
                volume_no=v.volume_no,
            )
        )

    # 卷规模：**相对占比** + 绝对下限（见 `GateConfig` 的说明）
    n = v.chapter_count
    if n < c.chapters_per_volume_floor:
        issues.append(
            GateIssue(
                GateErrorCode.CHAPTER_DISTRIBUTION,
                f"第 {v.volume_no} 卷只有 {n} 章（绝对下限 {c.chapters_per_volume_floor}）"
                f"—— 这不是一卷，是章组；碎片化的卷会让升级台阶失去意义",
                volume_no=v.volume_no,
                context={"chapters": n, "floor": c.chapters_per_volume_floor},
            )
        )
    elif target_chapters:
        share = n / target_chapters
        if share < c.volume_share_min or share > c.volume_share_max:
            issues.append(
                GateIssue(
                    GateErrorCode.CHAPTER_DISTRIBUTION,
                    f"第 {v.volume_no} 卷 {n} 章，占全书 {share:.0%}，"
                    f"超出 [{c.volume_share_min:.0%}, {c.volume_share_max:.0%}]"
                    f"（目标 {target_chapters} 章）—— "
                    f"占比过小说明本卷撑不起一条完整的升级台阶",
                    volume_no=v.volume_no,
                    context={"chapters": n, "share": round(share, 3),
                             "target": target_chapters},
                )
            )

    # 里程碑密度：**防 202 章真空**（旧项目书 23：3 → 205）
    if n > 0:
        required = max(1, n // c.milestone_max_gap)
        have = len(v.milestones)
        if have < required:
            issues.append(
                GateIssue(
                    GateErrorCode.MILESTONE_DENSITY,
                    f"第 {v.volume_no} 卷 {n} 章只有 {have} 个里程碑"
                    f"（要求 ≥{required}，即间隔 ≤{c.milestone_max_gap} 章）",
                    volume_no=v.volume_no,
                    context={"have": have, "need": required},
                )
            )
        # 最大间隔
        if v.milestones:
            points = sorted(m.chapter_no for m in v.milestones)
            span_start = v.chapter_start
            gaps = []
            for p in points:
                gaps.append(p - span_start)
                span_start = p
            gaps.append(v.chapter_end - span_start)
            worst_gap = max(gaps)
            if worst_gap > c.milestone_max_gap:
                issues.append(
                    GateIssue(
                        GateErrorCode.MILESTONE_DENSITY,
                        f"第 {v.volume_no} 卷存在 {worst_gap} 章的里程碑真空"
                        f"（上限 {c.milestone_max_gap}）—— "
                        f"旧项目书 23 实测 202 章真空（3 → 205）",
                        volume_no=v.volume_no,
                        context={"max_gap": worst_gap},
                    )
                )
        else:
            issues.append(
                GateIssue(
                    GateErrorCode.MILESTONE_DENSITY,
                    f"第 {v.volume_no} 卷没有任何里程碑",
                    volume_no=v.volume_no,
                )
            )


def evaluate_volume_reuse(
    volume: VolumePlan, chapter_locations: list[int | None], cfg: GateConfig | None = None
) -> GateResult:
    """章级地点复用检查 —— 防「全书困在一处」。

    旧项目书 23 的 400 章场景 token 主体是「门/电梯/楼层/走廊」，
    唯一地标「长明楼」只出现 5 次。本函数在**细纲阶段**就能发现该问题，
    而不是等 400 章写完。
    """
    c = cfg or GateConfig()
    issues: list[GateIssue] = []
    metrics: dict[str, float] = {}

    known = [x for x in chapter_locations if x is not None]
    if not known:
        return GateResult(ok=True, metrics={"known_locations": 0.0}, shadow=c.shadow)

    counts: dict[int, int] = {}
    for loc in known:
        counts[loc] = counts.get(loc, 0) + 1
    top_loc, top_n = max(counts.items(), key=lambda kv: kv[1])
    ratio = top_n / len(known)
    metrics["location_reuse_ratio"] = ratio
    metrics["distinct_locations"] = float(len(counts))

    if ratio > c.location_reuse_ratio_max:
        issues.append(
            GateIssue(
                GateErrorCode.LOCATION_REUSE,
                f"地点 {top_loc} 占本卷 {ratio:.0%} 的章节"
                f"（上限 {c.location_reuse_ratio_max:.0%}）—— "
                f"旧项目书 23 的 400 章正是困在一栋楼里",
                volume_no=volume.volume_no,
                context={"location": top_loc, "count": top_n,
                         "total": len(known), "ratio": ratio},
            )
        )
    return GateResult(
        ok=not issues, issues=issues, metrics=metrics, shadow=c.shadow
    )


__all__ = [
    "GateErrorCode",
    "GateIssue",
    "GateResult",
    "GateConfig",
    "evaluate_series",
    "evaluate_volume_reuse",
]
