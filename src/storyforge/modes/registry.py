"""模式注册表 —— `BookMode` → `Mode` 实现的**唯一映射点**。

## 为什么需要它

契约规则 3 禁止共享代码判模式（`if book.mode == "learn"`）。那么
"该用哪个模式实现"这个判断**总得发生在某处** —— 本模块就是那个
唯一允许的地方（`_MODE_SCAN_SKIP` 里的 `modes/`）。

调用方（graph / cli）这样拿实现：

    mode = mode_for(book.mode)      # ← 唯一的分派点
    pack = mode.prompts             # 之后全是 Protocol 调用，无分支

## 归属来源（★ 此前是断裂的）

`Book.mode`（`models.py:83`）此前**从未被写也从未被读**，
`config.py:275` 的 `settings.mode` 是**只写不读的死配置**。
`BookRepository.upsert()`（阶段 0 建）让 `mode` 真正落库，
本模块把"落库的值"翻译成"能力实现" —— 两端接通。
"""

from __future__ import annotations

from storyforge.domain.enums import BookMode
from storyforge.modes.learn import LearnMode, make_learn_mode
from storyforge.modes.protocols import Mode
from storyforge.modes.story import (
    StoryEstimator,
    StoryGenrePlugin,
    StoryPromptPack,
)

#: 默认题材（未指定时）。★ 不设成 `""`：空题材会让 bible prompt
#: 拼出「【题材】」后什么都没有，比明确默认更糟。
DEFAULT_GENRE = "xianxia"


class StoryMode:
    """story 模式对象 —— 一期唯一完整实现。

    只暴露 story 需要的 3 个能力（`01-总体架构.md:404-410`）。
    """

    name = "story"

    def __init__(self, genre: str | None = None) -> None:
        self.prompts = StoryPromptPack()
        self.genre = StoryGenrePlugin(genre or DEFAULT_GENRE)
        self.estimator = StoryEstimator()


def mode_for(
    mode: BookMode | str, *, genre: str | None = None
) -> Mode:
    """按 `BookMode` 取模式实现（**唯一分派点**）。

    ★ `genre` 只对 story 有意义（learn 的流派在阶段 2 移植）。
    """
    if isinstance(mode, BookMode):
        name = mode.value
    else:
        try:
            name = BookMode(str(mode)).value
        except ValueError as exc:
            raise ValueError(
                f"未知模式 {mode!r}；可用："
                f"{[m.value for m in BookMode]}"
            ) from exc

    return _BUILDERS[name](genre)


def _build_story(genre: str | None) -> Mode:
    return StoryMode(genre)


def _build_learn(genre: str | None) -> Mode:  # noqa: ARG001
    return make_learn_mode()


#: 模式构造器 —— 加模式只需在此登记一行
_BUILDERS = {
    BookMode.STORY.value: _build_story,
    BookMode.LEARN.value: _build_learn,
}

#: 自检：`BookMode` 的每个成员都必须有构造器。
#: 漏登记会让"新枚举值上线但 `mode_for` 抛 KeyError"——
#: 与 `PipelineStage.CHAPTER` 缺失同类（阶段 0 修过）。
assert set(_BUILDERS) == {m.value for m in BookMode}, (
    f"模式构造器不完整：缺 "
    f"{sorted({m.value for m in BookMode} - set(_BUILDERS))}"
)


__all__ = [
    "StoryMode",
    "LearnMode",
    "mode_for",
    "DEFAULT_GENRE",
]
