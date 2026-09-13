"""Schema 约束测试 —— 每个断言都对应旧项目的一次实测事故。

Schema 是 LLM 结构化输出的**唯一契约**。这些测试保证契约不会静默放宽。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from storyforge.domain.enums import (
    BeatType,
    EntityKind,
    InjurySeverity,
    InjuryStatus,
    RelationKind,
)
from storyforge.infra.llm.schemas import (
    ChapterPlanOut,
    DetailBatchOut,
    EntityMentionOut,
    InjuryUpdateOut,
    RelationUpdateOut,
    ReviewResultOut,
    SceneBeatOut,
    SeriesPlanOut,
    VolumePlanOut,
    WorldStateOut,
)

pytestmark = pytest.mark.unit


def _vol(**kw) -> dict:
    base = dict(
        volume_no=1, title="卷一", goal="目标",
        new_locations=["青云城", "落霞谷"],
        new_factions=["云岚宗", "七夜社"],
        antagonist="大反派", power_ceiling="金丹期",
        core_conflict="夺宝", escalation_from_prev="从村斗升级到城战",
        old_map_closure="旧城收尾", chapter_count=200,
    )
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# ★ 枚举单一来源（旧项目 severity 三方不一致）
# --------------------------------------------------------------------------- #


def test_entity_kind_description_includes_faction() -> None:
    """★「势力」必须在 schema description 里出现。

    旧项目三套词汇表冲突（`entities.py` 有组织无势力、`models.py` 有势力、
    `graph.py` 都无）→ LLM 吐 `type="势力"` 被判非法 → **静默降级「其他」**
    → 966 条关系里 `type_b='势力'` 只剩 **7 条**。
    """
    desc = EntityMentionOut.model_fields["kind"].description or ""
    for kind in EntityKind:
        assert kind.value in desc, f"schema description 缺枚举值「{kind.value}」"


def test_relation_kind_description_complete() -> None:
    """关系类型必须全部出现在指令里（旧项目是自由文本 → 191 种标签）。"""
    desc = RelationUpdateOut.model_fields["kind"].description or ""
    for k in RelationKind:
        assert k.value in desc, f"缺关系类型「{k.value}」"


def test_beat_type_includes_heal() -> None:
    """★ `BeatType` 必须含 heal / recovery_prep。

    旧项目 `VALID_BEATS = {"normal","boss","climax","emotion","recall","turning"}`
    **没有 heal** → 恢复不是合法节拍类型 → 细纲规划者无法把疗伤排成一章的任务
    → 这是伤势恢复率 0% 的直接原因之一。
    """
    assert BeatType.HEAL.value in BeatType._value2member_map_
    desc = ChapterPlanOut.model_fields["beat_type"].description or ""
    assert BeatType.HEAL.value in desc
    assert BeatType.RECOVERY_PREP.value in desc


def test_injury_severity_is_five_levels() -> None:
    """★ 损伤严重度必须是 5 档（旧项目 prompt 只给 3 档 → 最重的伤不催）。"""
    assert len(list(InjurySeverity)) == 5
    desc = InjuryUpdateOut.model_fields["severity"].description or ""
    for s in InjurySeverity:
        assert s.value in desc, f"缺严重度「{s.value}」"


def test_injury_status_description_complete() -> None:
    desc = InjuryUpdateOut.model_fields["status"].description or ""
    for s in InjuryStatus:
        assert s.value in desc


# --------------------------------------------------------------------------- #
# ★ 卷级升级台阶（对策②核心）
# --------------------------------------------------------------------------- #


def test_series_requires_all_volumes() -> None:
    """★ 声明 N 卷就必须给 N 卷的台阶。

    旧项目「本次只细化第 1 卷」+ 后续卷补占位 → **400 章全挤第 1 卷**，
    而第 1 卷 vol_goal 是「在一栋楼里读透三十七条」。
    """
    with pytest.raises(ValidationError, match="total_volumes|卷"):
        SeriesPlanOut(
            logline="x", total_volumes=3, target_chapters=600,
            volumes=[VolumePlanOut(**_vol())],
        )


def test_series_accepts_matching_volume_count() -> None:
    plan = SeriesPlanOut(
        logline="x", total_volumes=2, target_chapters=400,
        volumes=[
            VolumePlanOut(**_vol()),
            VolumePlanOut(**_vol(volume_no=2, new_locations=["新地A", "新地B"],
                                 new_factions=["新势力A", "新势力B"],
                                 antagonist="第二个反派")),
        ],
    )
    assert len(plan.volumes) == 2


def test_volume_requires_new_locations() -> None:
    """★ 新地图必填 —— 防「全书困在一处」。

    实测地理熵：书 24/23/18 的**后半本新增地点 0/1/5**。
    """
    with pytest.raises(ValidationError):
        VolumePlanOut(**_vol(new_locations=[]))


def test_volume_requires_new_factions() -> None:
    """★ 新势力必填 —— 旧项目势力实体实测 **0 个**。"""
    with pytest.raises(ValidationError):
        VolumePlanOut(**_vol(new_factions=[]))


def test_volume_escalation_must_be_specific() -> None:
    """升级说明必填（非空）。模板句由 `structure_gate` 进一步拦截。"""
    with pytest.raises(ValidationError):
        VolumePlanOut(**_vol(escalation_from_prev=""))
    # 模板句本身能过 schema，但会被闸门拦（分层防御）
    v = VolumePlanOut(**_vol(escalation_from_prev="推向「X」的下一个阶段"))
    assert v.escalation_from_prev


# --------------------------------------------------------------------------- #
# ★ 审校：issues 非空必须给修订稿
# --------------------------------------------------------------------------- #


def test_review_requires_revision_when_issues_present() -> None:
    """★ 旧项目重写环节空转的防线。

    实测：`no_revised_content`、`too_short:3839<4600`、`no_improve:6->6`
    → Editor 反复要求修改但从不产出可用修订稿 → 原稿入库。

    ⚠️ 这个校验**必须用 `model_validator`** —— 用 `field_validator` 会因
    Pydantic 的字段校验顺序（`revised_content` 声明在 `issues` 之前）
    而静默失效。实测踩过这个坑。
    """
    with pytest.raises(ValidationError, match="revised_content"):
        ReviewResultOut(
            issues=[{"severity": "major", "category": "ai_style", "description": "套话密集"}],
            score=5.0,
        )


def test_review_accepts_empty_issues_without_revision() -> None:
    r = ReviewResultOut(issues=[], score=8.5)
    assert r.has_usable_revision is False
    assert r.score == 8.5


def test_review_accepts_issues_with_revision() -> None:
    r = ReviewResultOut(
        issues=[{"severity": "major", "category": "pacing", "description": "节奏拖沓"}],
        revised_content="完整修正后的正文" * 100,
        score=7.0,
    )
    assert r.has_usable_revision


def test_review_rejects_whitespace_only_revision() -> None:
    """只有空白的"修订稿"不算修订稿。"""
    with pytest.raises(ValidationError):
        ReviewResultOut(
            issues=[{"severity": "minor", "category": "other", "description": "x"}],
            revised_content="   \n  ",
            score=7.0,
        )


def test_review_score_range_enforced() -> None:
    with pytest.raises(ValidationError):
        ReviewResultOut(issues=[], score=11.0)
    with pytest.raises(ValidationError):
        ReviewResultOut(issues=[], score=-1.0)


# --------------------------------------------------------------------------- #
# 细纲结构
# --------------------------------------------------------------------------- #


def test_chapter_plan_requires_min_four_scenes() -> None:
    """细纲要求 4~8 个分场景拍点（继承旧项目已验证格式）。"""
    scene = {"goal": "g", "obstacle": "o", "outcome": "r", "why_now": "w"}
    with pytest.raises(ValidationError, match="场景数"):
        ChapterPlanOut(
            title="t", plot="p", scenes=[scene] * 3,
            entry_state={}, exit_state={}, end_hook="h",
        )
    ok = ChapterPlanOut(
        title="t", plot="p", scenes=[scene] * 4,
        entry_state={}, exit_state={}, end_hook="h",
    )
    assert len(ok.scenes) == 4


def test_scene_beat_requires_all_four_fields() -> None:
    """每拍必须 goal/obstacle/outcome/why_now 齐全（防遗漏 + 防无动机跳转）。"""
    for missing in ("goal", "obstacle", "outcome", "why_now"):
        payload = {"goal": "g", "obstacle": "o", "outcome": "r", "why_now": "w"}
        payload.pop(missing)
        with pytest.raises(ValidationError):
            SceneBeatOut(**payload)


def test_world_state_is_structured_not_string() -> None:
    """★ entry/exit state 必须是结构化对象。

    旧项目是自由文本字符串（`architect.py:108-113`），实测填充率**仅 13%**
    （段1 100% → 段2 12% → 段3起**归零**），且无法机械校验。
    """
    st = WorldStateOut(
        location="长明楼三层值班室", story_time="第三日夜",
        present=["陈砚", "郑维"], known=["陈砚知道手册缺一页"],
        open_thread="门外有人唤名，陈砚未应答",
    )
    assert st.location and st.present and st.open_thread
    # 空构造也应可用（字段都有默认）
    assert WorldStateOut().location == ""


def test_detail_batch_holds_chapters() -> None:
    scene = {"goal": "g", "obstacle": "o", "outcome": "r", "why_now": "w"}
    batch = DetailBatchOut(
        chapters=[
            dict(title="t1", plot="p1", scenes=[scene] * 4,
                 entry_state={}, exit_state={}, end_hook="h1"),
            dict(title="t2", plot="p2", scenes=[scene] * 5,
                 entry_state={}, exit_state={}, end_hook="h2"),
        ]
    )
    assert len(batch.chapters) == 2


# --------------------------------------------------------------------------- #
# 抽取 schema
# --------------------------------------------------------------------------- #


def test_relation_intensity_bounded() -> None:
    with pytest.raises(ValidationError):
        RelationUpdateOut(from_entity="A", to_entity="B", kind="敌对", intensity=150)


def test_relation_has_tension_dimensions() -> None:
    """关系必须能表达张力（旧项目只有名词标签 + 一个 visibility 布尔量）。"""
    r = RelationUpdateOut(
        from_entity="陈砚", to_entity="老周", kind="盟友",
        surface_facing="前辈与后辈", true_facing="把他当棋子",
        is_mutual=False, intensity=30,
    )
    assert r.surface_facing != r.true_facing
    assert r.is_mutual is False


def test_entity_mention_has_intro_method() -> None:
    """引入方式必须可标（供「无依据新名词」检测）。"""
    m = EntityMentionOut(
        name="陆知白", kind="人物",
        intro_method="narrator_intro", context="这人名叫陆知白",
    )
    assert m.intro_method == "narrator_intro"


# --------------------------------------------------------------------------- #
# schema → prompt 生成（单一来源的落地）
# --------------------------------------------------------------------------- #


def test_schemas_are_json_serializable() -> None:
    """所有 schema 必须能生成 JSON Schema（供 prompt 与 provider 使用）。"""
    import json

    for cls in (SeriesPlanOut, VolumePlanOut, ChapterPlanOut, ReviewResultOut):
        s = cls.model_json_schema()
        assert "properties" in s
        assert len(json.dumps(s, ensure_ascii=False)) > 100


def test_field_descriptions_are_present() -> None:
    """关键字段必须有 description —— 它就是给模型的指令。

    旧项目 `post_processor.py` 的单条 system prompt 超过 10,000 字符，
    把约束写在散文里；本设计把约束挂在字段上，两者同源。
    """
    for cls in (ChapterPlanOut, SceneBeatOut, WorldStateOut, VolumePlanOut):
        for name, field in cls.model_fields.items():
            assert field.description, f"{cls.__name__}.{name} 缺 description"
