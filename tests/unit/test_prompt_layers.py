"""Prompt 分层与缓存稳定性测试（对策⑥ + 对策⑦）。

## 这些测试防的是旧项目命中率卡在 44% 的机制性原因

实测（writer prompt 逐块预算分解）：
  · 单章 30,115 字符（≈10.7k–13.3k token）
  · 稳定 11,998（36.5%）/ 易变 19,117（63.5%）
  · 真正跨请求完全不变的只有 **5,209 字符（17%）**
  · **每章变的「文风范本」插在「世界观圣经」之后** → 把后面 19k 全部推出缓存前缀
  · `bible_inject.select` 用 `f"{plot} {title}"` 做 query → **逐章不同**
    → 全书最稳的圣经前缀**在第一个块就分叉**
"""

from __future__ import annotations

import pytest

from storyforge.infra.llm.prompt_builder import (
    PromptLayerError,
    PromptLayers,
    diff_layers,
)
from storyforge.infra.prompts.library import (
    ChapterContext,
    anchor_block,
    beat_block,
    build_chapter_layers,
    build_extractor_layers,
    detail_block,
    health_block,
    length_block,
)

pytestmark = pytest.mark.unit

BIBLE = "【世界观】长明楼是一座旧档案馆，共五层。" * 40
VOLUME = "【本卷】第1卷《值夜手册》\n卷目标：读透三十七条"


def mk_ctx(ch: int, *, states: str = "陈砚：位置=值夜室", transition: bool = False) -> ChapterContext:
    return ChapterContext(
        global_no=ch, volume_no=1, title=f"第{ch}章标题",
        plot=f"第{ch}章梗概",
        plot_detail=f"场景一｜值夜室：陈砚查阅登记簿（第{ch}次）→缺页→发现空白",
        end_hook=f"第{ch}章钩子：末页翘开一角",
        states=states,
        injuries="陈砚：左肩剑创（重·维持）",
        prev_exit_state="值夜室内，门外有人唤名",
        next_entry_state="走廊灯位已复位",
        next_title=f"第{ch+1}章标题",
        beat_type="normal", is_transition=transition,
    )


def build(ch: int, **kw):
    return build_chapter_layers(
        bible_text=BIBLE, volume_block_text=VOLUME, segment_block_text="",
        ctx=mk_ctx(ch, **kw), min_chars=3500, soft_max=6000,
        style_rules="【文风】冷峻写实",
    )


# --------------------------------------------------------------------------- #
# ★ 稳定前缀必须在相邻章之间逐字节相同
# --------------------------------------------------------------------------- #


def test_stable_prefix_identical_across_adjacent_chapters() -> None:
    """★ 这是缓存命中的**前提条件**。

    旧项目命中率卡在 44%，且没有任何机制保证这一点。
    """
    a, b = build(12), build(13)
    assert a.prefix_hash() == b.prefix_hash()
    sa, sb = a.assemble()["system"], b.assemble()["system"]
    assert sa == sb, "system 必须逐字节稳定"


def test_changes_are_confined_to_volatile() -> None:
    """章节间变化**只应发生**在 volatile 层。"""
    d = diff_layers(build(12), build(13))
    assert d["prefix_stable"] is True
    assert d["stable_changed"] == []
    assert d["changed_blocks"], "应该有变化（否则测试无意义）"
    assert all(k.startswith("volatile:") for k in d["changed_blocks"]), (
        f"变化不应发生在 semi/stable：{d['changed_blocks']}"
    )


def test_semi_layer_is_stable_within_volume() -> None:
    """准稳定层在**同卷内**应稳定（卷号是常量，不破坏段内缓存）。"""
    a, b = build(12), build(13)
    assert a.semi_text == b.semi_text


# --------------------------------------------------------------------------- #
# ★ 字数参数不得进稳定层（旧项目的缓存杀手）
# --------------------------------------------------------------------------- #


def test_length_params_not_in_stable_layer() -> None:
    """★ 旧项目用 `.format(MIN_CHARS=...)` 把字数注入 **system prompt**
    → system 逐章变化 → **缓存前缀逐章失效**。

    本设计把字数放 user 尾部（recency 优先 + 不污染稳定层）。
    """
    msgs = build(12).assemble()
    assert "字数目标" in msgs["user"], "字数参数应在易变尾部"
    assert "字数目标" not in msgs["system"], "字数参数不得进稳定层"


def test_length_block_contains_targets() -> None:
    block = length_block(3500, 6000)
    assert "3500" in block and "6000" in block
    assert "不要 JSON" in block, "输出格式约束必须在尾部（模型最后读到的位置）"


def test_chapter_number_not_in_stable() -> None:
    for ch in (3, 12, 500):
        msgs = build(ch).assemble()
        assert f"第{ch}章" not in msgs["system"], f"第{ch}章污染了稳定层"


def test_volume_number_allowed_in_semi_but_not_stable() -> None:
    """两层标准不同：semi 允许卷号（卷内常量），stable 不允许。"""
    layers = build(12)
    assert "第1卷" in layers.semi_text
    bad = PromptLayers()
    bad.stable.add("x", "第3卷的内容")
    with pytest.raises(PromptLayerError):
        bad.assemble()


def test_chapter_number_forbidden_in_semi() -> None:
    bad = PromptLayers()
    bad.semi.add("segment", "第42章的段落目标")
    with pytest.raises(PromptLayerError):
        bad.assemble()


@pytest.mark.parametrize(
    "payload,label",
    [
        ("生成于 2026-09-13T18:37", "时间戳"),
        ("字数 3000~5000 字", "字数"),
        ("# a1b2c3d4-1111-2222-3333-444455556666", "UUID"),
    ],
)
def test_volatile_content_rejected_in_stable(payload: str, label: str) -> None:
    bad = PromptLayers()
    bad.stable.add("system", "正常内容")
    bad.stable.add("polluted", payload)
    with pytest.raises(PromptLayerError, match=label):
        bad.assemble()


def test_non_strict_validation_only_reports() -> None:
    """非 strict 模式只报告不抛错（供诊断用）。"""
    bad = PromptLayers()
    bad.stable.add("x", "第5章")
    violations = bad.validate_stability(strict=False)
    assert violations


# --------------------------------------------------------------------------- #
# ★ 换场章必须解除旧项目的换图禁令
# --------------------------------------------------------------------------- #


def test_transition_chapter_explicitly_allows_map_change() -> None:
    """★ 旧项目有 **8 处代码**禁止换图：

        architect.py:397「禁止每章自起自收（新场景开场）」
        architect.py:401「地点延续或自然过渡，禁止场景硬切」
        architect.py:372「ultra 档同卷相邻章必须场面连续；卷末可为下一卷换场」
        writer.py:258「禁止无过渡换地图」
        writer_rules.py:374「禁止新增细纲之外的…新地图」
        editor.py:243「无过渡换地图仍须改写」
        editor.py:106「新地图→记细纲偏离」

    → 全书只 2~5 次换图许可（ultra = 3~6 卷）；书 23 是 400 章/5 卷
    = **每 80 章才有一次**。实测地理熵：书 24/23/18 后半本新增地点 0/1/5。

    本设计：**规划内的换场正常，规划外的才是加戏**。
    """
    block = detail_block(mk_ctx(50, transition=True))
    assert "换场章" in block
    assert "可以换地图" in block
    assert "规划内" in block


def test_normal_chapter_still_allows_planned_new_map() -> None:
    """非换场章也须澄清：新地图不属于加戏（否则旧禁令换个形式复现）。"""
    block = detail_block(mk_ctx(20))
    assert "新地图" in block
    assert "不在此列" in block


def test_detail_block_enforces_outline_scenes() -> None:
    """细纲是**双向**硬约束：防遗漏（场景须落实）+ 防加戏（不得新增重大转折）。"""
    block = detail_block(mk_ctx(12))
    assert "场景骨架硬约束" in block
    assert "不得遗漏" in block
    assert "最高优先级" in block


def test_end_hook_forbids_invented_teasers() -> None:
    """章末钩子是硬指令 —— 禁止自创无主暗示（旧项目「反AI禁令」第 16/17 条）。"""
    block = detail_block(mk_ctx(12))
    assert "章末钩子" in block
    assert "无主暗示" in block


# --------------------------------------------------------------------------- #
# ★ 前后锚点（旧项目实测章间跳变是 Editor 低分主因）
# --------------------------------------------------------------------------- #


def test_anchor_block_includes_prev_and_next_state() -> None:
    """让 writer「看到要去哪里」—— 旧项目 writer 只知道自己这一章，
    导致书 1005 第 2 章从「拒绝给李巡长开门」直接跳到「与李巡长同席」。"""
    block = anchor_block(mk_ctx(12))
    assert "上一章出口状态" in block
    assert "入口状态" in block
    assert "不得写出下一章的内容" in block


def test_anchor_block_empty_when_no_neighbors() -> None:
    """首章无前锚点 → 返回空串（零开销，不产生无意义约束）。"""
    ctx = ChapterContext(global_no=1, volume_no=1, title="t", plot="p")
    assert anchor_block(ctx) == ""


# --------------------------------------------------------------------------- #
# ★ 伤势指令只经细纲下达（消除旧项目死锁）
# --------------------------------------------------------------------------- #


def test_health_block_absent_when_no_beats() -> None:
    """无节拍 → 无指令（旧项目无条件催促，而细纲无场景支撑 → 死锁）。"""
    assert health_block(mk_ctx(12)) == ""


def test_health_block_states_beats_are_planned_in_outline() -> None:
    """有节拍时必须说明「已排入细纲」，避免 writer 面对矛盾指令。"""
    ctx = mk_ctx(12)
    ctx.health_beats = "左肩剑创：第12章 疗伤尝试（场景2）"
    block = health_block(ctx)
    assert "已排入细纲" in block
    assert "不要额外加疗伤桥段" in block


# --------------------------------------------------------------------------- #
# 节拍指令
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "beat,keyword",
    [
        ("climax", "爆点章"),
        ("boss", "Boss"),
        ("emotion", "情绪点"),
        ("recall", "回忆杀"),
        ("turning", "转折章"),
        ("heal", "恢复章"),
        ("recovery_prep", "恢复铺垫"),
        ("transition", "换场章"),
    ],
)
def test_beat_blocks_exist(beat: str, keyword: str) -> None:
    assert keyword in beat_block(beat)


def test_unknown_beat_returns_empty() -> None:
    assert beat_block("nonexistent") == ""


def test_heal_beat_forbids_one_line_recovery() -> None:
    """恢复章必须写出可见过程 —— 「禁止一句话带过」。"""
    assert "禁止一句话带过" in beat_block("heal")


# --------------------------------------------------------------------------- #
# 抽取 prompt 必须精简（旧项目单条 system 超 10,000 字符）
# --------------------------------------------------------------------------- #


def test_extractor_prompt_excludes_bible() -> None:
    """★ 抽取类节点的 prompt **不含世界观圣经**。

    旧项目 `post_processor` 的单条 system prompt **超过 10,000 字符**，
    一次抽 11 类信息 → `entity_classify` 空输出 **100%**（32/32）、
    `verifier` **62.3%**。塞入圣经只会增加成本且稀释注意力。
    """
    layers = build_extractor_layers(
        instructions="你是记录员", prose="正文内容" * 100, known="既有账本"
    )
    msgs = layers.assemble()
    assert "世界观" not in msgs["system"]
    assert len(msgs["system"]) < 300, "抽取 system 必须精简"


def test_extractor_prompt_keeps_prose_in_volatile() -> None:
    """正文属易变内容，必须在尾部。"""
    layers = build_extractor_layers(
        instructions="你是记录员", prose="正文内容" * 100
    )
    assert "正文内容" in layers.volatile_text
    assert "正文内容" not in layers.stable_text


# --------------------------------------------------------------------------- #
# 层体量统计（供缓存设计参考）
# --------------------------------------------------------------------------- #


def test_layer_stats_shape() -> None:
    st = build(12).layer_stats()
    assert set(st) >= {
        "stable_chars", "semi_chars", "volatile_chars",
        "total_chars", "stable_ratio", "cacheable_ratio",
    }
    assert 0 <= st["stable_ratio"] <= 1
    assert st["cacheable_ratio"] >= st["stable_ratio"]


def test_breakdown_covers_all_layers() -> None:
    """上下文归因必须 100% 覆盖（旧项目覆盖率 0.76%）。"""
    layers = build(12)
    bd = layers.breakdown()
    assert sum(bd["chars"].values()) == layers.total_chars
    assert "bible" in bd["chars"] and "system" in bd["chars"]
