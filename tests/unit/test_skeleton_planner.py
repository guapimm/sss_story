"""骨架生成节点测试（P3-3 / 人工确认点 C-2）。

## 本文件防的是什么

旧项目 `ultra_structure_config.json` 的指标**设计正确**：

    { "min_arcs": 6, "min_core_arcs": 2, "min_relationship_arcs": 2,
      "min_opposition_arcs": 2, "min_characters": 3, "min_factions": 4,
      "enabled": false, "shadow": true }

**但它 `enabled: false, shadow: true` —— 从不阻断。**
于是书 23 以「400 章全在第 1 卷、4 个里程碑、**0 个势力实体**」的状态写完了 400 章。

> 设计对了却关着的校验器，等于没有。

本文件把同一组指标**打开**，并逐个验证它会拦住旧项目的真实失败形态：
  · 势力数量不足（旧项目势力实体实测 **0** 个）
  · 段落目标逐字重复（旧项目实测 6 段同句 → 29 个重复章节标题覆盖 65/300 章）
  · 骨架不落实卷级台阶的新地图/新势力（台阶变纸面规划 → 全书困在一处）
"""

from __future__ import annotations

import pytest

from storyforge.domain.outline import Milestone, VolumePlan
from storyforge.graph.nodes.skeleton_planner import (
    MIN_ARCS,
    MIN_FACTIONS,
    build_skeleton,
    load_skeleton,
    persist_skeleton,
    validate_skeleton,
)
from storyforge.infra.llm.client import (
    CallContext,
    ClientConfig,
    LLMClient,
    MemoryIdempotencyStore,
)
from storyforge.infra.llm.schemas import ArcOut, SkeletonOut
from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore

pytestmark = pytest.mark.unit


def good_skeleton(**over) -> SkeletonOut:
    """一个通过校验的骨架（6 线 = 2 主线 + 2 关系线 + 2 对立线）。"""
    base = dict(
        arcs=[
            ArcOut(key="十三条", kind="core",
                   description="陈砚逐条验证长明楼残页上的十三条规则", chapter_span=[1, 60]),
            ArcOut(key="继任", kind="core",
                   description="陈砚接过值夜簿，成为新的值夜人", chapter_span=[5, 120]),
            ArcOut(key="周德海", kind="relationship",
                   description="陈砚与周德海从长明楼内的互相提防，到旧城区闸口外以命相托",
                   chapter_span=[3, 90]),
            ArcOut(key="裴照", kind="relationship",
                   description="裴照隔门传话，两人由试探转为同盟", chapter_span=[10, 110]),
            ArcOut(key="守夜会", kind="opposition",
                   description="守夜会要封死长明楼，赌注是楼内所有住户的性命",
                   chapter_span=[20, 150]),
            ArcOut(key="十三层", kind="opposition",
                   description="十三层的存在与陈砚的记忆对抗，输了就忘记自己是谁",
                   chapter_span=[40, 180]),
        ],
        segments=[
            "接手值夜，摸清楼内规矩",
            "头一桩委托与住户的隐瞒",
            "守夜会介入，封锁前夜",
            "十三层的门第一次开启",
        ],
        characters=["陈砚", "周德海", "裴照", "老梁"],
        factions=["守夜会", "档案科", "旧城区房客互助", "十三层住户"],
    )
    base.update(over)
    return SkeletonOut(**base)


def make_volume(**over) -> VolumePlan:
    base = dict(
        volume_no=1,
        title="值夜",
        new_locations=["长明楼", "旧城区闸口"],
        new_factions=["守夜会", "档案科"],
        antagonist="守夜会执事",
        power_ceiling="只能覆盖条文，不能新增",
        core_conflict="留下还是封楼",
        escalation_from_prev="从旁观者变为值夜人本人",
        old_map_closure="旧城区的租房彻底退掉",
        chapter_start=1,
        chapter_end=200,
        milestones=[Milestone(chapter_no=1, key="m0"), Milestone(chapter_no=50, key="m1")],
        goal="接手值夜",
    )
    base.update(over)
    return VolumePlan(**base)


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
# 零 LLM 校验 —— 逐个复现旧项目的失败形态
# --------------------------------------------------------------------------- #


def test_good_skeleton_passes() -> None:
    assert validate_skeleton(good_skeleton(), make_volume()) == []


def test_validation_without_volume_still_checks_counts() -> None:
    """不带卷台阶时，规模类校验仍必须生效（台阶是额外约束，不是替代品）。"""
    assert validate_skeleton(good_skeleton()) == []
    assert validate_skeleton(good_skeleton(factions=["只此一家"])) != []


def test_too_few_arcs_rejected() -> None:
    """★ min_arcs: 6 —— 旧项目配置里有，但 `enabled: false` 从不生效。"""
    issues = validate_skeleton(
        good_skeleton(arcs=good_skeleton().arcs[:4]), make_volume()
    )
    assert any(f"要求 ≥{MIN_ARCS}" in i for i in issues)


def test_missing_relationship_arcs_rejected() -> None:
    """★ min_relationship_arcs: 2 —— 关系线缺失是"人物关系一维化"的结构成因。"""
    arcs = [a for a in good_skeleton().arcs if a.kind != "relationship"]
    issues = validate_skeleton(good_skeleton(arcs=arcs), make_volume())
    assert any("关系线只有 0 条" in i for i in issues)


def test_missing_opposition_arcs_rejected() -> None:
    """★ min_opposition_arcs: 2 —— 没有对立线就没有赌注。"""
    arcs = [a for a in good_skeleton().arcs if a.kind != "opposition"]
    issues = validate_skeleton(good_skeleton(arcs=arcs), make_volume())
    assert any("对立线只有 0 条" in i for i in issues)


def test_too_few_factions_rejected() -> None:
    """★ 旧项目实测**势力实体 0 个**：势力不是合法实体类型，
    `type_b='势力'` 只有 7 行。结局是全书只有主角和几个散人。"""
    issues = validate_skeleton(
        good_skeleton(factions=["守夜会", "档案科"]), make_volume()
    )
    assert any(f"要求 ≥{MIN_FACTIONS}" in i for i in issues)


def test_too_few_characters_rejected() -> None:
    issues = validate_skeleton(good_skeleton(characters=["陈砚"]), make_volume())
    assert any("主要角色只有 1 个" in i for i in issues)


def test_duplicate_segment_goals_rejected() -> None:
    """★ 旧项目实测：模型把 6 段目标写成同一句话
    → 29 个重复章节标题覆盖 65/300 章。"""
    dup = "推进主线剧情"
    issues = validate_skeleton(
        good_skeleton(segments=[dup, dup, dup]), make_volume()
    )
    assert any("逐字重复" in i for i in issues)


def test_arc_near_duplicate_rejected() -> None:
    """★ 模型用**换前缀/加后缀**凑线数。

    旧项目为此专门引入 `_arc_core_key`：`主线·X` → `X主线` → `X（核心）`
    在精确名比较下是三条线，在核心名比较下是同一件事。
    """
    arcs = good_skeleton().arcs + [
        ArcOut(key="主线·十三条", kind="sub", description="重复的线，描述够长",
               chapter_span=[1, 10]),
    ]
    issues = validate_skeleton(good_skeleton(arcs=arcs), make_volume())
    assert any("近重复" in i for i in issues)


def test_thin_arc_description_rejected() -> None:
    arcs = good_skeleton().arcs[:5] + [
        ArcOut(key="凑数的线", kind="sub", description="待定", chapter_span=[1, 5]),
    ]
    issues = validate_skeleton(good_skeleton(arcs=arcs), make_volume())
    assert any("描述过短" in i for i in issues)


# --------------------------------------------------------------------------- #
# ★ 台阶落地校验 —— 骨架必须落实本卷扩张
# --------------------------------------------------------------------------- #


def test_skeleton_must_include_new_factions() -> None:
    """★ 这是「全书困在一处」的**第二道**闸门。

    第一道在 `volume_planner`（台阶必须写 new_factions）；
    但若骨架不落实，台阶就只是纸面规划，细纲照样排不出新势力。
    """
    issues = validate_skeleton(
        good_skeleton(factions=["档案科", "旧城区房客互助", "十三层住户", "闲散人"]),
        make_volume(new_factions=["守夜会", "第九处"]),
    )
    assert any("未落实本卷台阶指定的新势力" in i for i in issues)
    assert any("第九处" in i for i in issues)


def test_skeleton_must_reference_new_locations() -> None:
    """★ 新地图必须在骨架的线/段落里有对应安排，否则细纲不会安排换场。

    旧项目实测地理熵：book 24 后半本新增地点 **0**、book 23 **1**。
    """
    sk = good_skeleton(
        arcs=[
            ArcOut(key=a.key, kind=a.kind,
                   description=a.description.replace("长明楼", "老档案馆"),
                   chapter_span=a.chapter_span)
            for a in good_skeleton().arcs
        ],
        segments=["接手值夜", "头一桩委托", "封锁前夜", "门第一次开启"],
    )
    issues = validate_skeleton(sk, make_volume(new_locations=["西郊水塔"]))
    assert any("未提及本卷新地图" in i for i in issues)
    assert any("西郊水塔" in i for i in issues)


async def test_build_rejects_volume_missing_escalation() -> None:
    """★ 台阶缺扩张字段 → 不调 LLM，直接失败。

    旧项目正是「台阶没有扩张字段」+「校验器关着」两者叠加，
    才产出 400 章全在一栋楼里的结果。
    """
    client, transport = make_client([good_skeleton().model_dump_json()])
    result = await build_skeleton(
        client, book_id=1, volume=make_volume(new_factions=[]), bible_text="【世界观】x"
    )
    assert result.ok is False
    assert "new_locations / new_factions" in (result.error or "")
    assert transport.calls == [], "台阶不合法时不得浪费一次 LLM 调用"


# --------------------------------------------------------------------------- #
# 落盘 / 读取
# --------------------------------------------------------------------------- #


def test_persist_and_load_roundtrip(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ref = persist_skeleton(store, 7, 2, good_skeleton())
    assert ref == "books/7/skeleton/v002.json"

    loaded = load_skeleton(store, 7, 2)
    assert len(loaded.arcs) == 6
    assert loaded.factions[0] == "守夜会"


def test_load_missing_skeleton_raises(tmp_path) -> None:
    """★ 读不到骨架必须抛错。

    旧项目 `books.bible`/蓝图缺失时静默降级 → 后续节点在**空结构**上继续跑，
    产出 400 章却没有结构，且全程无报错。
    """
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFound):
        load_skeleton(store, 999, 1)


# --------------------------------------------------------------------------- #
# 节点调用
# --------------------------------------------------------------------------- #


async def test_build_skeleton_success() -> None:
    client, _ = make_client([good_skeleton().model_dump_json()])
    result = await build_skeleton(
        client, book_id=1, volume=make_volume(), bible_text="【世界观】长明楼……"
    )
    assert result.ok, result.error
    assert result.skeleton is not None
    assert result.llm_calls == 1
    assert result.violations == []


async def test_build_skeleton_prompt_carries_escalation_requirements() -> None:
    """★ 台阶的扩张要求必须进 prompt，否则模型无从知道要落实什么。"""
    client, transport = make_client([good_skeleton().model_dump_json()])
    await build_skeleton(
        client, book_id=1,
        volume=make_volume(new_locations=["西郊水塔"], new_factions=["第九处"]),
        bible_text="【世界观】x",
    )
    user = transport.calls[0]["user"]
    assert "西郊水塔" in user and "第九处" in user
    assert "必须落实的横向扩张" in user
    assert "第 1 卷" in user


async def test_build_skeleton_retries_with_issue_list() -> None:
    """校验失败 → 带具体问题增量重试（不重发全上下文、不全量重写）。"""
    bad = good_skeleton(factions=["守夜会"]).model_dump_json()
    good = good_skeleton().model_dump_json()
    client, transport = make_client([bad, good])

    result = await build_skeleton(
        client, book_id=1, volume=make_volume(), bible_text="x", max_rounds=2
    )

    assert result.ok, result.error
    assert result.llm_calls == 2
    second = transport.calls[1]["user"]
    assert "未通过校验" in second
    assert "势力只有 1 股" in second
    assert "其余部分保持原样" in second


async def test_build_skeleton_gives_up_after_max_rounds() -> None:
    """连续失败必须显式失败（决策 E：质量闸门连续失败 → 暂停等人工）。"""
    bad = good_skeleton(factions=["守夜会"]).model_dump_json()
    client, _ = make_client([bad])

    result = await build_skeleton(
        client, book_id=1, volume=make_volume(), bible_text="x", max_rounds=1
    )

    assert result.ok is False
    assert result.skeleton is None
    assert result.violations
    assert "未通过校验" in (result.error or "")


async def test_build_skeleton_records_usage_and_context() -> None:
    client, _ = make_client([good_skeleton().model_dump_json()])
    await build_skeleton(client, book_id=42, volume=make_volume(), bible_text="x")

    assert client.records
    rec = client.records[0]
    assert rec.node_name == "skeleton_planner"
    assert rec.book_id == 42
    assert rec.finish_reason is not None


async def test_build_skeleton_is_idempotent() -> None:
    """★ 幂等键命中不重复调用（防 LangGraph #7417 静默重放导致重复扣费）。"""
    client, transport = make_client([good_skeleton().model_dump_json()])
    kw = dict(book_id=1, volume=make_volume(), bible_text="x",
              idempotency_key="skeleton:1:1")

    r1 = await build_skeleton(client, **kw)
    r2 = await build_skeleton(client, **kw)

    assert r1.ok and r2.ok
    assert len(transport.calls) == 1
    assert r2.llm_calls == 0

# --------------------------------------------------------------------------- #
# 台阶落实的**容错匹配**（★ 实测假阳性修复）
# --------------------------------------------------------------------------- #


def test_ladder_mention_tolerates_dropped_modifier() -> None:
    """★ 实测假阳性：台阶写「档案馆内部保守派值夜人集团」，
    骨架写「保守派值夜人集团」（省了修饰语）—— **是落实了**。

    原先用整串精确匹配 → 判为未落实 → 逼人去改本来正确的产物，
    或在反复失败后干脆关掉校验器（旧项目 `enabled: false` 的老路）。
    """
    from storyforge.graph.nodes.skeleton_planner import _mentions

    blob = "保守派值夜人集团 腐败档案守护者集团"
    assert _mentions(blob, "档案馆内部保守派值夜人集团") is True


def test_ladder_mention_tolerates_quote_and_order_change() -> None:
    """台阶写 `外部调查记者组织“记忆之眼”`，骨架写 `'记忆之眼'组织`。"""
    from storyforge.graph.nodes.skeleton_planner import _mentions

    blob = "'记忆之眼'组织 政府秘密部门"
    assert _mentions(blob, '外部调查记者组织“记忆之眼”') is True


def test_ladder_mention_tolerates_truncated_place_name() -> None:
    """台阶写「城市边缘的废弃印刷厂（关联规则手册的印刷）」，
    骨架写「潜入废弃印刷厂」—— 只留了 5 字关键词，**是落实了**。"""
    from storyforge.graph.nodes.skeleton_planner import _mentions

    blob = "印刷厂之谜 潜入废弃印刷厂 揭露规则手册的印刷源头"
    assert _mentions(blob, "城市边缘的废弃印刷厂（关联规则手册的印刷）") is True


def test_ladder_mention_rejects_unrelated_item() -> None:
    """★ 容错不等于放水：完全无关的势力必须判为未落实。"""
    from storyforge.graph.nodes.skeleton_planner import _mentions

    blob = "保守派值夜人集团 记忆之眼组织 废弃印刷厂"
    assert _mentions(blob, "星海帝国远征军") is False
    assert _mentions(blob, "洛阳纸贵商会") is False


def test_ladder_mention_normalize_removes_embedded_punctuation() -> None:
    """★ 归一必须**全局**去标点，不是 `str.strip`（它只处理两端）。

    实测踩到：`外部调查记者组织“记忆之眼”` 的引号在**中间**，
    `strip` 根本去不掉，于是匹配失败。
    """
    from storyforge.graph.nodes.skeleton_planner import _normalize_for_match

    assert _normalize_for_match("“记忆之眼”组织（外部）") == "记忆之眼组织外部"
    assert _normalize_for_match("城市边缘的废弃印刷厂") == "城市边缘的废弃印刷厂"


def test_ladder_mention_empty_item_is_ok() -> None:
    """台阶项为空 → 不构成"未落实"（不是骨架的错）。"""
    from storyforge.graph.nodes.skeleton_planner import _mentions

    assert _mentions("任意文本", "") is True


def test_validate_skeleton_passes_with_reworded_ladder_items() -> None:
    """★ 端到端：台阶项被改写措辞时，整轮校验仍应通过。

    这是上面几条的集成断言 —— 防的是"单元测试过了但校验器仍报错"。
    """
    vol = make_volume(
        new_factions=["档案馆内部保守派值夜人集团", '外部调查记者组织“记忆之眼”'],
        new_locations=["档案馆地下深层禁区", "城市边缘的废弃印刷厂（关联规则手册的印刷）"],
    )
    sk = good_skeleton(
        arcs=[
            ArcOut(key="十三条", kind="core",
                   description="陈砚在档案馆地下深层禁区逐条验证残页上的十三条规则",
                   chapter_span=[1, 60]),
            ArcOut(key="继任", kind="core",
                   description="陈砚接过值夜簿，成为新的值夜人", chapter_span=[5, 120]),
            ArcOut(key="周德海", kind="relationship",
                   description="陈砚与周德海从互相提防到以命相托", chapter_span=[3, 90]),
            ArcOut(key="裴照", kind="relationship",
                   description="裴照隔门传话，两人由试探转为同盟", chapter_span=[10, 110]),
            ArcOut(key="保守派", kind="opposition",
                   description="保守派值夜人集团要封死档案馆，赌注是楼内住户的性命",
                   chapter_span=[20, 150]),
            ArcOut(key="印刷厂之谜", kind="opposition",
                   description="潜入废弃印刷厂，追查规则手册被篡改的源头，"
                               "赌注是篡改者抢先找到陈砚",
                   chapter_span=[40, 180]),
        ],
        segments=[
            "接手值夜，摸清馆内规矩",
            "接触\u2018记忆之眼\u2019记者，卷入外部调查",
            "潜入废弃印刷厂取证",
            "地下深层禁区的门第一次开启",
        ],
        factions=["保守派值夜人集团", "\u2018记忆之眼\u2019组织", "档案科", "住户互助会"],
    )
    assert validate_skeleton(sk, vol) == []
