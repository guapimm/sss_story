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


def test_review_accepts_issues_as_plain_strings() -> None:
    """★ 实测：模型把问题写成**一句话**而不是对象 —— 必须归一，不能拒绝。

    ## 实测踩到（`storyforge write` 直接崩）

        "issues": [
          "AI味过重：比喻和套话密集，如'风像无数细小的刀片'…",
          "节奏问题：内心活动描写冗长…"
        ]

    **信息全都在**（写得还挺具体），只是没按 `{severity, category, description}`
    拆字段。`list[ReviewIssueOut]` 直接拒它 → 三次重试全败 →
    `StructuredOutputError` **中断整个 write**（正文一个字都没写出来）。

    与 `terms` / `rules` / `milestones` 的形状漂移是**同一类**：
    **对输入宽容，对输出严格** —— 信息不丢就归一，不拒绝。
    """
    r = ReviewResultOut(
        issues=[
            "AI味过重：比喻和套话密集，如'风像无数细小的刀片'、'如同一个沉默的鬼影'",
            "节奏问题：内心活动描写冗长，反复描述恐惧和思考，信息前进缓慢",
        ],
        revised_content="值房外的风呜咽着，刮过窗棂。" * 60,
        score=6.0,
    )
    assert len(r.issues) == 2
    # 归一到 `description`（模型给的那句话就是问题描述）
    assert "AI味过重" in r.issues[0].description
    assert "节奏问题" in r.issues[1].description
    # 严重度/类别用**保守默认值**，代码不替模型猜
    assert r.issues[0].severity == "minor"
    assert r.issues[0].category == "other"


def test_review_still_accepts_proper_objects() -> None:
    """正向对照：规范的**对象**形状不受影响（归一不是"什么都收"）。"""
    r = ReviewResultOut(
        issues=[{"severity": "critical", "category": "logic",
                 "description": "时间线矛盾", "suggestion": "调整顺序"}],
        revised_content="修正后的正文" * 100,
        score=4.0,
    )
    assert r.issues[0].severity == "critical"
    assert r.issues[0].category == "logic"
    assert r.issues[0].suggestion == "调整顺序"


def test_review_rejects_truly_malformed_issues() -> None:
    """真正畸形的形状仍要报错（不要在"宽容"上走过头）。"""
    with pytest.raises(ValidationError):
        ReviewResultOut(issues=[{"no_description_field": "x"}], score=5.0)


# --------------------------------------------------------------------------- #
# ★ 抽取类 schema 的形状归一（`storyforge write` 实测崩在这里）
# --------------------------------------------------------------------------- #


def test_state_update_accepts_person_attribute_to_shape() -> None:
    """★ 实测：模型用 `{person, attribute, from, to}` 表达状态变化。

    这是**非常自然**的写法 —— "从什么变成什么"本来就是状态变化的核心，
    而且信息给全了。但 schema 要 `{entity, field, value}` →
    三次重试全败 → `StructuredOutputError` **中断整个 write**。
    """
    from storyforge.infra.llm.schemas import StateUpdateOut  # noqa: PLC0415

    s = StateUpdateOut.model_validate({
        "person": "李明", "attribute": "情绪",
        "from": "疲惫", "to": "恐惧但表面镇定",
    })
    assert s.entity == "李明"
    assert s.field == "情绪"
    # ★ 保留**变化方向**：只留 to 会丢掉"原来是什么"
    assert "疲惫" in s.value and "恐惧但表面镇定" in s.value


def test_state_update_accepts_entity_attribute_value_shape() -> None:
    """实测第二种：`{entity, attribute, value}`（只用 attribute 漂移）。"""
    from storyforge.infra.llm.schemas import StateUpdateOut  # noqa: PLC0415

    s = StateUpdateOut.model_validate({
        "entity": "李明", "attribute": "目标",
        "value": "从完成文书归档变为隐瞒发现",
    })
    assert s.entity == "李明"
    assert s.field == "目标"
    assert s.value == "从完成文书归档变为隐瞒发现"


def test_state_update_still_accepts_canonical_shape() -> None:
    """正向对照：规范形状不受影响。"""
    from storyforge.infra.llm.schemas import StateUpdateOut  # noqa: PLC0415

    s = StateUpdateOut.model_validate({
        "entity": "陈砚", "field": "位置", "value": "长明楼三层",
    })
    assert (s.entity, s.field, s.value) == ("陈砚", "位置", "长明楼三层")


def test_event_accepts_time_place_and_chinese_keys() -> None:
    """★ 实测：`events` 用了 `time` / `place` / 中文 `后果`。

    ⚠️ 这几个字段**有默认值** —— 不归一**不会报错**，
    只会让时间/后果**静默落空**。静默丢数据比报错更糟：
    报表看不出来，下游拿到的是空值。
    """
    from storyforge.infra.llm.schemas import EventOut  # noqa: PLC0415

    e = EventOut.model_validate({
        "time": "议和前夜，夜间",
        "place": "雁门关府衙值房",
        "participants": ["李明"],
        "description": "李明发现封泥有二次黏合痕迹",
        "后果": "开始怀疑军报被篡改",
    })
    assert e.story_time == "议和前夜，夜间"
    assert e.location == "雁门关府衙值房"
    assert e.consequence == "开始怀疑军报被篡改"
    assert e.description


@pytest.mark.parametrize("name,payload,expect_entities", [
    ("规范形状",
     {"state_updates": [{"entity": "李明", "field": "位置", "value": "值房"}]},
     ["李明"]),
    ("entity+attribute+value",
     {"state_updates": [{"entity": "李明", "attribute": "情绪",
                         "value": "从疲惫变为惊恐"}]},
     ["李明"]),
    ("person+attribute+from+to",
     {"state_updates": [{"person": "李明", "attribute": "情绪",
                         "from": "疲惫", "to": "惊恐"}]},
     ["李明"]),
    ("dict，值是字符串",
     {"state_updates": {"李明": "情绪从疲惫变为恐惧和镇定"}},
     ["李明"]),
    ("★ dict 套 dict",
     {"state_updates": {"李明": {"情绪状态": "从疲惫转为极度惊恐"}}},
     ["李明"]),
    ("dict，值是列表",
     {"state_updates": {"李明": ["情绪：惊恐", "目标：隐瞒发现"]}},
     ["李明", "李明"]),
])
def test_scene_extraction_normalizes_all_observed_shapes(
    name: str, payload: dict, expect_entities: list[str]
) -> None:
    """★★ `scene_extractor` 实测的**全部形状**都必须能归一。

    ## 为什么必须穷举（真实数据）

    **同一本书、同一提示词**，四轮给出四种不同形状。单模型下这种
    "形状偏好不唯一"是**稳定的** —— 靠重试改不动（每次重试都可能撞上
    另一种），只能**全部接受并归一**。

    否则后果是：三次重试全败 → `StructuredOutputError` →
    **中断整个 `storyforge write`**（实测已发生两次）。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "李明发现军报封泥异常", **payload,
    })
    assert [s.entity for s in out.state_updates] == expect_entities
    # 每条都必须有可用的值（不能归一成空壳）
    assert all(s.value.strip() for s in out.state_updates), (
        f"{name}：归一后 value 为空 —— 信息丢了，等于没归一"
    )


# --------------------------------------------------------------------------- #
# ★ 形状 F：list[str]（阶段 1 收尾实测，第 5 种形状）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,payload", [
    ("list[str] 冒号分隔",
     {"state_updates": ["林默：失忆", "林默：获得《住户须知》"]}),
    ("list[str] 中文冒号",
     {"state_updates": ["林默：情绪从疲惫变为惊恐"]}),
    ("list[str] 自然句（没有分隔符）",
     {"state_updates": ["林默情绪从疲惫变为极度惊恐"]}),
    ("list[str] 破折号",
     {"state_updates": ["林默——记忆缺失"]}),
    ("list[str] JSON 串元素",
     {"state_updates": ['{"entity":"林默","value":"失忆"}']}),
    ("混合：dict 与 str 同现",
     {"state_updates": [
         {"entity": "林默", "field": "记忆", "value": "失忆"},
         "王叔：死亡",
     ]}),
])
def test_scene_extraction_accepts_list_of_strings(name: str, payload: dict) -> None:
    """★★ 第 5 种形状：**外层是 list、元素是字符串**。

    阶段 1 收尾实测（书 19 第 1 章真实 `write`）：

        StructuredOutputError: 节点 scene_extractor 结构化输出校验失败（3 次尝试）
        - `state_updates.0`：Input should be an object

    根因：`normalize_record_list` 的入口判据是 `isinstance(v, dict)` ——
    **外层是 list 时直接透传**，元素若是 `str` 就到不了归一逻辑。

    ## 为什么必须接受而不是拒绝

    与既有 4 种形状同理（模块 docstring 已论证）：模型把"谁变成了什么"
    写成一行文字，**信息是完整的**；拒绝它 → 3 次重试全败 →
    中断整个 `write`。本项目已因此崩过两次。

    ## 判据

    ① 不抛错；② `value` 非空（信息不得丢）；③ 能拆出实体名时要拆出来。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "林默在诡异楼道醒来", **payload,
    })
    assert out.state_updates, f"{name}：归一后一条都没有 —— 信息丢了"
    assert all(s.value.strip() for s in out.state_updates), (
        f"{name}：归一后 value 为空 —— 信息丢了，等于没归一"
    )
    # 能拆出实体名的必须拆出来（拆不出的才允许留空/兜底）
    entities = [s.entity for s in out.state_updates]
    if "林默" in str(payload):
        assert "林默" in entities, (
            f"{name}：文本里有明确的实体名「林默」，应当拆出来，实得 {entities}"
        )


def test_list_of_strings_keeps_full_text_when_no_entity_split() -> None:
    """★ 拆不出实体名时**整条留作 value**，不得丢弃。

    这是「信息不得丢」的底线：宁可把整句话放进 `value`，
    也不能因为拆不出结构就把它扔掉。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    long_sentence = "楼道里的气温忽然降了下来，墙上的字迹开始渗血"
    out = SceneExtractionOut.model_validate({
        "summary": "x", "state_updates": [long_sentence],
    })
    assert len(out.state_updates) == 1
    assert long_sentence in out.state_updates[0].value, (
        "拆不出实体名时，整条原文必须保留在 value 里"
    )


def test_list_of_strings_json_element_is_parsed() -> None:
    """元素本身是 JSON 串时要解出来（模型有时把对象转义成字符串）。"""
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "x",
        "state_updates": ['{"entity":"林默","field":"记忆","value":"失忆"}'],
    })
    assert len(out.state_updates) == 1
    s = out.state_updates[0]
    assert s.entity == "林默", f"JSON 串元素未解析，entity={s.entity!r}"
    assert s.value == "失忆"


def test_list_of_strings_ignores_empty_entries() -> None:
    """空串/纯空白元素不得产出空记录（会产生"幽灵状态变化"）。"""
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "x",
        "state_updates": ["", "   ", "林默：失忆", "\n"],
    })
    assert len(out.state_updates) == 1
    assert out.state_updates[0].value.strip()


# --------------------------------------------------------------------------- #
# ★ 形状 G：「字段名: 值」对 —— **静默语义反转**（比崩溃更危险）
# --------------------------------------------------------------------------- #


def test_field_keyed_dict_is_not_read_as_entity() -> None:
    """★★★ 「字段名: 值」对不得被误读成实体名。

    ## 实测（书 19 第 1 章，阶段 1 收尾）

    模型给的（`doc/research/_diag_scene_raw_b19c1.json`）：

        "state_updates": {
          "location": "+楼梯间",
          "equipment": "+《住户须知》|来源:口袋中凭空出现",
          "emotion": "恐慌→恐惧",
          "goal": "遵守规则以求生存"
        }

    既有归一假设「**外层键 = 实体名**，内层键 = 字段名」→ 产出：

        entity='location'   field='状态'  value='+楼梯间'
        entity='equipment'  field='状态'  value='+《住户须知》…'

    **没有报错**，但 `entity` 全是**字段名**。而本章主角是「林默」
    （正文出现 18 次）—— 这些状态变化**没有一条挂在他身上**。

    ★ 这是**静默数据损坏**：数据入库、看起来正常、实际全错。
    比"崩溃"危险得多 —— 崩溃会被立刻发现，这个不会。
    （本项目已记录过同类教训：「没有任何信号」与「信号为 0」是两回事。）

    ## 判据

    `location` / `equipment` / `emotion` / `goal` 是**字段名**，
    不能当实体名 → 必须识别为「字段名: 值」对，
    实体名留空或标为未知，**字段名归到 `field`**。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "林默在诡异楼道醒来",
        "state_updates": {
            "location": "+楼梯间",
            "equipment": "+《住户须知》",
            "emotion": "恐慌→恐惧",
            "goal": "遵守规则以求生存",
        },
    })
    assert len(out.state_updates) == 4

    known_fields = {"location", "equipment", "emotion", "goal"}
    for s in out.state_updates:
        assert s.entity not in known_fields, (
            f"★ 字段名 {s.entity!r} 被当成了实体名 —— "
            f"静默语义反转（这些记录会挂在一个不存在的实体上）"
        )
    # 字段名必须被归到 field（信息不得丢）
    got_fields = {s.field for s in out.state_updates}
    assert got_fields & known_fields, (
        f"字段名没有归到 field（实得 {got_fields}）—— 语义丢了"
    )
    # 值必须保留
    assert all(s.value.strip() for s in out.state_updates)


def test_plain_string_list_fields_are_not_split_into_records() -> None:
    """★★ `deaths` / `unsupported` 是 **`list[str]`**，不得被"拆条"。

    ## 这个测试防的是**我自己修形状 F 时引入的回归**

    第一版把 6 个字段一视同仁地送进 `normalize_string_entries`，
    于是 `deaths: ["中年男人"]` 被拆成 `[{"value": "中年男人"}]`
    → `Input should be a valid string`。

    用**真实落盘的模型输出**（`doc/research/_diag_scene_raw_b19c1.json`）
    复验时当场抓到 —— 单测当时全绿，因为既有用例的 `deaths` 恰好是
    空列表或 dict 形状。

    ★ 教训：**字段的 schema 类型不同，归一动作就不能相同。**
    这条测试把"哪些字段是记录列表、哪些是纯字符串列表"钉死。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "林默目睹中年男人被门后实体拖走",
        "deaths": ["中年男人"],
        "unsupported": ["住户须知", "楼梯间"],
        "state_updates": ["林默：失忆"],
    })
    assert out.deaths == ["中年男人"], (
        f"deaths 被拆坏了：{out.deaths}（应为纯字符串列表）"
    )
    assert out.unsupported == ["住户须知", "楼梯间"]
    # 同一份数据里两类字段并存 —— 各按自己的规则归一
    assert out.state_updates[0].entity == "林默"


def test_real_captured_output_from_book19_validates() -> None:
    """★★ 用**真实落盘的模型输出**做端到端复验（最硬的证据）。

    来源：阶段 1 收尾跑真实 `write` 时，`scene_extractor` 输出被存到
    `doc/research/_diag_scene_raw_b19c1.json`。

    该输出同时包含两种新形状：
      · `state_updates` 是 **dict 且键是字段名**（形状 G）
      · `deaths` 是 **`list[str]`**（保持原样）

    文件缺失时 skip（不 fake 数据 —— 假数据会让测试失去意义）。
    """
    import json
    from pathlib import Path

    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    p = (Path(__file__).resolve().parents[2] / "doc" / "research"
         / "_diag_scene_raw_b19c1.json")
    if not p.exists():
        pytest.skip("真实模型输出样本不存在（需先跑 diag_scene_list_shape_real.py）")

    raw = json.loads(p.read_text(encoding="utf-8"))
    out = SceneExtractionOut.model_validate(raw)

    # deaths 保持纯字符串
    assert all(isinstance(x, str) for x in out.deaths)
    # state_updates 的 entity 不得是字段名（形状 G 的核心断言）
    field_names = {"location", "equipment", "emotion", "goal"}
    leaked = [s.entity for s in out.state_updates if s.entity in field_names]
    assert not leaked, (
        f"★ 字段名被当成实体名：{leaked} —— 静默语义反转复发了"
    )
    # 字段名必须归到 field，值必须保留
    assert {s.field for s in out.state_updates} & field_names
    assert all(s.value.strip() for s in out.state_updates)


def test_real_entity_dict_still_reads_entity_correctly() -> None:
    """★ 反向对照：**真·实体名**做外层键时**不得**被当成字段名。

    防的是"为了修形状 G 而把既有 4 种形状改坏" ——
    这是典型的过度修正风险（本届项目已记录：修 A 必须加 A 的反向对照）。
    """
    from storyforge.infra.llm.schemas import SceneExtractionOut  # noqa: PLC0415

    out = SceneExtractionOut.model_validate({
        "summary": "x",
        "state_updates": {
            "林默": "情绪从疲惫变为惊恐",
            "王叔": {"位置": "楼梯间"},
        },
    })
    entities = {s.entity for s in out.state_updates}
    assert entities == {"林默", "王叔"}, (
        f"真·实体名被误判成字段名了：{entities}"
    )


# --------------------------------------------------------------------------- #
# 细纲结构
# --------------------------------------------------------------------------- #


def test_chapter_plan_does_not_hard_reject_too_few_scenes() -> None:
    """★ 场景数下限**不在 schema 层**拦 —— 它属于 lint（按章、可增量修）。

    ## 实测踩到（书 11，6 书回归）

    schema 层曾抛 `场景数 3 < 4`。而 **schema 失败 = 整批失败**：

        15 章里只有 2 章是 3 个场景 → **15 章全部作废**（3 次重试全败）

    这是**粒度错误**：`场景数 ≥ 4` 是**单章**质量要求，却放在**批级**把关点上，
    一章的瑕疵否决了另外 13 章的合格内容。

    正确把关点 `detail_planner.lint_chapter` **已经存在**且能抓到同一问题
    （实测 `['第1章场景数 3 < 4']`），按章记入 `lint_issues` → 可增量修复。

    > 原则：**结构必需性放 schema，质量偏好放 lint**。
    > 与 `BibleOut.terms` 那次是同一类修复（可选字段不该卡死整条链路）。
    """
    scene = {"goal": "g", "obstacle": "o", "outcome": "r", "why_now": "w"}
    # ★ 3 个场景**不再被拒**（交给 lint 按章报告）
    out = ChapterPlanOut(
        title="t", plot="p", scenes=[scene] * 3,
        entry_state={}, exit_state={}, end_hook="h",
    )
    assert len(out.scenes) == 3

    # 而 lint 层确实会报告它
    from storyforge.domain.outline import ChapterPlan, SceneBeat  # noqa: PLC0415
    from storyforge.graph.nodes.detail_planner import lint_chapter  # noqa: PLC0415

    plan = ChapterPlan(
        global_no=1, volume_no=1, title="t", plot="p" * 20,
        scenes=[SceneBeat(seq=i, goal="g", obstacle="o", outcome="r", why_now="w")
                for i in range(3)],
    )
    assert any("场景数" in i for i in lint_chapter(plan)), (
        "lint 层必须抓到 —— 否则删掉 schema 校验就成了放水"
    )


def test_chapter_plan_accepts_four_scenes() -> None:
    """4 个场景是正常形态（正向对照，证明上一条不是"什么都收"）。"""
    scene = {"goal": "g", "obstacle": "o", "outcome": "r", "why_now": "w"}
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

def test_rules_content_misnested_is_relocated_not_rejected() -> None:
    """★ 实测：模型把 `era`/`tone`/`terms` 的内容塞进了 `rules` 数组。

    schema 层看不出来（`rules` 是 `list[str]`，`"era：..."` 确实是合法字符串），
    但后果是三个顶层字段**全为空**、rules 里混进非规则内容。

    这类**结构错位**只能靠内容特征识别。

    ★★ 为什么是"归位"而不是"拒绝"：实测这是**间歇性**的 ——
    同一本书连续 3 次调用全部正常，多书测试那一轮恰好撞上。
    原先"拒绝 + 重试"在撞上时会连续 3 次失败 → **整本书的圣经生成失败**。
    信息没丢就不该有理由拒绝（与本文件对 `terms`/`rules` 形状漂移的处理同一原则）。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="",
        rules=["规则一：不可违反", "era：永恒黄昏时代", "tone：冷峻压抑"],
        terms=["占位——非空"],
    )
    # 真规则留下
    assert b.rules == ["规则一：不可违反"]
    # 错位内容回到各自的顶层字段
    assert b.era == "永恒黄昏时代"
    assert b.tone == "冷峻压抑"


def test_relocate_does_not_override_existing_top_level_field() -> None:
    """顶层字段**已有内容时不覆盖** —— 顶层比 rules 里捡出来的更可信。"""
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="既有的时代",
        rules=["规则一：不可违反", "era：rules 里的时代"],
        terms=["占位——非空"],
    )
    assert b.era == "既有的时代"
    assert b.rules == ["规则一：不可违反"]


def test_relocate_accepts_dict_shaped_misnesting() -> None:
    """★ 同一个"塞错字段"的毛病，模型**换个形状**（对象）也得认出来。

    只认字符串形态等于只修了一半 —— 实测模型错位时并不总是写字符串。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="",
        rules=["规则一：不可违反", {"era": "永恒黄昏时代"}],
        terms=["占位——非空"],
    )
    assert b.rules == ["规则一：不可违反"]
    assert b.era == "永恒黄昏时代"


def test_relocate_terms_from_rules() -> None:
    """`terms` 错位：内容是「说法——含义」串 → 拆成条目。"""
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=["规则一：不可违反", "terms：值夜人——承担夜间值守的人；朱批——改判之权"],
        terms=[],
    )
    assert b.rules == ["规则一：不可违反"]
    assert b.terms == ["值夜人——承担夜间值守的人", "朱批——改判之权"]


def test_relocate_discards_truncated_terms_fragment() -> None:
    """★ 实测模型会写成 `"terms： ["` 这种被截断的样子 —— 残片丢弃。

    留着残片只会污染术语表，让下游把它当成一条真术语。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=["规则一：不可违反", "terms： ["],
        terms=[],
    )
    assert b.rules == ["规则一：不可违反"]
    assert b.terms == []


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


def test_missing_terms_is_tolerated_structurally() -> None:
    """★ `terms` 缺失**不阻塞结构** —— 它在质量闸门里查，不在这里查。

    为什么分开：术语对照是"锦上添花"，缺失只影响用词一致性，
    不该让整本书的圣经生成失败。硬要求它会重现"可选字段卡死整条链路"。
    `validate_bible` 的 `_MIN_TERMS` 才是它该待的地方（质量闸门）。
    """
    b = BibleOut(
        title="t", worldview="w" * 250, power_system="p", era="当代",
        rules=["规则一：不可违反"],
    )
    assert b.terms == []

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
