"""模式数据加载器测试 —— `config/genres/`、`config/scales.toml`、`config/flows/`。

## 本文件防的是什么

`config/genres/` 此前是**空目录**、`config/scales.toml` **不存在**、
`config/flows/` **不存在**：题材只是一个裸字符串透传进 prompt，
档位数据硬编码在 `agents/architect.py` 的函数体内（旧项目），
流派数据则完全留在旧项目里未移植。

三条实测教训驱动本文件的断言：

1. **静默降级**：旧项目"判定类型不在枚举内 → 降级为「其他」"
   → 966 条关系里势力仅剩 7 条、**势力实体 0 个**。
   故本文件断言"未知 id/档位必须抛错，不返回空值"。
2. **两份真相**：旧项目 `_def_lo/_def_hi` 的硬编码兜底值与
   `scale_spec` 的权威值不一致。故断言"只有一份来源"。
3. **两个命名空间**：旧项目两套 flow 数据 **2 个 ID 撞名**且措辞不同，
   合并会丢信息 → 故断言"撞名的两套语义确实不同、各自保留专有字段"。
"""

from __future__ import annotations

import pytest

from storyforge.modes.flows import (
    FlowConfigError,
    flows_dir,
    load_flow,
    load_flows,
    load_namespace,
)
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


# --------------------------------------------------------------------------- #
# 流派卡（两个命名空间：tropes 通用套路 / xianxia 修仙流派）
# --------------------------------------------------------------------------- #


def test_both_namespaces_load_fully() -> None:
    """★ 两套流派数据都必须完整（16 通用套路 + 25 修仙流派）。

    权威来源：旧项目 `story_flows.py`（16）与 `flows.py`（25）。
    """
    tropes = load_namespace("tropes")
    xianxia = load_namespace("xianxia")
    assert len(tropes) == 16, f"通用套路数不是 16：{sorted(tropes)}"
    assert len(xianxia) == 25, f"修仙流派数不是 25：{sorted(xianxia)}"


def test_flow_id_comes_from_filename_not_field() -> None:
    """★ `id` 由**文件名**给出，不在 TOML 里重复声明。

    防"文件名 vs id 字段"的第二真相 —— 本项目已记录过多份真相漂移的教训
    （旧项目 `since_ch` 卷内号/全书号、`context_breakdown` 两份平行定义）。
    """
    card = load_flow("knowledge_seeker", "xianxia")
    assert card.id == "knowledge_seeker"
    assert card.namespace == "xianxia"
    raw = (flows_dir("xianxia") / "knowledge_seeker.toml").read_text(encoding="utf-8")
    assert not raw.startswith("id =") and "\nid =" not in raw, (
        "TOML 里出现了 id 字段 —— id 必须只由文件名给出（单一来源）"
    )


def test_colliding_ids_are_isolated_by_namespace() -> None:
    """★★ 撞名的 ID 在两套数据里**语义不同**，分命名空间后不再冲突。

    实测：`rebirth` / `system` 两套都有，措辞不同：

        learn 版：以信息差**降维打击**、抢占**机缘**、同门构陷/测灵大典
        story 版：以信息差与成熟心智**改写命运**、抢占**先机**、背叛/命运线

    合并会丢掉模式专属措辞 —— 这是"两个命名空间"决策的**直接依据**。
    """
    l_rebirth = load_flow("rebirth", "xianxia")
    s_rebirth = load_flow("rebirth", "tropes")
    assert l_rebirth.name == s_rebirth.name == "重生流"      # 名字相同
    assert l_rebirth.hook != s_rebirth.hook, "两套的 hook 应当措辞不同"
    # 各套保留自己的专属字段
    assert l_rebirth.scenes == ("xianxia",)
    assert l_rebirth.universal is False
    assert s_rebirth.universal is True
    assert "male" in s_rebirth.channels


def test_all_learn_flows_are_xianxia_scoped() -> None:
    """learn 的 25 流派 `scenes` 恒为 `['xianxia']`（用户只做玄幻修仙）。"""
    for fid, card in load_namespace("xianxia").items():
        assert card.scenes == ("xianxia",), f"{fid} 的 scenes 异常：{card.scenes}"


def test_all_flows_have_three_narrative_devices() -> None:
    """★ 两套数据的 `narrative_devices` 实测均为 **3 条**。

    少于 3 条会让"叙事装置"这一维度信息不足（它要被反复使用）。
    """
    for ns in ("tropes", "xianxia"):
        for fid, card in load_namespace(ns).items():
            assert len(card.narrative_devices) == 3, (
                f"{ns}/{fid} 的 narrative_devices 不是 3 条："
                f"{len(card.narrative_devices)}"
            )


def test_all_flows_have_arc_example() -> None:
    """`arc_example` 必须都有（用户决策**保留**：架构师的现成 few-shot）。"""
    for ns in ("tropes", "xianxia"):
        for fid, card in load_namespace(ns).items():
            assert card.arc_example.strip(), f"{ns}/{fid} 缺 arc_example"
            assert 60 <= len(card.arc_example) <= 200, (
                f"{ns}/{fid} 的 arc_example 长度异常：{len(card.arc_example)}"
            )


def test_load_flows_preserves_selection_order() -> None:
    """★ 「流派可多选」必须**保持用户勾选顺序**。

    顺序稳定有利于 prompt 缓存命中（同一组流派每次渲染逐字节相同）。
    """
    sel = ["sword_saint", "knowledge_seeker", "pill_master"]
    assert [c.id for c in load_flows(sel, "xianxia")] == sel
    rev = list(reversed(sel))
    assert [c.id for c in load_flows(rev, "xianxia")] == rev


def test_flow_writer_block_include_example_toggle() -> None:
    """★ `include_example` 开关控制 arc_example（token 预算的抓手）。

    实测：25 张卡含示例 8,453 字符 / 不含 5,295 字符（单卡 338 vs 211）。
    规划期带示例（风格锚点），逐章写作不带（控 token）。
    """
    card = load_flow("knowledge_seeker", "xianxia")
    with_ex = card.writer_block(include_example=True)
    without = card.writer_block(include_example=False)
    assert card.arc_example[:20] in with_ex
    assert card.arc_example[:20] not in without
    assert len(with_ex) > len(without)
    for blk in (with_ex, without):
        assert "【流派】悟道流" in blk
        assert card.hook in blk
        assert card.narrative_devices[0] in blk


def test_flow_writer_block_skips_empty_sections(tmp_path, monkeypatch) -> None:
    """空字段不得拼空壳小节（与 `GenreCard.writer_block` 同原则）。"""
    d = tmp_path / "flows" / "xianxia"
    d.mkdir(parents=True)
    (d / "bare.toml").write_text('name = "光板流派"\n', encoding="utf-8")
    _patch_flows_root(monkeypatch, tmp_path)
    assert load_flow("bare", "xianxia").writer_block() == "【流派】光板流派"


def test_unknown_flow_raises_with_available() -> None:
    """未知流派 id 必须抛错并列出可用的（与 `load_genre` 同口径）。"""
    with pytest.raises(FlowConfigError) as ei:
        load_flow("不存在的流派", "xianxia")
    assert "knowledge_seeker" in str(ei.value), "报错应列出可用流派"


def test_unknown_namespace_raises() -> None:
    with pytest.raises(FlowConfigError, match="未知命名空间"):
        load_namespace("不存在的命名空间")


def test_flow_missing_required_field_raises(tmp_path, monkeypatch) -> None:
    """缺 `name` 必须抛错（TOML 少写 name 是常见错误）。"""
    d = tmp_path / "flows" / "xianxia"
    d.mkdir(parents=True)
    (d / "broken.toml").write_text('hook = "只有卖点"\n', encoding="utf-8")
    _patch_flows_root(monkeypatch, tmp_path)
    with pytest.raises(FlowConfigError, match="缺少必填字段"):
        load_flow("broken", "xianxia")


def test_flow_wrong_type_for_array_field_raises(tmp_path, monkeypatch) -> None:
    d = tmp_path / "flows" / "xianxia"
    d.mkdir(parents=True)
    (d / "bad.toml").write_text(
        'name = "坏"\nnarrative_devices = "不是数组"\n', encoding="utf-8"
    )
    _patch_flows_root(monkeypatch, tmp_path)
    with pytest.raises(FlowConfigError, match="必须是数组"):
        load_flow("bad", "xianxia")


def test_flow_bad_toml_syntax_raises(tmp_path, monkeypatch) -> None:
    d = tmp_path / "flows" / "xianxia"
    d.mkdir(parents=True)
    (d / "syntax.toml").write_text('name = "x\nhook = \n', encoding="utf-8")
    _patch_flows_root(monkeypatch, tmp_path)
    with pytest.raises(FlowConfigError, match="TOML 语法错误"):
        load_flow("syntax", "xianxia")


def test_missing_namespace_dir_returns_empty(tmp_path, monkeypatch) -> None:
    """命名空间目录不存在 → 空 dict（全新 clone 时可能尚未填充）。"""
    _patch_flows_root(monkeypatch, tmp_path)
    assert load_namespace("tropes") == {}
    assert load_namespace("xianxia") == {}


def test_genre_flows_field_points_at_tropes_not_xianxia() -> None:
    """★★ `GenreCard.flows` 引用的 id 属于 **tropes** 命名空间。

    实测依据：18 个题材共引用 16 个 id，**全部**属于 story 的通用套路集；
    learn 的 25 个流派里 **23 个从未被任何题材引用** ——
    这是"两套是两个不同的轴"的直接证据，也是分命名空间的理由。
    """
    all_genre_flows: set[str] = set()
    for card in load_all_genres().values():
        all_genre_flows |= set(card.flows)
    tropes = set(load_namespace("tropes"))
    assert all_genre_flows <= tropes, (
        f"题材引用了 tropes 之外的 id：{sorted(all_genre_flows - tropes)}"
    )


def test_xianxia_genre_flows_all_loadable() -> None:
    """`xianxia` 题材引用的 6 个 flow 必须都能在 tropes 里加载到。"""
    g = load_genre("xianxia")
    loaded = load_flows(list(g.flows), "tropes")
    assert len(loaded) == len(g.flows)
    assert all(c.name for c in loaded)


def _patch_flows_root(monkeypatch, tmp_path) -> None:
    """把 `flows_dir` 指到临时目录（测错误分支用）。"""
    import storyforge.modes.flows as F

    monkeypatch.setattr(
        F, "flows_dir",
        lambda ns, root=None: tmp_path / "flows" / ns,
    )
