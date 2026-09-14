"""细纲生成节点测试（P3-4）—— ★ 本文件是「恢复率 0%」的回归防线。

## 核心验收：恢复必须由**细纲场景**承载

旧项目的能力矩阵：

    | 角色          | 能看到损伤 | 能规划治疗 | 能落实治疗 |
    | architect     | ❌         | ❌        | —         |
    | writer        | ✅         | ❌        | ⚠️ 只能照细纲写 |

`architect.py` 中 `伤势|损伤` **0 命中** → 恢复率实测 **0%**。

本文件验证本节点补上了这个缺口：`HealthScheduler` 排出的节拍
**进入 `ChapterPlan.health_beats` 且指向真实场景**，
从而 writer 拿到的是可执行的细纲，而不是与细纲冲突的催促。

## 其余验收点

· 章号由**代码钉定**（模型只按顺序给章，不负责编号 —— 防旧项目卷/全书号混用）
· 批次 lint：漏章 / 重复标题 / 相邻章出口-入口不接
· 换场章允许地点跳变（旧项目把换图一律判为"细纲偏离"）
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import (
    BeatType,
    HealthAction,
    InjuryKind,
    InjurySeverity,
    InjuryStatus,
)
from storyforge.domain.health import HealthBeat, Injury
from storyforge.domain.outline import (
    ChapterPlan,
    Milestone,
    SceneBeat,
    VolumePlan,
    WorldStateSnapshot,
)
from storyforge.domain.schedulers.health_scheduler import HealthScheduler
from storyforge.graph.nodes.detail_planner import (
    attach_health_beats,
    build_detail_batch,
    chapter_plan_from_out,
    format_health_beats,
    lint_chapter,
    load_plan,
    persist_plan,
    plan_from_dict,
    plan_to_dict,
    validate_batch,
)
from storyforge.infra.llm.client import (
    ClientConfig,
    LLMClient,
    MemoryIdempotencyStore,
)
from storyforge.infra.llm.schemas import ChapterPlanOut, DetailBatchOut, SceneBeatOut
from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 构造器
# --------------------------------------------------------------------------- #


def scene(i: int, **over) -> dict:
    base = dict(
        goal=f"陈砚想在第{i}个地点查证残页",
        obstacle=f"第{i}个阻碍：守夜人拦阻",
        outcome=f"第{i}个结果：拿到半页抄本",
        why_now=f"因为第{i - 1}拍的结果逼他必须现在来",
    )
    base.update(over)
    return base


def chap(**over) -> ChapterPlanOut:
    base = dict(
        title="残页上的第十三条",
        plot="陈砚查证残页，确认第十三条被人改过",
        scenes=[SceneBeatOut(**scene(i)) for i in range(4)],
        entry_state={"location": "长明楼三层值班室", "story_time": "第三日夜",
                     "present": ["陈砚"], "open_thread": "残页缺一页"},
        exit_state={"location": "长明楼三层值班室", "story_time": "第四日晨",
                    "present": ["陈砚"], "open_thread": "第十三条被改过"},
        end_hook="他翻到背面，发现改动的墨色比正文新。",
    )
    base.update(over)
    return ChapterPlanOut(**base)


def batch(n: int, start_title: int = 1) -> DetailBatchOut:
    return DetailBatchOut(
        chapters=[
            chap(
                title=f"第{start_title + i}件委托",
                plot=f"陈砚处理第{start_title + i}件委托，牵出旧档案",
                scenes=[SceneBeatOut(**scene(j)) for j in range(4)],
            )
            for i in range(n)
        ]
    )


def volume(**over) -> VolumePlan:
    base = dict(
        volume_no=1, title="值夜", goal="接手值夜",
        new_locations=["长明楼"], new_factions=["守夜会"],
        chapter_start=1, chapter_end=200,
        milestones=[Milestone(chapter_no=1, key="m0")],
    )
    base.update(over)
    return VolumePlan(**base)


def injured(**over) -> Injury:
    """一条**重伤**（阈值 3 章 → 恢复计划铺得开）。"""
    base = dict(
        id=1, book_id=1, entity_id=10, kind=InjuryKind.WOUND,
        name="左肩剑创", severity=InjurySeverity.SEVERE,
        status=InjuryStatus.NEW, since_chapter=5, updated_chapter=5,
    )
    base.update(over)
    return Injury(**base)


class ScriptedTransport:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def __call__(self, *, model, provider, system, user, **params):
        self.calls.append({"system": system, "user": user, "model": model.id})
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx], {
            "prompt_tokens": 100, "completion_tokens": 50,
            "reasoning_tokens": 20, "cached_tokens": 40,
            "total_tokens": 150, "finish_reason": "stop",
        }


def make_client(responses: list[str]) -> tuple[LLMClient, ScriptedTransport]:
    t = ScriptedTransport(responses)
    return (
        LLMClient(t, config=ClientConfig(max_attempts=1, backoff_base_s=0.0,
                                         structured_retries=0),
                  idempotency=MemoryIdempotencyStore()),
        t,
    )


# --------------------------------------------------------------------------- #
# ★ 核心：伤势节拍进入细纲 + 指向真实场景
# --------------------------------------------------------------------------- #


def test_health_beats_are_injected_with_real_scene_seq() -> None:
    """★ 恢复率 0% 的修复点。

    节拍必须 (a) 进入 `ChapterPlan.health_beats`，
             (b) `scene_seq` 指向**真实存在**的场景。
    """
    plan = ChapterPlan(
        global_no=10, volume_no=1, title="t", plot="陈砚查证残页并确认第十三条被人改过",
        scenes=[SceneBeat(seq=i, goal="g", obstacle="o", outcome="r", why_now="w")
                for i in range(4)],
        entry_state=WorldStateSnapshot(location_name="a"),
        exit_state=WorldStateSnapshot(location_name="a"),
        end_hook="h",
    )
    sched = HealthScheduler().schedule(
        [injured()], from_chapter=6, to_chapter=12
    )
    n = attach_health_beats(plan, sched)

    assert n > 0, "重伤必须被排出恢复节拍"
    assert plan.health_beats, "节拍必须写进细纲（旧项目细纲层完全不规划伤）"
    for b in plan.health_beats:
        assert 0 <= b.scene_seq < len(plan.scenes), (
            f"节拍指向场景 {b.scene_seq}，但只有 {len(plan.scenes)} 个场景 "
            f"—— 凭空痊愈的根源"
        )
    # lint 必须通过（含节拍落地校验）
    assert lint_chapter(plan) == []


def test_heal_chapter_gets_heal_beat_type() -> None:
    """★ 有痊愈节拍的章 → `BeatType.HEAL`。

    旧项目 `VALID_BEATS` 里**没有 heal** → 恢复不是合法节拍类型，
    细纲规划者无法把疗伤排成一章的任务。这是恢复率 0% 的直接成因之一。
    """
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    heal_chs = sched.recovery_chapters
    assert heal_chs, "重伤必须有痊愈章"

    plan = ChapterPlan(global_no=heal_chs[0], volume_no=1, title="t", plot="陈砚查证残页并确认第十三条被人改过",
                       scenes=[SceneBeat(seq=i, goal="g", obstacle="o",
                                        outcome="r", why_now="w") for i in range(4)],
                       entry_state=WorldStateSnapshot(location_name="a"),
                       exit_state=WorldStateSnapshot(location_name="a"), end_hook="h")
    attach_health_beats(plan, sched)
    assert plan.beat_type == BeatType.HEAL


def test_recovery_prep_chapter_gets_prep_beat_type() -> None:
    """只有铺垫节拍的章 → `RECOVERY_PREP`（解决"恢复太突然"）。"""
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    prep = [
        ch for ch, slot in sched.chapters.items()
        if slot.beats and not slot.has_heal
    ]
    assert prep, "重伤必须有预热章（否则恢复是突变）"

    plan = ChapterPlan(global_no=prep[0], volume_no=1, title="t", plot="陈砚查证残页并确认第十三条被人改过",
                       scenes=[SceneBeat(seq=i, goal="g", obstacle="o",
                                        outcome="r", why_now="w") for i in range(4)],
                       entry_state=WorldStateSnapshot(location_name="a"),
                       exit_state=WorldStateSnapshot(location_name="a"), end_hook="h")
    attach_health_beats(plan, sched)
    assert plan.beat_type == BeatType.RECOVERY_PREP


def test_heavy_beat_chapter_type_is_not_downgraded() -> None:
    """已显式指定为爆点/高潮的章**不得**被恢复节拍改写节拍类型。

    恢复是剧情的一部分，不是覆盖剧情的理由。
    """
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    ch = sorted(sched.chapters)[0]
    plan = ChapterPlan(global_no=ch, volume_no=1, title="t", plot="陈砚查证残页并确认第十三条被人改过",
                       beat_type=BeatType.CLIMAX,
                       scenes=[SceneBeat(seq=i, goal="g", obstacle="o",
                                        outcome="r", why_now="w") for i in range(4)],
                       entry_state=WorldStateSnapshot(location_name="a"),
                       exit_state=WorldStateSnapshot(location_name="a"), end_hook="h")
    attach_health_beats(plan, sched)
    assert plan.beat_type == BeatType.CLIMAX


def test_beat_without_scenes_is_reported_not_silently_dropped() -> None:
    """无场景可承载 → 保留节拍并**报错**（不静默丢弃）。

    静默丢弃正是旧项目「写出的信号无人读」的形态。
    """
    plan = ChapterPlan(global_no=6, volume_no=1, title="t", plot="陈砚查证残页并确认第十三条被人改过",
                       scenes=[],
                       entry_state=WorldStateSnapshot(location_name="a"),
                       exit_state=WorldStateSnapshot(location_name="a"), end_hook="h")
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    attach_health_beats(plan, sched)
    assert plan.health_beats, "不得静默丢弃"
    assert any("没有任何场景" in i for i in lint_chapter(plan))


def test_mild_injury_does_not_stall() -> None:
    """轻伤也必须能在窗口内被规划（旧项目「轻微/中」永不产生的同类问题）。"""
    sched = HealthScheduler().schedule(
        [injured(severity=InjurySeverity.MINOR, kind=InjuryKind.WOUND)],
        from_chapter=6, to_chapter=12,
    )
    assert sched.chapters, "轻伤也必须有节拍（阈值 1 章）"


def test_format_health_beats_renders_window_only() -> None:
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    text = format_health_beats(sched, 6, 8)
    assert "左肩剑创" in text
    for line in text.splitlines():
        no = int(line.split("第")[1].split("章")[0])
        assert 6 <= no <= 8, f"窗口外的节拍不得进 prompt：{line}"


# --------------------------------------------------------------------------- #
# 章号由代码钉定（D-15）
# --------------------------------------------------------------------------- #


def test_chapter_numbers_are_pinned_by_code() -> None:
    """模型只按顺序给章，编号由代码写入 —— 数量不符必须显式报错。"""
    plan = chapter_plan_from_out(chap(), global_no=57, volume_no=2)
    assert plan.global_no == 57
    assert plan.volume_no == 2


def test_beat_type_falls_back_on_unknown_value() -> None:
    """未知 beat_type 回退 normal 并记日志（不抛错中断整批）。"""
    plan = chapter_plan_from_out(chap(beat_type="xxx"), global_no=1, volume_no=1)
    assert plan.beat_type == BeatType.NORMAL


def test_world_state_uses_names_not_ids_at_plan_time() -> None:
    """★ 细纲阶段实体 ID 尚不存在 → 状态用**名**表达。

    若强行要求 ID，细纲只能留空（旧项目 `entry_state` 填充率 13%）
    或由模型瞎编 ID（口径漂移）。
    """
    plan = chapter_plan_from_out(chap(), global_no=1, volume_no=1)
    assert plan.entry_state.location_name == "长明楼三层值班室"
    assert plan.entry_state.present_names == ["陈砚"]
    assert plan.entry_state.location_entity_id is None
    assert not plan.entry_state.is_empty()


# --------------------------------------------------------------------------- #
# 批次 lint
# --------------------------------------------------------------------------- #


def _plans(n: int, start: int = 1) -> dict[int, ChapterPlan]:
    out: dict[int, ChapterPlan] = {}
    for i in range(n):
        no = start + i
        out[no] = chapter_plan_from_out(
            chap(title=f"第{no}件委托", plot=f"陈砚处理第{no}件委托并牵出旧档案"),
            global_no=no, volume_no=1,
        )
    return out


def test_valid_batch_passes() -> None:
    assert validate_batch(_plans(3), expected=[1, 2, 3]) == {}


def test_missing_chapter_is_reported() -> None:
    """模型漏写章号 → 显式报错（旧项目会静默产出细纲空洞）。"""
    plans = _plans(3)
    del plans[2]
    issues = validate_batch(plans, expected=[1, 2, 3])
    assert any("缺章" in i for v in issues.values() for i in v)


def test_duplicate_title_in_batch_is_reported() -> None:
    """★ 旧项目实测：29 个重复标题覆盖 65/300 章。"""
    plans = _plans(3)
    plans[3].title = plans[1].title
    issues = validate_batch(plans, expected=[1, 2, 3])
    assert any("逐字重复" in i for v in issues.values() for i in v)


def test_broken_continuity_between_adjacent_chapters_is_reported() -> None:
    """★ 章间跳变是 Editor 低分主因（书 1005：拒绝开门 → 同席吃饭）。"""
    plans = _plans(2)
    plans[1].exit_state = WorldStateSnapshot(
        location_name="长明楼", story_time="夜", open_thread="门外有人唤名"
    )
    plans[2].entry_state = WorldStateSnapshot(
        location_name="镇公所", story_time="次日"
    )
    issues = validate_batch(plans, expected=[1, 2])
    assert any("地点跳变" in i for v in issues.values() for i in v)


def test_transition_chapter_may_change_location() -> None:
    """★ 换场章允许地点跳变。

    旧项目把新地图一律记为「细纲偏离」（`editor.py:106`）+
    `writer_rules.py:374` 禁止写细纲外新地图 → 400 章困在一栋楼里。
    本设计：**规划内的换场正常，规划外的才是加戏**。
    """
    plans = _plans(2)
    plans[1].exit_state = WorldStateSnapshot(location_name="长明楼", story_time="夜")
    plans[2].entry_state = WorldStateSnapshot(location_name="西郊水塔", story_time="次日")
    plans[2].is_transition = True
    issues = validate_batch(plans, expected=[1, 2])
    assert not any("地点跳变" in i for v in issues.values() for i in v)


def test_chapter_with_too_few_scenes_is_reported() -> None:
    plans = _plans(1)
    plans[1].scenes = plans[1].scenes[:2]
    issues = validate_batch(plans, expected=[1])
    assert any("场景数" in i for v in issues.values() for i in v)


# --------------------------------------------------------------------------- #
# 落盘 / 读取（含伤势节拍）
# --------------------------------------------------------------------------- #


def test_plan_roundtrip_preserves_health_beats(tmp_path) -> None:
    """★ 伤势节拍必须能**落盘并读回**。

    旧项目 `must_review` 只写不读（25 命中 / 0 读取点）——
    写出的信号必须有消费者，否则等于没有。
    """
    plan = _plans(1)[1]
    plan.health_beats = [
        HealthBeat(injury_id=7, action=HealthAction.HEALED,
                   chapter_no=1, scene_seq=2, note="左肩剑创痊愈")
    ]
    back = plan_from_dict(plan_to_dict(plan))
    assert len(back.health_beats) == 1
    assert back.health_beats[0].action == HealthAction.HEALED
    assert back.health_beats[0].scene_seq == 2


def test_persist_and_load_roundtrip(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    plan = chapter_plan_from_out(chap(), global_no=42, volume_no=2)
    ref = persist_plan(store, 3, plan)
    assert ref == "books/3/plans/00042.json"
    loaded = load_plan(store, 3, 42)
    assert loaded.global_no == 42
    assert loaded.title == plan.title


def test_load_missing_plan_raises(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFound):
        load_plan(store, 9, 999)


# --------------------------------------------------------------------------- #
# 节点调用
# --------------------------------------------------------------------------- #


async def test_build_detail_batch_success() -> None:
    client, _ = make_client([batch(4).model_dump_json()])
    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="【世界观】x",
        batch_start=1, batch_end=4,
    )
    assert result.ok, result.error
    assert sorted(result.plans) == [1, 2, 3, 4]
    assert result.llm_calls == 1


async def test_build_detail_batch_injects_health_beats() -> None:
    """★ 端到端：调度器 → 细纲。这是恢复率 0% 的完整修复路径。"""
    sched = HealthScheduler().schedule([injured()], from_chapter=6, to_chapter=12)
    client, transport = make_client([batch(12).model_dump_json()])

    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=1, batch_end=12, health_schedule=sched,
    )

    assert result.ok, result.error
    assert result.health_beats_injected > 0, "必须注入伤势节拍"
    with_beats = [p for p in result.plans.values() if p.health_beats]
    assert with_beats, "至少有一章承载伤势节拍"
    for plan in with_beats:
        assert lint_chapter(plan) == []
    # 节拍要求必须进 prompt（否则模型不知道要安排疗伤场景）
    assert "伤势节拍" in transport.calls[0]["user"]


async def test_build_detail_batch_retries_with_issue_list() -> None:
    """★ lint 失败 → 带**逐章问题清单**增量重试（禁止全量重写结构）。"""
    bad = batch(4)
    bad.chapters[2].title = bad.chapters[0].title  # 制造重复标题
    client, transport = make_client([bad.model_dump_json(), batch(4).model_dump_json()])

    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=1, batch_end=4, max_rounds=2,
    )

    assert result.ok, result.error
    assert result.llm_calls == 2
    assert "未通过校验" in transport.calls[1]["user"]
    assert "逐字重复" in transport.calls[1]["user"]


async def test_build_detail_batch_rejects_wrong_count() -> None:
    """★ 章数不符 → 逐次重试，最终显式失败（不猜、不补空章）。"""
    client, transport = make_client([batch(3).model_dump_json()])
    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=1, batch_end=4, max_rounds=1,
    )
    assert result.ok is False
    assert result.plans == {}
    assert any("数量不符" in i for v in result.lint_issues.values() for i in v)
    assert "数量不符" in transport.calls[1]["user"]


async def test_build_detail_batch_gives_up_after_max_rounds() -> None:
    bad = batch(4)
    bad.chapters[1].title = bad.chapters[0].title
    client, _ = make_client([bad.model_dump_json()])

    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=1, batch_end=4, max_rounds=1,
    )
    assert result.ok is False
    assert result.plans == {}
    assert result.lint_issues
    assert "未通过 lint" in (result.error or "")


async def test_build_detail_batch_prompt_carries_previous_exit() -> None:
    """★ 前后锚点：上一章出口状态必须进 prompt（否则章间必然断裂）。"""
    client, transport = make_client([batch(2).model_dump_json()])
    prev = WorldStateSnapshot(
        location_name="长明楼三层", story_time="第三日夜",
        open_thread="陈砚拒绝给李巡长开门",
    )
    await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=5, batch_end=6, previous_exit=prev,
    )
    user = transport.calls[0]["user"]
    assert "拒绝给李巡长开门" in user
    assert "第 5 章到第 6 章" in user


async def test_build_detail_batch_rejects_illegal_range() -> None:
    client, transport = make_client([batch(1).model_dump_json()])
    result = await build_detail_batch(
        client, book_id=1, volume=volume(), bible_text="x",
        batch_start=5, batch_end=3,
    )
    assert result.ok is False
    assert transport.calls == [], "非法区间不得浪费 LLM 调用"


async def test_build_detail_batch_is_idempotent() -> None:
    client, transport = make_client([batch(2).model_dump_json()])
    kw = dict(book_id=1, volume=volume(), bible_text="x",
              batch_start=1, batch_end=2, idempotency_key="detail:1:1")
    r1 = await build_detail_batch(client, **kw)
    r2 = await build_detail_batch(client, **kw)
    assert r1.ok and r2.ok
    assert len(transport.calls) == 1
    assert r2.llm_calls == 0
