"""字面层指标的回归测试 —— 每条防一个**实测复现过的**具体缺陷。

## 为什么这个文件存在

`scorecard/literal.py` 的指标全部参与打分卡的 hard/target 判定，
但它们此前没有专门的单测 —— 只有 golden 基线复现测试
（`tests/golden/test_baseline_reproduction.py`，跑旧项目书 23 的 47 章）。

golden 有**一个覆盖盲区**：它只在书 23 上验证**整体分布**，
而书 23 恰好对下面两处缺陷**零命中** —— 于是缺陷静默存活：

  1. `犹如` 正则写了两遍 → `simile_count` 双计数（书 23 零命中 → 看不出来）
  2. `AI_SCENE_ENDINGS` 在 `__all__` 里但没定义（书 23 零命中 → 看不出来）

本文件用**合成输入**直接打在缺陷上，不依赖任何语料。
"""

from __future__ import annotations

import pytest

from storyforge.scorecard.literal import (
    AI_SCENE_ENDINGS,
    analyze,
    classify_ending,
    has_ai_scene_ending,
    simile_count,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 缺陷 B：`犹如` 双计数（0-3B）
# --------------------------------------------------------------------------- #


def test_simile_dedup_you_ru() -> None:
    """★ `犹如` 只许计一次 —— 曾经正则重复 → 双计数。

    实测复现（修复前）：
        输入 `他的眼睛犹如星辰般明亮，她的笑容犹如春风。`
        `犹如` 实际出现 **2** 次 → 修复前 `simile_count = 4`
        （pattern[5] 与 pattern[7] 各命中 2 次）

    ⚠️ 书 23（golden 基线样本）对 `犹如` **零命中** →
    基线测试**永远抓不到**这个缺陷。所以必须用合成输入。
    """
    text = "他的眼睛犹如星辰般明亮，她的笑容犹如春风。"
    assert text.count("犹如") == 2
    assert simile_count(text) == 2, (
        f"`犹如` 双计数复发了：应为 2，实得 {simile_count(text)}"
        "（检查 `_SIMILE_PATTERNS` 是否有重复项）"
    )


def test_simile_patterns_are_unique() -> None:
    """★ 比喻正则表**不得有重复项** —— 结构层防复发。

    上面那条测的是行为，这条测的是结构：任何两条 pattern 的
    `pattern` 字符串相同就说明有人又抄了一遍。
    """
    from storyforge.scorecard.literal import _SIMILE_PATTERNS

    raw = [p.pattern for p in _SIMILE_PATTERNS]
    dupes = sorted({p for p in raw if raw.count(p) > 1})
    assert not dupes, f"`_SIMILE_PATTERNS` 有重复正则（会导致重复计数）：{dupes}"


def test_simile_counts_each_marker_once() -> None:
    """各种比喻标记各出现一次 → 计数等于标记数。"""
    text = (
        "他的脸像石头一样硬，眼神仿佛结了霜似的，"
        "脚步如同踩在棉花上一般沉重，笑容好似刀锋一样冷，"
        "背影宛如山岳，气势犹如雷霆，神情恰似寒潭。"
    )
    assert simile_count(text) == 7


# --------------------------------------------------------------------------- #
# 缺陷 A：`AI_SCENE_ENDINGS`（0-3A）
# --------------------------------------------------------------------------- #


def test_ai_scene_endings_is_defined() -> None:
    """★ `__all__` 里声明的名字必须**真的存在**。

    修复前 `AI_SCENE_ENDINGS` 在 `__all__`（L612）但**全仓零定义** ——
    `from storyforge.scorecard.literal import *` 会抛 `AttributeError`
    （`scorecard/__init__.py` 用显式 import 侥幸未爆）。

    旧项目权威定义见 `story_llm/backend/app/style_metrics.py:52`。
    """
    assert len(AI_SCENE_ENDINGS) == 8
    assert "望向远方" in AI_SCENE_ENDINGS
    assert "目光深邃" in AI_SCENE_ENDINGS


def test_star_import_actually_works() -> None:
    """★ 直接验证 `import *` 不抛错 —— 这是缺陷 A 的**原始症状**。

    （名字不存在时 `import *` 抛 `AttributeError: module ... has no
     attribute 'AI_SCENE_ENDINGS'`。）
    """
    ns: dict[str, object] = {}
    exec(  # noqa: S102  （受控的内建 import，非用户输入）
        "from storyforge.scorecard.literal import *", ns
    )
    for name in ("AI_SCENE_ENDINGS", "SUMMARY_ENDINGS", "OPEN_TEASER_ENDINGS"):
        assert name in ns, f"`__all__` 声明了 {name} 但 `import *` 拿不到"


def test_ai_scene_ending_detected_at_tail() -> None:
    """AI 画面收尾命中末尾 → `has_ai_scene_ending` 为真。"""
    text = "他处理完手头的事，转身离开。\n她望着他的背影，目光深邃。"
    assert has_ai_scene_ending(text) is True


def test_ai_scene_ending_only_looks_at_tail() -> None:
    """★ **只看结尾 60 字** —— 中段出现合法画面**不得**误报。

    旧项目注释原文（`style_metrics.py:129`）：
    「AI 画面收尾只看结尾 60 字（避免中间合法『望向/嘴角』误报）」。

    本测试就是那个"中间合法场景"：`嘴角` 出现在开头，
    后面用足够多的正文把它推到 60 字窗口之外。
    """
    # `嘴角` 在开头，之后 ~200 字与它无关
    filler = "他继续核对账目，把每一笔都记在册子上。" * 12
    text = "他嘴角动了动，随即恢复平静。" + filler + "\n他合上册子，吹熄了灯。"
    assert text.strip()[-60:].find("嘴角") == -1, "构造失败：嘴角不该在末 60 字内"
    assert has_ai_scene_ending(text) is False, "中段的合法画面被误报了"


def test_ai_scene_ending_empty_and_short_input() -> None:
    """空输入与极短输入不得抛错。"""
    assert has_ai_scene_ending("") is False
    assert has_ai_scene_ending("他走了。") is False


def test_ai_scene_ending_does_not_change_ending_pattern() -> None:
    """★ 画面收尾**不并入** `classify_ending` 的 summary 判据 —— 语义分离。

    旧项目里两者是**独立信号**：
      · `classify_ending`（← `prose_style.ending_pattern`）是**分布分类**，
        用于统计章末形态（golden 基线：mid_action 23 / other 21 / omen 2 /
        dialogue 1 / summary 0 / time_skip 0）
      · `AI_SCENE_ENDINGS` 是 `needs_rewrite` 的**独立触发器**

    合并会让「AI 画面收尾」与「总结式收尾」混成一个类别，
    且 golden 基线的 `summary == 0` 断言就不再是"总结式结尾为零"的意思。
    """
    from storyforge.domain.enums import EndingPattern

    text = "他把事情都安排妥当了，该做的都做完了。\n她望着远方，目光坚定。"
    # 画面收尾命中...
    assert has_ai_scene_ending(text) is True
    # ...但章末分类仍是动作收束/其他，不会被翻成 summary
    assert classify_ending(text) is not EndingPattern.SUMMARY


def test_ai_scene_ending_trigger_fires() -> None:
    """画面收尾必须真的进入打分卡的触发器（不是只算不拦）。"""
    from storyforge.scorecard import evaluate
    from storyforge.scorecard.aggregate import TriggerReason

    body = "他逐一核对完账目，把册子收回柜中，站起身活动了一下肩颈。" * 40
    text = body + "\n她望着远方，目光深邃。"
    card = evaluate(text, global_no=9)
    assert TriggerReason.AI_SCENE_ENDING in card.triggers
    assert card.needs_review is True


def test_ai_scene_ending_not_triggered_in_middle() -> None:
    """中段画面不得触发 —— 防误报（与 `has_ai_scene_ending` 同口径）。"""
    from storyforge.scorecard import evaluate
    from storyforge.scorecard.aggregate import TriggerReason

    body = "他逐一核对完账目，把册子收回柜中，站起身活动了一下肩颈。" * 40
    text = "她望着远方，目光深邃。" + body + "\n他吹熄了灯。"
    card = evaluate(text, global_no=9)
    assert TriggerReason.AI_SCENE_ENDING not in card.triggers


# --------------------------------------------------------------------------- #
# 汇总契约
# --------------------------------------------------------------------------- #


def test_analyze_exposes_ai_scene_ending() -> None:
    """`analyze()` 的汇总结果必须带上该字段（供打分卡消费）。"""
    text = "他把事情做完了。\n她望着远方，目光坚定。"
    m = analyze(text)
    assert m.ai_scene_ending is True
    assert m.to_dict()["ai_scene_ending"] is True
