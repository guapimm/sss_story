"""全卷台阶规划节点测试（对策② / 痛点 5）。

★ 本文件的核心验收：**用旧项目的真实失败形态**验证节点能拦住它。

旧项目的失败形态（书 23 实测）：
  · `total_volumes=5` 但只有第 1 卷有内容，后续卷 `blueprint="missing"`
  · 400 章全部分配给第 1 卷
  · 第 1 卷 `vol_goal` = 「在长明楼读透三十七条」（一栋楼里）
  · 全书 4 个里程碑（3 → 205 之间 202 章真空）
"""

from __future__ import annotations

import pytest

from storyforge.domain.outline import Milestone, SeriesPlan, VolumePlan
from storyforge.domain.validators.structure_gate import GateConfig, evaluate_series
from storyforge.graph.nodes.volume_planner import (
    local_index,
    load_series_plan,
    persist_series_plan,
    series_plan_from_out,
    volume_for_chapter,
)
from storyforge.infra.llm.schemas import MilestoneOut, SeriesPlanOut, VolumePlanOut

pytestmark = pytest.mark.unit


def _milestones(count: int, n: int) -> list[MilestoneOut]:
    """均匀铺满整卷的里程碑（保证最大间隔 ≤25）。

    ⚠️ 天真的 `1 + i*(count//n)` 会留一个尾部大间隔：
    `count=200, n=8` → points=[1,26,...,176]，尾段 176→200 只有 24 帧，
    但 `count=224, n=8` → `224//8=28` → 间隔 28 > 上限 25 → 闸门报错。
    这里按**实际需要的最小里程碑数**铺满。
    """
    need = max(1, -(-count // 25))  # ceil(count / 25)
    n = max(n, need)
    step = count / n
    pts = sorted({max(1, min(count, int(round(1 + i * step)))) for i in range(n)})
    return [MilestoneOut(chapter_no=p, key=f"m{i}") for i, p in enumerate(pts)]


def _vol(n: int, *, count: int = 200, locs=None, facs=None, ant=None, milestones=8):
    return VolumePlanOut(
        volume_no=n, title=f"第{n}卷", goal=f"目标{n}",
        new_locations=locs or [f"{n}号新城", f"{n}号秘境"],
        new_factions=facs or [f"{n}号宗门", f"{n}号世家"],
        antagonist=ant or f"第{n}位大敌",
        power_ceiling=f"上限{n}", core_conflict=f"矛盾{n}",
        escalation_from_prev=f"从第{n-1}卷的村斗升级为第{n}卷的城战",
        old_map_closure=f"第{n}卷旧地图收尾",
        chapter_count=count,
        # ⚠️ 必须用 pydantic 的 `MilestoneOut`（不是 domain 的 `Milestone`）——
        # schema 层只接受可校验的模型对象。
        milestones=_milestones(count, milestones),
    )


def test_series_plan_assigns_contiguous_chapter_ranges() -> None:
    """★ 卷边界由代码分配并**冻结**（决策 D-15）。

    模型只给每卷章数，章号区间由 `series_plan_from_out` 计算 ——
    这样避免模型自己编号导致重叠或空洞（旧项目卷章混用造成过 P0 事故）。
    """
    out = SeriesPlanOut(
        logline="x", total_volumes=3, target_chapters=600,
        volumes=[_vol(1), _vol(2), _vol(3)],
    )
    plan = series_plan_from_out(out)
    assert plan.volumes[0].chapter_start == 1
    assert plan.volumes[0].chapter_end == 200
    assert plan.volumes[1].chapter_start == 201
    assert plan.volumes[2].chapter_end == 600
    # 无空洞、无重叠
    for a, b in zip(plan.volumes, plan.volumes[1:]):
        assert b.chapter_start == a.chapter_end + 1


def test_volume_lookup_and_local_index_are_derived() -> None:
    """卷内序号**派生不存储**（决策 D-15）。

    旧项目 P0 事故：`since_ch` 写入侧存卷内号、消费侧按全书号比对
    → 「已持续 10011 章」→ 每个伤每章都挂「本轮必须推进」→ Editor 每章判低分。
    """
    out = SeriesPlanOut(
        logline="x", total_volumes=2, target_chapters=400,
        volumes=[_vol(1, count=200), _vol(2, count=200)],
    )
    plan = series_plan_from_out(out)
    plan.book_id = 1

    v = volume_for_chapter(plan, 250)
    assert v is not None and v.volume_no == 2
    assert local_index(plan, 2, 250) == 50      # 250 - 201 + 1
    assert local_index(plan, 2, 201) == 1
    assert local_index(plan, 2, 400) == 200
    assert volume_for_chapter(plan, 9999) is None


# --------------------------------------------------------------------------- #
# ★ 拦住旧项目的失败形态
# --------------------------------------------------------------------------- #


def test_blocks_book23_shape_400_chapters_in_volume_1() -> None:
    """★ 书 23 的真实形态必须被闸门拦住。

    实测：`chapters: list[400]` → 卷分配 `{1: 400}`；
    `volumes[1..4].blueprint = "missing"`；`plot_nodes` 仅 4 个
    （3 → 205 之间有 **202 章真空**）。

    而旧项目的 `ultra_structure_config` **设计对了指标却 enabled=false**，
    于是这本书顺利写了 400 章。本测试确保新系统的闸门真的会拦。

    ⚠️ 「占位卷」要构造得真实：它**不是全空**（schema 会先拦空值），
    而是**有内容但内容空洞**——这正是旧项目 `blueprint="missing"` 的形态：
    字段有值，值是模板句「推向「X」的下一个阶段」、只有 1 章、无里程碑。
    """
    placeholders = [
        VolumePlanOut(
            volume_no=i, title=f"占位{i}", goal=f"延续第{i-1}卷",
            new_locations=["待定"], new_factions=["待定"],
            antagonist="老周",
            power_ceiling="待定", core_conflict="待定",
            escalation_from_prev="推向「X」的下一个阶段",   # ← 模板句
            old_map_closure="待定",
            chapter_count=1,
            milestones=[],                                   # ← 无里程碑
        )
        for i in (2, 3, 4, 5)
    ]
    out = SeriesPlanOut(
        logline="陈砚接手老楼夜班，得到一本会自己改写的规则手册",
        total_volumes=5,
        target_chapters=400,
        volumes=[
            _vol(1, count=400, locs=["长明楼"], facs=["档案馆"], ant="老周", milestones=4),
            *placeholders,
        ],
    )
    plan = series_plan_from_out(out)
    result = evaluate_series(plan, GateConfig())

    assert not result.ok, "必须拦住书 23 的形态"
    assert result.blocking, "必须**阻断**（不能只是 shadow 告警）"
    codes = {i.code.value for i in result.issues}
    # 章数失衡 / 地图与势力不足 / 里程碑缺失 / 模板句
    assert "chapter_distribution" in codes
    assert "milestones" in codes
    assert "escalation" in codes
    assert "new_locations" in codes
    # 第 1 卷占全书 100% 章数
    assert result.metrics["max_volume_share"] == 1.0


def test_gate_default_is_blocking_not_shadow() -> None:
    """★ 闸门默认必须**阻断**。

    旧项目 `ultra_structure_config.json` 是 `enabled: false, shadow: true` ——
    **设计对了却关着的校验器，等于没有**。这是最贵的教训之一。
    """
    cfg = GateConfig()
    assert cfg.enabled is True
    assert cfg.shadow is False


def test_healthy_plan_passes_gate() -> None:
    out = SeriesPlanOut(
        logline="x", total_volumes=3, target_chapters=600,
        volumes=[_vol(1), _vol(2), _vol(3)],
    )
    plan = series_plan_from_out(out)
    result = evaluate_series(plan, GateConfig())
    assert result.ok, [i.message for i in result.issues]


# --------------------------------------------------------------------------- #
# 落盘与加载（大产物不入 state）
# --------------------------------------------------------------------------- #


def test_persist_and_load_roundtrip(tmp_path) -> None:
    """全卷台阶落盘 → 加载必须保语义（含卷边界与里程碑）。"""
    from storyforge.infra.storage.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    out = SeriesPlanOut(
        logline="x", total_volumes=2, target_chapters=400,
        volumes=[_vol(1, count=200), _vol(2, count=200)],
    )
    plan = series_plan_from_out(out)
    plan.book_id = 7

    key = persist_series_plan(store, plan)
    assert key.endswith("series_plan.json")
    assert store.exists(key)

    loaded = load_series_plan(store, 7)
    assert loaded.book_id == 7
    assert loaded.total_volumes == 2
    assert len(loaded.volumes) == 2
    assert loaded.volumes[0].chapter_start == 1
    assert loaded.volumes[1].chapter_end == 400
    assert loaded.volumes[0].new_locations == plan.volumes[0].new_locations
    assert len(loaded.volumes[0].milestones) == len(plan.volumes[0].milestones)
    assert loaded.volumes[0].escalation_from_prev == plan.volumes[0].escalation_from_prev


def test_artifact_read_missing_raises_not_empty(tmp_path) -> None:
    """★ 读不存在的产物必须**抛错**，不得返回空串。

    旧项目 `docstore.py:212` 读不到章节正文时静默返回 `""`
    → **正文丢失不报错**（实测 80% 的 `content_path` 为空）。
    """
    from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore

    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFound):
        store.read_text("books/1/prose/00001.txt")
    with pytest.raises(ArtifactNotFound):
        store.read_json("books/1/series_plan.json")


def test_artifact_paths_are_generated_by_single_source(tmp_path) -> None:
    """路径必须由 `ArtifactStore` 的静态方法**统一生成**。

    旧项目路径有 3 种格式混用（`story/books/`、`learn/books/`、`books/`），
    **同一本书内也混用**。
    """
    from storyforge.infra.storage.artifacts import ArtifactStore

    assert ArtifactStore.prose_key(23, 42).key == "books/23/prose/00042.txt"
    assert ArtifactStore.plan_key(23, 42).key == "books/23/plans/00042.json"
    assert ArtifactStore.skeleton_key(23, 3).key == "books/23/skeleton/v003.json"
    assert ArtifactStore.bible_key(23).key == "books/23/bible.md"


def test_artifact_append_is_incremental(tmp_path) -> None:
    """追加语义 —— 旧项目 `rag.json` 整份重写（book 8 达 27.75 MB）。"""
    from storyforge.infra.storage.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    ref = ArtifactStore.prose_key(1, 1)
    store.write_text(ref, "第一段。")
    store.append_text(ref, "第二段。")
    store.append_text(ref, "第三段。")
    assert store.read_text(ref) == "第一段。第二段。第三段。"


def test_content_hash_is_deterministic(tmp_path) -> None:
    from storyforge.infra.storage.artifacts import ArtifactStore, content_hash

    store = ArtifactStore(tmp_path)
    ref = ArtifactStore.prose_key(1, 1)
    store.write_text(ref, "内容")
    assert store.hash_of(ref) == content_hash("内容")
    assert store.hash_of("books/1/prose/99999.txt") is None


# --------------------------------------------------------------------------- #
# 修复提示必须是增量补缺（不是全量重写）
# --------------------------------------------------------------------------- #


def test_repair_prompt_is_incremental() -> None:
    """★ 修复指令必须是**增量补缺**，不是"重新全部规划一遍"。

    旧项目实测（`docs/AGENTS.md`）：
        **修复必须走增量补缺，禁止回退成「全量重写整份故事 JSON」**
        —— 大输出必被模型截断（book23/25 两次 ROLLED_BACK 的根因）。
        补丁按 error_code 分类：arcs/beats/capacity/milestones/
        factions/characters/volumes，各自小 prompt。
    """
    from storyforge.graph.nodes.volume_planner import _repair_prompt
    from storyforge.domain.validators.structure_gate import GateIssue, GateErrorCode

    plan = SeriesPlan(
        book_id=1, total_volumes=1, target_chapters=400,
        volumes=[VolumePlan(volume_no=1, title="v", chapter_start=1, chapter_end=400)],
    )
    gate = type("G", (), {})()
    from storyforge.domain.validators.structure_gate import GateResult

    result = GateResult(
        ok=False,
        issues=[
            GateIssue(GateErrorCode.MILESTONES if hasattr(GateErrorCode, "MILESTONES")
                      else GateErrorCode.MILESTONE_DENSITY,
                      "里程碑不足", volume_no=1),
            GateIssue(GateErrorCode.NEW_LOCATIONS, "新地图不足", volume_no=1),
        ],
    )
    prompt = _repair_prompt(result, plan)
    assert "只修补下列问题" in prompt
    assert "只修改有问题的部分" in prompt
    assert "new_locations" in prompt
    assert "milestones" in prompt
    # 必须带上 error_code 分类（供模型按类修）
    assert "问题类别" in prompt
