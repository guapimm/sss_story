"""模式数据加载器测试 —— `config/genres/` 与 `config/scales.toml`。

## 本文件防的是什么

`config/genres/` 此前是**空目录**、`config/scales.toml` **不存在**：
题材只是一个裸字符串透传进 prompt，档位数据硬编码在
`agents/architect.py` 的函数体内（旧项目）。

两条实测教训驱动本文件的断言：

1. **静默降级**：旧项目"判定类型不在枚举内 → 降级为「其他」"
   → 966 条关系里势力仅剩 7 条、**势力实体 0 个**。
   故本文件断言"未知 id/档位必须抛错，不返回空值"。
2. **两份真相**：旧项目 `_def_lo/_def_hi` 的硬编码兜底值与
   `scale_spec` 的权威值不一致。故断言"只有一份来源"。
"""

from __future__ import annotations

import pytest

from storyforge.modes.genres import (
    GenreCard,
    GenreConfigError,
    load_all_genres,
    load_genre,
)
from storyforge.modes.scales import (
    ScaleConfigError,
    all_scales,
    scale_spec,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 题材加载
# --------------------------------------------------------------------------- #


def test_all_ported_genres_load() -> None:
    """★ 18 个题材必须全部可加载（移植完整性的验收）。

    权威来源：旧项目 `backend/app/story_genres.py`。
    """
    genres = load_all_genres()
    assert len(genres) == 18, f"题材数不是 18：{sorted(genres)}"
    for gid in ("xianxia", "urban", "scifi", "suspense", "boys_love"):
        assert gid in genres, f"缺题材 {gid}"


def test_genre_card_has_expected_fields() -> None:
    """`GenreCard` 的字段必须与旧项目一一对应（防移植时漏字段）。

    旧项目字段：`id / channel / name / tags / world_prompt /
    growth_ladder / naming / tropes / reader_expect / tone_examples / flows`
    """
    g = load_genre("xianxia")
    assert g.id == "xianxia"
    assert g.name == "修仙玄幻"
    assert g.channel == "male"
    assert "修真" in g.tags
    assert g.world_prompt, "world_prompt 不得为空"
    assert g.growth_ladder, "growth_ladder 不得为空"
    assert g.naming, "naming 不得为空"
    assert g.tropes, "tropes 不得为空"
    assert g.reader_expect, "reader_expect 不得为空"
    assert g.tone_examples, "tone_examples 不得为空"
    assert g.flows, "flows 不得为空"


def test_unknown_genre_raises_not_silent( ) -> None:
    """★ 未知题材必须**抛错**，不返回空卡。

    静默降级正是旧项目势力实体归零的那条路径
    （`normalize_entity_kind` 的 docstring 明确记载）。
    """
    with pytest.raises(GenreConfigError, match="不存在"):
        load_genre("不存在的题材")


def test_genre_error_lists_available() -> None:
    """报错必须列出可用题材（让调用方能自助修复）。"""
    with pytest.raises(GenreConfigError) as ei:
        load_genre("nope")
    assert "xianxia" in str(ei.value), "报错应列出可用题材"


def test_missing_required_field_raises(tmp_path) -> None:
    """★ 缺必填字段必须抛错（TOML 里少写 `name` 是常见错误）。"""
    d = tmp_path / "genres"
    d.mkdir()
    (d / "broken.toml").write_text('id = "broken"\n', encoding="utf-8")
    with pytest.raises(GenreConfigError, match="缺少必填字段"):
        load_genre("broken", directory=d)


def test_wrong_type_for_array_field_raises(tmp_path) -> None:
    """数组字段写成字符串必须报错（不静默当成单元素）。"""
    d = tmp_path / "genres"
    d.mkdir()
    (d / "bad.toml").write_text(
        'id = "bad"\nname = "坏"\ntags = "不是数组"\n', encoding="utf-8"
    )
    with pytest.raises(GenreConfigError, match="必须是数组"):
        load_genre("bad", directory=d)


def test_bad_toml_syntax_raises(tmp_path) -> None:
    d = tmp_path / "genres"
    d.mkdir()
    (d / "syntax.toml").write_text('id = "x\nname = \n', encoding="utf-8")
    with pytest.raises(GenreConfigError, match="TOML 语法错误"):
        load_genre("syntax", directory=d)


def test_empty_genres_dir_returns_empty_dict(tmp_path) -> None:
    """目录不存在 → 空 dict（**不报错**）：全新 clone 时可能尚未填充。

    但单个文件缺失**要**报错 —— 两者语义不同。
    """
    assert load_all_genres(directory=tmp_path / "not_exist") == {}


def test_unknown_extra_field_is_preserved_not_dropped(tmp_path) -> None:
    """前向兼容：TOML 里多写的字段不得导致报错，且要保留在 `extra`。"""
    d = tmp_path / "genres"
    d.mkdir()
    (d / "fwd.toml").write_text(
        'id = "fwd"\nname = "未来题材"\nfuture_field = "值"\n', encoding="utf-8"
    )
    card = load_genre("fwd", directory=d)
    assert card.name == "未来题材"
    assert card.extra.get("future_field") == "值"


def test_writer_block_skips_empty_sections() -> None:
    """★ 空字段**不得**拼出空壳小节。

    `命名风格：`（后面什么都没有）会被模型读成"随便起名"，
    比整节不出现更糟 —— 旧项目这类空壳注入实测促成了套话。
    """
    bare = GenreCard(id="x", name="光板题材")
    block = bare.writer_block()
    assert block == "【题材】光板题材", f"空字段被拼进去了：{block!r}"
    for label in ("世界设定", "成长阶梯", "命名风格", "常见桥段", "读者预期"):
        assert label not in block


def test_writer_block_includes_all_present_fields() -> None:
    g = load_genre("xianxia")
    block = g.writer_block()
    for label in ("【题材】修仙玄幻", "世界设定", "成长阶梯", "命名风格",
                  "常见桥段", "读者预期", "基调参考"):
        assert label in block, f"缺小节 {label}"


# --------------------------------------------------------------------------- #
# 档位加载
# --------------------------------------------------------------------------- #


def test_all_four_scales_available() -> None:
    """四个档位都必须可加载（fast / standard / grand / ultra）。"""
    for mode in ("story", "learn"):
        scales = all_scales(mode)
        assert set(scales) == {"fast", "standard", "grand", "ultra"}, (
            f"{mode} 档位不全：{sorted(scales)}"
        )


def test_all_scales_does_not_leak_other_mode_name() -> None:
    """★ 另一个模式的**名字**不得被当成档位名。

    实测踩到：初版 `all_scales("story")` 返回了
    `['fast','grand','learn','standard']` —— 因为 `[learn]` 是顶层
    dict 就被当成档位了。根因是把"所有顶层 dict"当成了档位。
    """
    for mode in ("story", "learn"):
        names = set(all_scales(mode))
        assert "story" not in names and "learn" not in names, (
            f"{mode} 的档位名里混进了模式名：{sorted(names)}"
        )


def test_grand_is_shared_between_modes() -> None:
    """★ grand 档两模式**完全相同** —— 共享写在顶层，避免两份数据漂移。

    （实测：`scale_spec("story","grand") == scale_spec("learn","grand")`。）
    """
    s = scale_spec("story", "grand")
    l = scale_spec("learn", "grand")
    assert s.rule == l.rule
    assert s.chapters_lo == l.chapters_lo
    assert s.chapters_hi == l.chapters_hi


def test_ultra_differs_between_modes() -> None:
    """★ ultra 档两模式**不同** —— learn 讲知识点拓展路线，story 讲扩张。

    这条防的是"为省事把 ultra 也写成共享"。
    """
    s = scale_spec("story", "ultra")
    l = scale_spec("learn", "ultra")
    assert s.rule != l.rule, "两模式的 ultra 规则文本不应相同"
    assert "知识" in l.rule, "learn 的 ultra 应讲知识拓展路线"


def test_unknown_scale_raises_with_available() -> None:
    """★ 未知档位必须抛错，且列出可用档位。

    **不返回默认档**：静默回落会让 25 章的书按 400 章档位规划
    （旧项目"400 章全挤第 1 卷"的同类风险）。
    """
    with pytest.raises(ScaleConfigError) as ei:
        scale_spec("story", "nonexistent")
    msg = str(ei.value)
    assert "不存在" in msg
    assert "ultra" in msg, "应列出可用档位"


def test_clamp_chapters_keeps_inside_range() -> None:
    """★ `clamp_chapters` 必须把章数夹进档位区间（下限至少 1）。

    旧项目 `chapter_count` 有时为 0（`est_ch` 全 0 时），
    会推出"0 章的书"。
    """
    fast = scale_spec("story", "fast")     # 3~10
    assert fast.clamp_chapters(0) == 3, "0 章必须被夹到下限"
    assert fast.clamp_chapters(1) == 3
    assert fast.clamp_chapters(6) == 6
    assert fast.clamp_chapters(999) == 10, "超上限必须被夹回"
    assert fast.clamp_chapters(-5) == 3


def test_multi_volume_flag_matches_ultra_only() -> None:
    """只有 ultra 是多卷档（story 的 fast/standard/grand 都单卷）。"""
    assert scale_spec("story", "ultra").is_multi_volume is True
    assert scale_spec("story", "grand").is_multi_volume is False
    assert scale_spec("story", "fast").is_multi_volume is False
