"""节点：细纲批次生成（P3-4，对策④核心落地）。

## 它是「伤势恢复率 0%」这个问题的**修复点**

旧项目的能力矩阵（实测）：

    | 角色          | 能看到损伤 | 能规划治疗 | 能落实治疗 |
    | architect     | ❌         | ❌        | —         |
    | writer        | ✅         | ❌        | ⚠️ 只能照细纲写 |

`architect.py` 中 `伤势|损伤` **0 命中** —— 细纲层**根本不规划伤**。

因此本节点是唯一能让恢复发生的地方：它把 `HealthScheduler` 排出的
`health_beats` **注入 `ChapterPlan.health_beats`**，使恢复成为
**细纲的一部分**，再由 writer 照细纲写出。

    scheduler（纯确定性）→ 细纲（有场景支撑的节拍）→ writer（照写）

而不是旧项目那样：

    writer 收到「本轮必须推进」 vs 细纲「禁止细纲外转折」 → **服从细纲** → 永不恢复

## 每章必须过两道零 LLM 闸门

1. **`ChapterPlan.lint()`** —— 标题/plot/场景数/场景完整性/entry/exit/end_hook
   （旧项目实测落地率：`plot_detail` p50 788 < 下限 900、`entry_state` 13%、
   `milestone_ids` 0% —— 全部是"缺省即通过"造成的）
2. **`HealthScheduler.validate_beats_against_scenes`** —— 伤势节拍必须指向
   **真实存在**的场景（否则仍是凭空痊愈；`scene_seq` 由本节点按规则填入）

## 批次生成，且**禁止全量重写**

旧项目 book23/25 两次 `ROLLED_BACK` 的根因是「大输出被模型截断」。
本节点按 `pipeline.detail_batch_size`（默认 15 章）分批，
失败时**只重发问题章节**。

## 章号口径（决策 D-15）

prompt 给模型的是**全书递增章号**；模型不需要知道卷内序号。
`volume_no` 由代码按 `SeriesPlan` 派生。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from storyforge.config import get_settings
from storyforge.domain.enums import BeatType
from storyforge.domain.health import HealthBeat
from storyforge.domain.outline import (
    ChapterPlan,
    SceneBeat,
    VolumePlan,
    WorldStateSnapshot,
)
from storyforge.domain.schedulers.health_scheduler import (
    HealthSchedule,
    HealthScheduler,
)
from storyforge.graph.nodes.continuity import continuity_issues
from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import ChapterPlanOut, DetailBatchOut, SkeletonOut
from storyforge.infra.storage.artifacts import ArtifactStore

logger = logging.getLogger(__name__)


DETAIL_PLANNER_SYSTEM = """你是长篇小说细纲规划师。为给定的章号区间写出**逐章细纲**。

## 每一章必须给出

- `title`：章标题。**具体、有信息量**，且**不得与其他章重复**
  （旧项目实测：29 个重复标题覆盖 65/300 章，全是「深夜的敲门声」这类同义反复）。
- `plot`：一句话剧情梗概。
- `beat_type`：节拍类型。取值 `normal | opening | turning | climax | boss |
  emotion | recall | settle | heal | recovery_prep | transition`。
- `is_transition`：本章**是否换场**（地点或时间推移）。
  ★ **换场是正常的，不是缺陷**（旧项目把换图一律判为"细纲偏离"，
  加上 8 处同类禁令，导致 400 章困在一栋楼里）。
  ⚠️ **判断口径是「场景层面」**：本章的场景拍点里换了地方就要置 `true`，
  **不必等到换城市/换地图** —— 同一栋楼里换房间也算换场。
  置 `true` 不会受罚；漏置 `true` 而场景确实换了地方，才会被判为"硬切"。
- `scenes`：**4~8 个**分场景拍点。每个拍点必须四项齐全：
  `goal`（谁想做什么，具体到动作与对象）+ `obstacle`（阻碍）+
  `outcome`（可检验的结果）+ `why_now`（为什么是现在 ——
  必须由上一拍的结果或上一章的出口状态逼出）。
  另给 `location`（地点名）与 `participants`（在场人物名）。
- `entry_state` / `exit_state`：章首/章末状态，四项：
  `location`（地点名）、`story_time`（故事内时间，如「第三日夜」）、
  `present`（在场人物名）、`known`（**谁知道什么**）、
  `open_thread`（章末**具体**未收束的事：谁在做什么、什么没解决）。
- `end_hook`：章末钩子 —— 本章现场内**悬而未决的事件本身**。
  **禁止**「莫名的波动」「心头一紧」这类无主暗示。

## 硬性要求

1. **相邻章必须因果相接**：第 N 章的 `exit_state` 与第 N+1 章的
   `entry_state` 必须是**同一时刻的同一场景**（除非是规划内的换场章）。
   禁止「拒绝给某人开门」→ 下一章「与某人同席」这种无动机跳变。
2. **`why_now` 不得空洞**：不能写「因为剧情需要」，必须指向具体前因。
3. **场景要展开，不要概述**：每个拍点写清 谁 / 做什么 / 遇到什么 / 结果。
4. **换场章必须给出过渡**：赶路、整备、时间推进，一两句自然交代即可，
   不得硬切。
5. **标题两两不同**，且不要用「XX的XX」同构句式批量生产。

## ★ 字段名必须逐字照抄下面的骨架

实测教训：只用散文描述字段时，模型会自造同义名或改变形状，
而每个不一致都会让**整批**细纲失败。

```json
{
  "chapters": [
    {
      "title": "章标题（具体、有信息量、不得与其他章重复）",
      "plot": "一句话剧情梗概",
      "beat_type": "normal",
      "is_transition": false,
      "scenes": [
        {
          "goal": "谁想做什么（具体到动作与对象）",
          "obstacle": "阻碍是什么",
          "outcome": "结果是什么（必须可检验）",
          "why_now": "为什么是现在（必须由上一拍结果逼出）",
          "location": "场景地点名",
          "participants": ["在场人物名"]
        }
      ],
      "entry_state": {
        "location": "地点名",
        "story_time": "故事内时间，如「第三日夜」",
        "present": ["在场人物名"],
        "known": ["谁知道什么"],
        "open_thread": "章末未收束的事件本身"
      },
      "exit_state": {
        "location": "地点名",
        "story_time": "故事内时间",
        "present": ["在场人物名"],
        "known": ["谁知道什么"],
        "open_thread": "章末未收束的事件本身"
      },
      "end_hook": "章末钩子：本章现场内悬而未决的事件本身"
    }
  ]
}
```

⚠️ `scenes` 必须有 **4~8** 个，每拍四项（`goal`/`obstacle`/`outcome`/`why_now`）
**一个都不能少**；`entry_state` / `exit_state` 是**对象**不是字符串。

**只输出 JSON**，不要任何解释或代码块标记。"""


#: 一章最少场景数（与 `ChapterPlan.lint` 默认一致）
MIN_SCENES = 4
#: `plot` 最小字数 —— 旧项目 `plot_detail` p50 仅 788（下限 900）
MIN_PLOT_CHARS = 12


@dataclass
class DetailBatchResult:
    plans: dict[int, ChapterPlan] = field(default_factory=dict)
    refs: dict[int, str] = field(default_factory=dict)
    lint_issues: dict[int, list[str]] = field(default_factory=dict)
    health_beats_injected: int = 0
    llm_calls: int = 0
    ok: bool = False
    error: str | None = None
    #: ★ **最后一轮未通过 lint** 的产物（失败时才有值）。
    #:
    #: ## 为什么必须回传（实测踩到，与 `SkeletonResult.last_skeleton` 同一类）
    #:
    #: 失败批次**不落盘**（这是设计正确的行为：半成品不该被下游读到）。
    #: 但诊断时去读产物文件，读到的是**上一批成功**的章节，甚至读不到 ——
    #: 于是无法回答"报警那一章到底怎么写的、它有没有声明换场"。
    #:
    #: 实测：书 16 第 12 章报「地点跳变」，我想看它的
    #: `is_transition` / `beat_type` / 场景拍点，结果 `plans/00012.json`
    #: 根本不存在 —— **只能靠猜**。而"猜"正是本文件反复警告的过拟合入口。
    #:
    #: ⚠️ 只放内存、不进 state（State 不得含大文本，契约规则 5）；
    #: 图节点会把它按需落盘到 `debug/`。
    last_plans: dict[int, ChapterPlan] = field(default_factory=dict)

    @property
    def chapters(self) -> list[ChapterPlan]:
        return [self.plans[k] for k in sorted(self.plans)]


# --------------------------------------------------------------------------- #
# 零 LLM 转换与校验
# --------------------------------------------------------------------------- #


def _state_from_out(st: object) -> WorldStateSnapshot:
    """schema → 领域快照。

    ★ 细纲阶段实体 ID 尚不存在（抽取在写作之后），故用**名**表达；
    `location_entity_id` 留空，由后续解析器补齐。
    """
    return WorldStateSnapshot(
        location_name=getattr(st, "location", "") or "",
        story_time=getattr(st, "story_time", "") or "",
        present_names=[str(x) for x in (getattr(st, "present", None) or [])],
        known_facts={},  # 人名的 known 需归一后才映射到实体 ID
        open_thread=getattr(st, "open_thread", "") or "",
    )


def chapter_plan_from_out(
    out: ChapterPlanOut, *, global_no: int, volume_no: int, segment_no: int = 0
) -> ChapterPlan:
    """schema → 领域 `ChapterPlan`。"""
    try:
        beat = BeatType((out.beat_type or "").strip() or BeatType.NORMAL.value)
    except ValueError:
        logger.warning("未知 beat_type %r，回退为 normal", out.beat_type)
        beat = BeatType.NORMAL

    scenes = [
        SceneBeat(
            seq=i,
            goal=s.goal,
            obstacle=s.obstacle,
            outcome=s.outcome,
            why_now=s.why_now,
            # ★ 这两个字段原先被**丢掉**了 —— prompt 要求填、schema 也声明了，
            #   但构造领域对象时没取，于是场景层面的地点/人物信息
            #   在进入领域模型的瞬间就没了（实测发现，见 `SceneBeat` 注释）。
            location_name=(s.location or "").strip(),
            participant_names=[str(x).strip() for x in (s.participants or []) if str(x).strip()],
        )
        for i, s in enumerate(out.scenes)
    ]
    return ChapterPlan(
        global_no=global_no,
        volume_no=volume_no,
        segment_no=segment_no,
        title=out.title,
        plot=out.plot,
        beat_type=beat,
        is_transition=bool(out.is_transition),
        scenes=scenes,
        entry_state=_state_from_out(out.entry_state),
        exit_state=_state_from_out(out.exit_state),
        end_hook=out.end_hook,
    )


def attach_health_beats(
    plan: ChapterPlan, schedule: HealthSchedule
) -> int:
    """把调度好的伤势节拍照注入本章细纲。返回注入条数。

    ## `scene_seq` 的分配规则（**不能随便填 0**）

    旧项目的"痊愈"是无场景支撑的一句声明。本设计让每个节拍**占用一个真实场景**：
    按节拍在本章的先后顺序，均匀落到 `[0, scene_count-1]` 上。

    为什么不是全部填 0：`scene_seq` 全为 0 会让多拍挤在同一场景，
    恢复仍然显得突变；且 `validate_beats_against_scenes` 无法区分
    「没排」与「排了但没落地」。

    **修改 plan 方式**：原地设置 `plan.health_beats`。
    LangGraph 的"禁止原地修改"针对的是**输入 state**；`ChapterPlan` 是
    本节点新建的产物对象，尚未进入 state，故这里直接赋值。
    """
    slot = schedule.for_chapter(plan.global_no)
    beats = list(slot.beats)
    if not beats:
        plan.health_beats = []
        return 0

    scene_count = len(plan.scenes)
    if scene_count <= 0:
        # 无场景可承载 —— 保留节拍，让 lint/validator 显式报错，不静默丢弃
        plan.health_beats = beats
        return len(beats)

    # 均匀落到真实场景：第 i 个节拍 → 第 round(i*(n-1)/(k-1)) 个场景
    k = len(beats)
    assigned: list[HealthBeat] = []
    for i, b in enumerate(beats):
        seq = 0 if k == 1 else round(i * (scene_count - 1) / (k - 1))
        assigned.append(
            HealthBeat(
                injury_id=b.injury_id,
                action=b.action,
                chapter_no=b.chapter_no,
                scene_seq=seq,
                note=b.note,
            )
        )
    plan.health_beats = assigned

    # 节拍类型：有痊愈 → heal；只有铺垫 → recovery_prep
    # （仅在本章尚未被显式指定更"重"的节拍类型时改写）
    hint = slot.beat_type_hint
    if hint and plan.beat_type in (BeatType.NORMAL, BeatType.SETTLE):
        plan.beat_type = BeatType(hint)
    return len(assigned)


def lint_chapter(plan: ChapterPlan) -> list[str]:
    """一章的完整 lint（结构 lint + 伤势节拍落地）。

    旧项目 `outline_lint.py` 只做结构检查，**伤势节拍没有任何检查**
    （因为细纲 schema 里根本没有伤势字段）。
    """
    issues = list(plan.lint(min_scenes=MIN_SCENES, min_plot_chars=MIN_PLOT_CHARS))
    issues += HealthScheduler.validate_beats_against_scenes(
        list(plan.health_beats), len(plan.scenes)
    )
    return issues


def validate_batch(
    plans: dict[int, ChapterPlan], *, expected: list[int] | None = None
) -> dict[int, list[str]]:
    """整批 lint。返回 `{章号: 问题列表}`（只含**有问题**的章）。

    额外检查（批次级，单章 lint 看不到）：
      · 章号缺失（模型漏写某章 —— 旧项目会静默跳过，导致细纲空洞）
      · 标题重复（旧项目实测 29 个重复标题覆盖 65/300 章）
      · 相邻章出口/入口不接（`WorldStateSnapshot.continuity_problems`）
    """
    issues: dict[int, list[str]] = {}
    for no, plan in plans.items():
        got = lint_chapter(plan)
        if got:
            issues[no] = got

    if expected:
        missing = [n for n in expected if n not in plans]
        if missing:
            # 用第一个缺失章号作为 key，便于上层按"补哪几章"重试
            issues[missing[0]] = [
                f"本批缺章：{missing}（模型漏写章号 —— 细纲空洞必须显式报错）"
            ]

    # 标题重复
    seen: dict[str, int] = {}
    for no in sorted(plans):
        t = plans[no].title.strip()
        if not t:
            continue
        if t in seen:
            issues.setdefault(no, []).append(
                f"章标题与第{seen[t]}章逐字重复：「{t}」"
                f"（旧项目实测 29 个重复标题覆盖 65/300 章）"
            )
        else:
            seen[t] = no

    # 相邻连续性（只在同批内检查；跨批由上层带上一章 exit 检查）
    ordered = sorted(plans)
    for a, b in zip(ordered, ordered[1:]):
        if b != a + 1:
            continue
        problems = continuity_issues(plans[a], plans[b])
        if problems:
            issues.setdefault(b, []).extend(problems)
    return issues


# --------------------------------------------------------------------------- #
# 落盘 / 读取
# --------------------------------------------------------------------------- #


def persist_plan(store: ArtifactStore, book_id: int, plan: ChapterPlan) -> str:
    """落盘一章细纲，返回产物 key。"""
    ref = ArtifactStore.plan_key(book_id, plan.global_no)
    store.write_json(ref, plan_to_dict(plan))
    return ref.key


def plan_to_dict(plan: ChapterPlan) -> dict:
    """细纲序列化（含伤势节拍 —— 旧项目完全缺失这一块）。"""
    return {
        "global_no": plan.global_no,
        "volume_no": plan.volume_no,
        "segment_no": plan.segment_no,
        "title": plan.title,
        "plot": plan.plot,
        "beat_type": plan.beat_type.value,
        "is_transition": plan.is_transition,
        "scenes": [
            {
                "seq": s.seq,
                "goal": s.goal,
                "obstacle": s.obstacle,
                "outcome": s.outcome,
                "why_now": s.why_now,
                # ★ 场景地点/人物也要落盘 —— 否则落盘再读回就丢了，
                #   下游（审校/连续性）在**读回后**同样看不到（实测发现）
                "location_name": s.location_name,
                "participant_names": list(s.participant_names),
            }
            for s in plan.scenes
        ],
        "entry_state": _state_to_dict(plan.entry_state),
        "exit_state": _state_to_dict(plan.exit_state),
        "plant_foreshadowings": list(plan.plant_foreshadowings),
        "resolve_foreshadowings": list(plan.resolve_foreshadowings),
        "end_hook": plan.end_hook,
        "health_beats": [
            {
                "injury_id": b.injury_id,
                "action": b.action.value,
                "chapter_no": b.chapter_no,
                "scene_seq": b.scene_seq,
                "note": b.note,
            }
            for b in plan.health_beats
        ],
    }


def _state_to_dict(s: WorldStateSnapshot) -> dict:
    return {
        "location_entity_id": s.location_entity_id,
        "location_name": s.location_name,
        "story_time": s.story_time,
        "present_entity_ids": list(s.present_entity_ids),
        "present_names": list(s.present_names),
        "known_facts": {str(k): list(v) for k, v in s.known_facts.items()},
        "open_thread": s.open_thread,
    }


def plan_from_dict(d: dict) -> ChapterPlan:
    """细纲反序列化。"""
    from storyforge.domain.enums import HealthAction

    def _state(x: dict) -> WorldStateSnapshot:
        return WorldStateSnapshot(
            location_entity_id=x.get("location_entity_id"),
            location_name=x.get("location_name", ""),
            story_time=x.get("story_time", ""),
            present_entity_ids=list(x.get("present_entity_ids", [])),
            present_names=list(x.get("present_names", [])),
            known_facts={
                int(k): list(v) for k, v in (x.get("known_facts") or {}).items()
            },
            open_thread=x.get("open_thread", ""),
        )

    return ChapterPlan(
        global_no=int(d["global_no"]),
        volume_no=int(d["volume_no"]),
        segment_no=int(d.get("segment_no", 0)),
        title=d.get("title", ""),
        plot=d.get("plot", ""),
        beat_type=BeatType(d.get("beat_type", BeatType.NORMAL.value)),
        is_transition=bool(d.get("is_transition", False)),
        scenes=[
            SceneBeat(
                seq=int(s.get("seq", i)),
                goal=s.get("goal", ""),
                obstacle=s.get("obstacle", ""),
                outcome=s.get("outcome", ""),
                why_now=s.get("why_now", ""),
                location_name=s.get("location_name", ""),
                participant_names=list(s.get("participant_names", [])),
            )
            for i, s in enumerate(d.get("scenes", []))
        ],
        entry_state=_state(d.get("entry_state", {})),
        exit_state=_state(d.get("exit_state", {})),
        plant_foreshadowings=list(d.get("plant_foreshadowings", [])),
        resolve_foreshadowings=list(d.get("resolve_foreshadowings", [])),
        end_hook=d.get("end_hook", ""),
        health_beats=[
            HealthBeat(
                injury_id=int(b["injury_id"]),
                action=HealthAction(b["action"]),
                chapter_no=int(b["chapter_no"]),
                scene_seq=int(b.get("scene_seq", 0)),
                note=b.get("note", ""),
            )
            for b in d.get("health_beats", [])
        ],
    )


def load_plan(store: ArtifactStore, book_id: int, global_no: int) -> ChapterPlan:
    ref = ArtifactStore.plan_key(book_id, global_no)
    return plan_from_dict(store.read_json(ref))


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #


def _detail_user_prompt(
    *,
    bible_text: str,
    volume: VolumePlan,
    skeleton: SkeletonOut | None,
    batch_start: int,
    batch_end: int,
    previous_exit: str = "",
    health_text: str = "",
    extra_issues: dict[int, list[str]] | None = None,
) -> str:
    """细纲批次的 user prompt（易变部分）。

    ★ 章号区间用**全书递增号**（D-15）。模型只负责内容，编号由代码管。
    """
    parts = [
        bible_text,
        "",
        f"【当前卷】第 {volume.volume_no} 卷《{volume.title}》：{volume.goal}",
    ]
    if skeleton is not None:
        arcs = "；".join(f"{a.key}({a.kind})" for a in skeleton.arcs[:8])
        parts += [
            f"【本卷故事线】{arcs}",
            f"【本卷段落目标】{' / '.join(skeleton.segments[:8])}",
            f"【本卷主要角色】{'、'.join(skeleton.characters)}",
            f"【本卷势力】{'、'.join(skeleton.factions)}",
        ]
        if volume.new_locations:
            parts.append(f"【本卷新地图】{'、'.join(volume.new_locations)}")
    if previous_exit:
        parts += [
            "",
            "【上一章出口状态（本批第一章的开场必须与它一致）】",
            previous_exit,
        ]
    if health_text:
        parts += [
            "",
            "【伤势节拍·已由调度器排定，必须落到场景里】",
            health_text,
            "→ 请在对应章节的 scenes 里安排能承载该节拍的场景"
            "（寻医=求诊场景；求药=取得药物；恶化=伤情发作；"
            "疗伤尝试=施治过程；好转=见效；痊愈=明确治愈且有代价）。",
        ]
    parts += [
        "",
        f"【本批任务】写出第 {batch_start} 章到第 {batch_end} 章共 "
        f"{batch_end - batch_start + 1} 章的细纲（**每章都要有，不得跳过任何章号**）。",
        f"章号依次为：{', '.join(str(n) for n in range(batch_start, batch_end + 1))}",
    ]
    if extra_issues:
        parts += ["", "【上一次未通过校验，请针对下列问题修正后重新输出完整 JSON】"]
        for no in sorted(extra_issues):
            parts.append(f"第{no}章：")
            parts += [f"  - {i}" for i in extra_issues[no]]
        parts.append("其余章节保持原样。")
    return "\n".join(parts)


def _inject_chapter_numbers(out: DetailBatchOut, start: int, end: int) -> list[int]:
    """按输出顺序把章号钉回 `[start, end]`。

    ★ 模型不负责编号：它只要按顺序给出 `end-start+1` 章。
    数量不符则**显式失败**（不猜、不补空章）—— 编号错位是旧项目
    `since_ch` 卷内号/全书号混用的同类事故。
    """
    want = end - start + 1
    got = len(out.chapters)
    if got != want:
        raise ValueError(
            f"本批应输出 {want} 章（{start}~{end}），实际 {got} 章 —— "
            f"章号由代码钉定，数量不符无法对齐"
        )
    return list(range(start, end + 1))


def format_health_beats(schedule: HealthSchedule, batch_start: int, batch_end: int) -> str:
    """把窗口内的伤势节拍渲染成 prompt 文本。"""
    lines: list[str] = []
    for ch in sorted(schedule.chapters):
        if not (batch_start <= ch <= batch_end):
            continue
        slot = schedule.chapters[ch]
        for b in slot.beats:
            lines.append(f"- 第{ch}章：{b.note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 节点主函数
# --------------------------------------------------------------------------- #


async def build_detail_batch(
    client: LLMClient,
    *,
    book_id: int,
    volume: VolumePlan,
    bible_text: str,
    batch_start: int,
    batch_end: int,
    skeleton: SkeletonOut | None = None,
    health_schedule: HealthSchedule | None = None,
    previous_exit: WorldStateSnapshot | None = None,
    max_rounds: int = 2,
    idempotency_key: str | None = None,
) -> DetailBatchResult:
    """生成一批细纲（含伤势节拍注入 + 两道零 LLM 闸门）。

    人工确认点 **C-3**：第一批细纲 + **黄金三章正文**完成后确认。
    """
    settings = get_settings()
    if batch_end < batch_start:
        return DetailBatchResult(
            ok=False, error=f"批次区间非法：{batch_start}~{batch_end}"
        )

    ctx = CallContext(
        node="detail_planner", book_id=book_id, volume_no=volume.volume_no,
        idempotency_key=idempotency_key,
    )
    health_text = (
        format_health_beats(health_schedule, batch_start, batch_end)
        if health_schedule
        else ""
    )
    prev_text = _render_state(previous_exit) if previous_exit else ""

    calls = 0
    last_issues: dict[int, list[str]] = {}
    last_plans: dict[int, ChapterPlan] = {}

    for attempt in range(max_rounds + 1):
        user = _detail_user_prompt(
            bible_text=bible_text, volume=volume, skeleton=skeleton,
            batch_start=batch_start, batch_end=batch_end,
            previous_exit=prev_text, health_text=health_text,
            extra_issues=last_issues if attempt > 0 else None,
        )
        try:
            out, call = await client.complete_structured(
                node="detail_planner",
                system=DETAIL_PLANNER_SYSTEM,
                user=user,
                schema=DetailBatchOut,
                context=ctx,
            )
            if not call.from_cache:
                calls += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("detail LLM failed (attempt %d): %s", attempt + 1, exc)
            return DetailBatchResult(
                lint_issues=last_issues, llm_calls=calls, ok=False,
                error=f"细纲生成调用失败：{exc}",
                last_plans=last_plans,
            )

        try:
            numbers = _inject_chapter_numbers(out, batch_start, batch_end)
        except ValueError as exc:
            last_issues = {batch_start: [str(exc)]}
            logger.info("detail batch size mismatch: %s", exc)
            if attempt >= max_rounds:
                break
            continue

        plans: dict[int, ChapterPlan] = {}
        injected = 0
        for no, cout in zip(numbers, out.chapters):
            plan = chapter_plan_from_out(
                cout, global_no=no, volume_no=volume.volume_no
            )
            if health_schedule is not None:
                injected += attach_health_beats(plan, health_schedule)
            plans[no] = plan

        last_issues = validate_batch(
            plans, expected=list(range(batch_start, batch_end + 1))
        )
        if not last_issues:
            return DetailBatchResult(
                plans=plans, lint_issues={}, health_beats_injected=injected,
                llm_calls=calls, ok=True,
            )
        # ★ 记下**这一轮**的产物（失败不落盘，诊断只能靠它）
        last_plans = plans
        logger.info(
            "detail lint failed (round %d): %d chapters with issues",
            attempt + 1, len(last_issues),
        )
        if attempt >= max_rounds:
            break

    return DetailBatchResult(
        plans={}, lint_issues=last_issues, llm_calls=calls, ok=False,
        error=(
            f"细纲在 {max_rounds + 1} 次尝试后仍未通过 lint"
            f"（{len(last_issues)} 章有问题）"
        ),
        # ★ 带上**最后一轮**的产物，供诊断（见字段注释）
        last_plans=last_plans,
    )


def _render_state(s: WorldStateSnapshot) -> str:
    """把出口状态渲染成一行文本（供下一批的开场锚点）。"""
    parts = []
    if s.location_name:
        parts.append(f"地点：{s.location_name}")
    if s.story_time:
        parts.append(f"时间：{s.story_time}")
    if s.present_names:
        parts.append(f"在场：{'、'.join(s.present_names)}")
    if s.open_thread:
        parts.append(f"未收束：{s.open_thread}")
    return "；".join(parts)


__all__ = [
    "DETAIL_PLANNER_SYSTEM",
    "MIN_SCENES",
    "MIN_PLOT_CHARS",
    "DetailBatchResult",
    "chapter_plan_from_out",
    "attach_health_beats",
    "lint_chapter",
    "validate_batch",
    "persist_plan",
    "plan_to_dict",
    "plan_from_dict",
    "load_plan",
    "format_health_beats",
    "build_detail_batch",
]
