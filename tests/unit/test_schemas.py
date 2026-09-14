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
    BibleOut,
    ChapterPlanOut,
    DetailBatchOut,
    EntityMentionOut,
    InjuryUpdateOut,
    MilestoneOut,
    RelationUpdateOut,
    ReviewResultOut,
    SceneBeatOut,
    SeriesPlanOut,
    VolumePlanOut,
    WorldStateOut,
    coerce_str_list,
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

# --------------------------------------------------------------------------- #
# 形状归一（★ 实测驱动：真实运行时模型反复用这些形状）
# --------------------------------------------------------------------------- #


def test_terms_accepts_dict_shape() -> None:
    """★ 实测：prompt 说「格式：世界内说法——含义」，模型就给字典。

    这是**完全合理**的读法（键=说法，值=含义），信息量一模一样。
    旧行为是三次重试全部失败、节点放弃 —— 白烧 token 而内容其实是对的。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=["r" * 10],
        terms={"值夜人": "承担夜间值守的人", "朱批": "以血为墨改判"},
    )
    assert b.terms == ["值夜人——承担夜间值守的人", "朱批——以血为墨改判"]


def test_terms_accepts_object_list_shape() -> None:
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代", rules=["r" * 10],
        terms=[{"term": "值夜人", "meaning": "值守的人"}],
    )
    assert b.terms == ["值夜人——值守的人"]


def test_terms_accepts_plain_list_unchanged() -> None:
    """规范形状必须原样通过（归一不得改动正确输入）。"""
    b = BibleOut(title="t", worldview="w" * 250, power_system="p", era="当代",
                 rules=["r" * 10], terms=["值夜人——值守的人"])
    assert b.terms == ["值夜人——值守的人"]


def test_terms_none_becomes_empty() -> None:
    b = BibleOut(title="t", worldview="w" * 250, power_system="p", era="当代",
                 rules=["r" * 10], terms=["占位——保持 terms 非空"])
    assert isinstance(b.terms, list)


def test_coerce_str_list_handles_term_without_meaning() -> None:
    assert coerce_str_list([{"term": "孤词"}]) == ["孤词"]


def test_coerce_str_list_passes_through_non_list() -> None:
    """非列表非字典 → 原样返回，交给 Pydantic 正常报错（**不吞掉真错误**）。"""
    assert coerce_str_list(42) == 42


@pytest.mark.parametrize("raw,expect", [
    (8, 8),
    ("8", 8),
    ("第6章：潜入上海，追踪知识窃取者踪迹", 6),
    ("第 12 章 最终对决", 12),
    ("13", 13),
])
def test_milestone_accepts_bare_and_string_forms(raw, expect) -> None:
    """★ 实测：模型**两次**都把里程碑写成「第N章：描述」。

    那不是模型犯错 —— 那是中文小说里最自然的写法，而且它把章号和描述
    一起给了，信息比规范形式还全。只认一种写法会让整条规划链路失败。
    """
    m = MilestoneOut.model_validate(raw)
    assert m.chapter_no == expect


def test_milestone_keeps_description_from_string_form() -> None:
    m = MilestoneOut.model_validate("第6章：潜入上海，追踪知识窃取者踪迹")
    assert "潜入上海" in m.description
    assert "第6章" not in m.description, "章号前缀应当被剥掉"


def test_milestone_key_defaults_empty_and_code_fills_it() -> None:
    """标识符由**代码**生成，不要求模型编（D-15：模型给局部、代码算全局）。"""
    assert MilestoneOut.model_validate(8).key == ""


def test_volume_plan_accepts_field_name_drift() -> None:
    """★ 实测：模型给 `volume_number` 而非 `volume_no`（同义命名）。

    单模型下这类命名偏好是**稳定**的 —— 反复重试改不动，只会失败到底。
    """
    v = VolumePlanOut.model_validate({
        "volume_number": 1, "title": "t", "goal": "g",
        "new_locations": ["a", "b"], "new_factions": ["c", "d"],
        "antagonist": "x", "power_ceiling": "p", "core_conflict": "c",
        "escalation_from_prev": "从村斗升级为城战",
        "old_map_closure": "收尾", "chapter_count": 8,
        "milestones": [3, 6],
    })
    assert v.volume_no == 1
    assert [m.chapter_no for m in v.milestones] == [3, 6]


def test_volume_plan_still_rejects_truly_missing_fields() -> None:
    """★ 归一只做**形状**转换，不补真正缺失的字段。

    把缺字段也"补"上会让校验器失去意义 —— 那比直接失败更危险。
    """
    with pytest.raises(ValidationError):
        VolumePlanOut.model_validate({
            "volume_number": 1, "title": "t", "goal": "g",
            "new_factions": ["c", "d"],           # 缺 new_locations
            "antagonist": "x", "power_ceiling": "p", "core_conflict": "c",
            "escalation_from_prev": "从村斗升级为城战",
            "old_map_closure": "收尾", "chapter_count": 8,
        })


def test_series_plan_accepts_drifted_target_chapters_name() -> None:
    """`target_total_chapters` → `target_chapters`（实测出现）。"""
    out = SeriesPlanOut.model_validate({
        "logline": "x", "total_volumes": 1,
        "target_total_chapters": 25,
        "volumes": [{
            "volume_number": 1, "title": "t", "goal": "g",
            "new_locations": ["a", "b"], "new_factions": ["c", "d"],
            "antagonist": "x", "power_ceiling": "p", "core_conflict": "c",
            "escalation_from_prev": "从村斗升级为城战",
            "old_map_closure": "收尾", "chapter_count": 25,
        }],
    })
    assert out.target_chapters == 25
    assert out.total_volumes == 1

def test_milestone_string_form_not_shadowed_by_volume_normalizer() -> None:
    """★ 回归：卷级归一化**不得遮蔽**里程碑的字符串解析。

    实测踩到：`VolumePlanOut` 的归一器把里程碑字符串预先包成
    `{"chapter_no": "第6章：…"}`，于是 `MilestoneOut` 的字符串校验器
    拿到的是 dict 而不是 str —— 解析照样失败。

    教训：**归一化层之间不能互相遮蔽**，能自己处理的形状就原样传递。
    """
    from storyforge.infra.llm.schemas import VolumePlanOut as _V

    v = _V.model_validate({
        "volume_number": 2, "title": "t", "goal": "g",
        "new_locations": ["a", "b"], "new_factions": ["c", "d"],
        "antagonist": "x", "power_ceiling": "p", "core_conflict": "c",
        "escalation_from_prev": "从馆内升级为跨城博弈",
        "old_map_closure": "收尾", "chapter_count": 80,
        "milestones": [
            "第55章：在现实锚点中寻找改写第三条规则的机会。",
            "第80章：结局。值夜人成为传说，留下神秘注脚。卷终。",
        ],
    })
    assert [m.chapter_no for m in v.milestones] == [55, 80]
    assert "现实锚点" in v.milestones[0].description

def test_rules_reject_other_field_content() -> None:
    """★ 实测：模型把 `era`/`tone`/`terms` 的内容塞进了 `rules` 数组。

    schema 层看不出来（`rules` 是 `list[str]`，`"era：..."` 确实是合法字符串），
    但后果是三个顶层字段**全为空**、rules 里混进非规则内容。

    这类**结构错位**只能靠内容特征识别。
    """
    with pytest.raises(ValidationError, match="其他字段的内容"):
        BibleOut(
            title="t", worldview="w" * 250, power_system="p", era="当代",
            rules=["规则一：不可违反", "era：永恒黄昏时代", "tone：冷峻压抑"],
            terms=["占位——非空"],
        )


def test_rules_accept_normal_content() -> None:
    """真规则不得被误伤（真规则不会以 "era：" 开头）。"""
    rules = [
        "规则一旦写入手册便不可擦除，只能覆盖",
        "午夜后应答唤名者视为交接完成",
        "提到 era 这个词也不算错位",
    ]
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=rules, terms=["占位——非空"],
    )
    assert b.rules == rules


def test_missing_era_is_rejected() -> None:
    """★ 错位后 `era` 会为空 —— 「缺省即通过」的反面：空字段必须报错。"""
    with pytest.raises(ValidationError, match="era"):
        BibleOut(
            title="t", worldview="w" * 250, power_system="p",
            rules=["规则一：不可违反"], terms=["占位——非空"],
        )


def test_missing_terms_is_rejected() -> None:
    with pytest.raises(ValidationError, match="terms"):
        BibleOut(
            title="t", worldview="w" * 250, power_system="p", era="当代",
            rules=["规则一：不可违反"],
        )

def test_rules_accepts_object_item_shape() -> None:
    """★ 实测（书 11 第 2 轮）：`rules` 里混进了一个对象，3 次重试全失败。

        "rules": [..., {"rule": "…", "exception": "…"}]   ← 第 17 项

    模型写到最后几条时"想补充细节"，把一条规则写成带子字段的对象。
    `list[str]` 直接拒它 → **整本书的圣经生成失败**。

    与 `terms` 同源的修法：归一成文本，而不是拒绝。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=[
            "规则一：不可违反",
            {"规则": "午夜后不得回应唤名", "例外": "持手册者除外"},
        ],
        terms=["占位——非空"],
    )
    assert b.rules[0] == "规则一：不可违反"
    # 多字段对象拼成「键：值；键：值」—— 那是一条规则的多个侧面
    assert "午夜后不得回应唤名" in b.rules[1]
    assert "持手册者除外" in b.rules[1]


def test_coerce_str_list_single_field_dict_keeps_value() -> None:
    """单字段 dict：**值**才是内容，键只是标签。

    （`[{"term": "孤词"}]` 这种写法里，"term" 不是内容。）
    """
    assert coerce_str_list([{"term": "孤词"}]) == ["孤词"]
    assert coerce_str_list([{"规则": "不得越线"}]) == ["不得越线"]


def test_coerce_str_list_top_level_dict_stays_one_per_pair() -> None:
    """★ 顶层字典 = 多项对照，每个键值对是**一条**（terms 的天然写法）。

    这与「列表里的多字段对象」（一条规则的多个侧面）是**两种不同形状**，
    不能混为一谈 —— 实测踩到过。
    """
    assert coerce_str_list({"值夜人": "值守的人", "朱批": "改判之权"}) == [
        "值夜人——值守的人", "朱批——改判之权",
    ]
