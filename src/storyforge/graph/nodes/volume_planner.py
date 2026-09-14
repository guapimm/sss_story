"""节点：全卷升级台阶规划（对策②，痛点 5）。

## 它解决的确切问题

旧项目 `scales.json` 的 `story.ultra.rule` **明文规定「本次只细化第 1 卷」**，
且 `per_vol: 300~500` → 模型把 `target_chapters` **全部分配给第 1 卷**
（因为只有它需要细化）→ 第 1 卷的 `vol_goal` 是「在长明楼读透三十七条」
→ **400 章全在一栋楼里**；卷 2~5 只有占位目标（`blueprint="missing"`）。

**实测地理熵**（从章 `plot`+`title` 抽地点 token，比较前半本 vs 后半本）：

    book 24（447 章）后半本新增地点 **0**
    book 23（400 章）                  **1**（且非地名）
    book 18（350 章）                  **5**（全是已登场势力的变体）

书 23 的高频"地点"token 是 `周德海(11) 裴照隔门(9) 电梯在四楼(5) 长明楼(5)
陈砚下楼(5) 他上楼(4) 电梯门(4) ...` —— **400 章的场景主体是「门/电梯/楼层/走廊」**，
唯一地标「长明楼」只出现 **5 次**。

## 本节点的设计

1. **一次规划全部卷**（不是只规划第 1 卷）—— 由 `SeriesPlanOut` 的
   validator 强制 `total_volumes == len(volumes)`。
2. **升级台阶是必填字段** —— 模型无法省略。
3. **闸门失败走增量补缺**（旧项目实测：**禁止全量重写**，
   大输出必被模型截断 —— book23/25 两次 ROLLED_BACK 的根因）。
4. **零 LLM 的闸门**先跑，只有失败才再调 LLM 修复。

## 成本依据

旧项目实测：book 23 大纲 3.93M token / 全书 11.9M ≈ **33%**；
但 1500 章外推大纲只占 **6%** → **在大纲层多花 5 倍 token 是数量级上的廉价投资**。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from storyforge.config import get_settings
from storyforge.domain.outline import Milestone, SeriesPlan, VolumePlan
from storyforge.domain.validators.structure_gate import (
    GateConfig,
    GateResult,
    evaluate_series,
)
from storyforge.infra.llm.capability import ModelRegistry, get_registry
from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import SeriesPlanOut
from storyforge.infra.storage.artifacts import ArtifactStore

logger = logging.getLogger(__name__)


#: 规划指令（稳定前缀）—— 放在代码里是因为它是**节点专属**的固定规则，
#: 不含任何逐章/逐书变化的内容。
VOLUME_PLANNER_SYSTEM = """你是长篇小说结构规划师。为给定的主题规划**全部卷**的升级台阶。

## 最重要的要求：横向扩张是必填项

长篇小说的最大失败模式是**全书困在一个地方**。真实案例：一本 400 章的
规则怪谈小说，全部 400 章都发生在一栋楼的「门 / 电梯 / 楼层 / 走廊」之间，
唯一地标只被提到 5 次。原因不是作者不会写，而是**大纲层从未规划过扩张**。

因此每一卷**必须**给出：

1. `new_locations`（≥2 个）—— 本卷新开的地图。不得与既往卷重复。
   必须是**真正的新地点**（新城市/新秘境/新建筑群），
   不是「XX楼的二楼」（那是同一地点的细分）。
2. `new_factions`（≥2 个）—— 本卷新登场的势力/组织。不得与既往卷重复。
3. `antagonist` —— 本卷主要对手。**必须与既往卷不同**（长篇需要对手阶梯）。
4. `escalation_from_prev` —— 相对上卷「升级在哪」。
   必须写**具体内容**（例：「从单栋楼的规则解谜，升级为跨城节点网的势力博弈」）。
   禁止「推向X的下一个阶段」「持续推进主线」这类模板句。

## 其他要求

- `chapter_count`：每卷 150~250 章。
- `milestones`：卷内里程碑，**间隔不超过 25 章**（否则中间会出现情节真空）。
  每条写成 `{"chapter_no": 卷内章号, "description": "一句话"}`。
- `old_map_closure`：旧地图如何收尾（有交代地离开，而非弃置）。
- 各卷 `chapter_count` 之和应等于目标章数。
- `total_volumes` 必须等于 `volumes` 数组的长度。

## ★ 字段名必须逐字照抄下面的骨架

实测教训：如果只用散文描述字段，模型会**自造同义名**
（`volume_number` / `volume_title` / `core_focus` …），
而每个名字对不上都会让整轮规划失败。**下文骨架就是唯一合法的形状。**

```json
{
  "logline": "一句话故事梗概",
  "theme": "主题",
  "tone": "基调",
  "total_volumes": 2,
  "target_chapters": 25,
  "volumes": [
    {
      "volume_no": 1,
      "title": "卷标题",
      "goal": "本卷目标（人类可读）",
      "new_locations": ["新地图一", "新地图二"],
      "new_factions": ["新势力一", "新势力二"],
      "antagonist": "本卷主要对手",
      "power_ceiling": "本卷力量/能力上限",
      "core_conflict": "本卷核心矛盾",
      "escalation_from_prev": "相对上卷升级在哪（写具体内容）",
      "old_map_closure": "旧地图如何收尾",
      "volume_end_hook": "卷末钩子",
      "chapter_count": 13,
      "milestones": [
        {"chapter_no": 4, "description": "里程碑内容"},
        {"chapter_no": 9, "description": "里程碑内容"}
      ]
    }
  ]
}
```

卷 1 的 `escalation_from_prev` 写「开局卷，从零建立本世界的规则感」这类即可，
但**不得留空**、不得写模板句。

**只输出 JSON**，不要任何解释或代码块标记。"""


@dataclass
class VolumePlanResult:
    """规划结果。"""

    plan: SeriesPlan | None
    gate: GateResult
    repair_rounds: int
    llm_calls: int
    ok: bool
    error: str | None = None


def series_plan_from_out(out: SeriesPlanOut) -> SeriesPlan:
    """把 LLM 输出转成领域对象（分配卷内章号区间 + 换算里程碑为全书号）。

    ## 两件事都在这里做，且都遵循「模型给局部，代码算全局」

    1. **卷章号区间**在这里一次定稿并**冻结**（决策 D-15：卷边界定稿后不再变，
       保证已写章节的显示标签稳定）。
    2. **里程碑章号**从**卷内序号**换算为**全书号**。

    ⚠️ 第 2 点曾经出过错：初版直接让模型输出"全书章号"，但模型不知道
    各卷的全局偏移（那是代码按 `chapter_count` 累加出来的）——
    实测多卷规划里第二、三卷的里程碑间隔全部超限，因为模型按**卷内**编号
    而闸门按**全书号**比较。这与旧项目 `since_ch` 卷内号/全书号混用
    （导致「已持续 10011 章」）是**同一类错误**，必须从设计上消除。
    """
    volumes: list[VolumePlan] = []
    cursor = 1
    for v in sorted(out.volumes, key=lambda x: x.volume_no):
        n = max(1, int(v.chapter_count))
        start, end = cursor, cursor + n - 1
        cursor = end + 1

        # 卷内序号 → 全书号（夹紧到本卷范围内，容忍模型的轻微越界）
        milestones: list[Milestone] = []
        for i, m in enumerate(v.milestones):
            local = max(1, min(n, int(m.chapter_no)))
            milestones.append(
                Milestone(
                    chapter_no=start + local - 1,
                    # ★ 标识符由**代码**生成，不要求模型编。
                    # 实测：模型把 milestones 写成 `[8, 16]`（裸章号），
                    # 信息完全等价；要求它同时编一个稳定 key 只会增加
                    # 一个可以被拒的理由（旧项目 entity_classify 的同类形态）。
                    # 与 D-15「模型给局部事实、代码算全局标识」同一原则。
                    key=(m.key or f"v{v.volume_no}m{i + 1}"),
                    description=m.description,
                )
            )
        milestones.sort(key=lambda x: x.chapter_no)

        volumes.append(
            VolumePlan(
                volume_no=v.volume_no,
                title=v.title,
                goal=v.goal,
                new_locations=list(v.new_locations),
                new_factions=list(v.new_factions),
                antagonist=v.antagonist,
                power_ceiling=v.power_ceiling,
                core_conflict=v.core_conflict,
                escalation_from_prev=v.escalation_from_prev,
                old_map_closure=v.old_map_closure,
                volume_end_hook=v.volume_end_hook,
                chapter_start=start,
                chapter_end=end,
                milestones=milestones,
            )
        )
    return SeriesPlan(
        book_id=0,  # 由调用方填充
        total_volumes=out.total_volumes,
        target_chapters=out.target_chapters,
        volumes=volumes,
    )


def _repair_prompt(gate: GateResult, plan: SeriesPlan) -> str:
    """构造**增量补缺**的修复指令（不是全量重写）。

    旧项目实测教训：
        **修复必须走增量补缺，禁止回退成「全量重写整份故事 JSON」**
        —— 大输出必被模型截断（book23/25 两次 ROLLED_BACK 的根因）。
        补丁按 error_code 分类：arcs / beats / capacity / milestones /
        factions / characters / volumes，各自小 prompt。
    """
    by_code = gate.by_code()
    lines = ["【上一次规划未通过结构闸门，请只修补下列问题】", ""]
    for code, issues in by_code.items():
        lines.append(f"## 问题类别：{code}")
        for i in issues[:6]:
            loc = f"第{i.volume_no}卷：" if i.volume_no else ""
            lines.append(f"- {loc}{i.message}")
        lines.append("")
    lines.append("【当前规划（只修改有问题的部分，其余保持原样）】")
    lines.append(json.dumps(plan.to_dict(), ensure_ascii=False, indent=1)[:6000])
    lines.append("")
    lines.append("请输出**完整**的修正后 JSON（结构与之前相同）。")
    return "\n".join(lines)


async def plan_volumes(
    client: LLMClient,
    *,
    book_id: int,
    topic: str,
    genre: str,
    target_chapters: int,
    bible_text: str,
    registry: ModelRegistry | None = None,
    gate_config: GateConfig | None = None,
    idempotency_key: str | None = None,
) -> VolumePlanResult:
    """规划全卷升级台阶，并用结构闸门校验（失败则增量补缺）。

    这是**唯一**产出卷级台阶的地方 —— 后续骨架/细纲都受它约束。
    """
    settings = get_settings()
    # ★ `shadow` **刻意不从配置读**。
    #
    # 旧项目 `ultra_structure_config.json` 的指标设计正确，但为
    # `enabled: false, shadow: true` —— 闸门存在却从不阻断，
    # 于是 400 章全挤第 1 卷、412 项 lint error 全部放行。
    #
    # 「只报告不阻断」这一开关本身就是失败模式：它让"闸门已实现"
    # 与"闸门在生效"变成两件事，而报表上看不出区别。
    # 因此 `GateConfig.shadow` 默认 False 且**不暴露为配置项** ——
    # 要改只能改代码，评审时必然被看见。
    cfg = gate_config or GateConfig(
        enabled=settings.structure_gate.enabled,
        new_locations_per_volume_min=settings.structure_gate.new_locations_per_volume_min,
        new_factions_per_volume_min=settings.structure_gate.new_factions_per_volume_min,
        milestone_max_gap=settings.structure_gate.milestone_max_gap,
        location_reuse_ratio_max=settings.structure_gate.location_reuse_ratio_max,
        volume_share_min=settings.structure_gate.volume_share_min,
        volume_share_max=settings.structure_gate.volume_share_max,
        chapters_per_volume_floor=settings.structure_gate.chapters_per_volume_floor,
    )

    user = (
        f"【主题】{topic}\n"
        f"【题材】{genre}\n"
        f"【目标总章数】{target_chapters}\n\n"
        f"{bible_text}\n\n"
        f"请规划全部卷的升级台阶。"
    )

    ctx = CallContext(
        node="volume_planner",
        book_id=book_id,
        idempotency_key=idempotency_key,
    )

    out: SeriesPlanOut | None = None
    rounds = 0
    calls = 0
    max_rounds = settings.structure_gate.repair_attempts

    for attempt in range(max_rounds + 1):
        try:
            out, call = await client.complete_structured(
                node="volume_planner",
                system=VOLUME_PLANNER_SYSTEM,
                user=user,
                schema=SeriesPlanOut,
                context=ctx,
            )
            # ★ 幂等命中不计入 llm_calls（未真正调用 provider，算进去会虚高成本）
            if not call.from_cache:
                calls += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("volume plan LLM failed (attempt %d): %s", attempt + 1, exc)
            # ★ 解析失败也必须进入**修复循环**，不能直接放弃。
            #
            # 实测踩到（与 `bible_architect` 同型）：模型漏了一个必填字段
            # （第 3 卷缺 `core_conflict`），`complete_structured` 用尽自己的
            # 修复重试后抛错 → 这里直接 return → **整条规划链路挂掉**。
            #
            # 而「调用失败」与「闸门失败」本就可以走同一条修复路径：
            # 把**具体错误**发回去重试。旧项目 book23/25 两次 ROLLED_BACK
            # 就是"一次失败即放弃"的代价。
            rounds += 1
            if rounds > max_rounds:
                break
            user = (
                f"【主题】{topic}\n【题材】{genre}\n"
                f"【目标总章数】{target_chapters}\n\n"
                f"【上一次输出无法解析，请修正后重新输出完整 JSON】\n"
                f"- {str(exc)[:400]}\n\n"
                f"要求：每个卷对象都必须给出**全部必填字段**"
                f"（volume_no / title / goal / new_locations / new_factions / "
                f"antagonist / power_ceiling / core_conflict / "
                f"escalation_from_prev / old_map_closure / chapter_count），"
                f"一个都不能省。只输出 JSON 本身。"
            )
            continue

        plan = series_plan_from_out(out)
        plan.book_id = book_id
        gate = evaluate_series(plan, cfg)

        if gate.ok:
            return VolumePlanResult(
                plan=plan, gate=gate, repair_rounds=rounds,
                llm_calls=calls, ok=True,
            )

        rounds += 1
        logger.info(
            "structure gate failed (round %d/%d): %s",
            rounds, max_rounds, gate.summary(),
        )
        if rounds > max_rounds:
            break
        # ★ 增量补缺：只把问题清单 + 当前规划发回去
        user = _repair_prompt(gate, plan)

    return VolumePlanResult(
        plan=None,
        gate=evaluate_series(plan, cfg) if out else GateResult(ok=False),
        repair_rounds=rounds, llm_calls=calls, ok=False,
        error=f"结构闸门在 {rounds} 轮修复后仍未通过",
    )


def persist_series_plan(store: ArtifactStore, plan: SeriesPlan) -> str:
    """落盘全卷台阶，返回产物 key。

    大产物**不入图 state**（只有 ref）。
    """
    ref = ArtifactStore.series_plan_key(plan.book_id)
    store.write_json(ref, plan.to_dict())
    return ref.key


def load_series_plan(store: ArtifactStore, book_id: int) -> SeriesPlan:
    """从产物加载全卷台阶。"""
    ref = ArtifactStore.series_plan_key(book_id)
    data = store.read_json(ref)
    volumes = [
        VolumePlan(
            volume_no=v["volume_no"],
            title=v["title"],
            goal=v.get("goal", ""),
            new_locations=list(v.get("new_locations", [])),
            new_factions=list(v.get("new_factions", [])),
            antagonist=v.get("antagonist", ""),
            power_ceiling=v.get("power_ceiling", ""),
            core_conflict=v.get("core_conflict", ""),
            escalation_from_prev=v.get("escalation_from_prev", ""),
            old_map_closure=v.get("old_map_closure", ""),
            volume_end_hook=v.get("volume_end_hook", ""),
            chapter_start=v["chapter_start"],
            chapter_end=v["chapter_end"],
            milestones=[
                Milestone(chapter_no=m["chapter_no"], key=m["key"],
                          description=m.get("description", ""))
                for m in v.get("milestones", [])
            ],
        )
        for v in data["volumes"]
    ]
    return SeriesPlan(
        book_id=data["book_id"],
        total_volumes=data["total_volumes"],
        target_chapters=data["target_chapters"],
        volumes=volumes,
    )


def volume_for_chapter(plan: SeriesPlan, global_no: int) -> VolumePlan | None:
    """按全书章号定位所属卷（决策 D-15：卷内序号派生不存储）。"""
    for v in plan.volumes:
        if v.chapter_start <= global_no <= v.chapter_end:
            return v
    return None


def local_index(plan: SeriesPlan, volume_no: int, global_no: int) -> int:
    """派生卷内序号（1-based）。**不存储**（决策 D-15）。"""
    v = next((x for x in plan.volumes if x.volume_no == volume_no), None)
    if v is None:
        return global_no
    return global_no - v.chapter_start + 1


__all__ = [
    "VOLUME_PLANNER_SYSTEM",
    "VolumePlanResult",
    "plan_volumes",
    "series_plan_from_out",
    "persist_series_plan",
    "load_series_plan",
    "volume_for_chapter",
    "local_index",
]
