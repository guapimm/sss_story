r"""PG-1B：`doctor` 必须是只读体检 —— 防"体检一次，写盘一串"。

## 防的是什么（doc/10 实测 B-7、doc/11 §4.3）

PG-0 实测：安装态跑一次 `doctor`，安装目录多出 `data/`、`artifacts/`
与 **176,128 B 的 `storyforge.db`**。根因两处：`s.ensure_dirs()` 与
`Database.create_all()`。真实安装到 `C:\Program Files` 时这是
`PermissionError`，或被 UAC 虚拟化**静默**写进 VirtualStore（用户找不到自己的库）。

本文件钉住两条行为：
  1. doctor **不创建任何文件/目录**（缺库 → 四项数据库检查显式 SKIP，
     报"运行 init-db"，绝不代建）；
  2. 库已存在时四项检查**真跑**（不是全变跳过）—— 把"跳过"与"检查"分开。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storyforge.config import Settings
from storyforge.doctor import run_doctor

pytestmark = pytest.mark.unit

_DB_CHECKS = ("database", "foreign_keys_forced", "integrity", "all_tables_have_fk")


def _by_name(results: list[dict]) -> dict[str, dict]:
    return {r["check"]: r for r in results}


@pytest.fixture
def fresh_install_data(tmp_path: Path, monkeypatch) -> Path:
    """把 data 指向**不存在**的目录（模拟全新安装、还没 init-db）。

    `run_doctor()` 内部会 reset+重建 Settings，因此环境变量是唯一注入缝。
    """
    data = tmp_path / "data"
    monkeypatch.setenv("STORYFORGE_DATA_DIR", str(data))
    return data


def test_doctor_creates_nothing(fresh_install_data: Path) -> None:
    """★ 核心：跑 doctor 后文件系统增量必须为 0（目录与库都不许出现）。

    双向：若有人把 `s.ensure_dirs()` 或 `create_all()` 加回来，
    目录/库会立刻出现 —— 本测试当场变红。
    """
    ok, results = run_doctor()
    assert not fresh_install_data.exists(), (
        "doctor 创建了 data 目录 —— 写副作用回归（B-7）"
    )

    by = _by_name(results)
    # 未代建 ⇒ 数据库四项必须是显式 SKIP（ok=None），不得伪装 OK、也不得判失败
    for name in _DB_CHECKS:
        assert by[name]["ok"] is None, f"{name} 应为跳过，实际 {by[name]['ok']}"
        assert "init-db" in by[name]["detail"], "跳过原因必须可诊断"

    # config 项：目录不存在但父目录可写 → 通过，且明确说明"未创建"
    assert by["config"]["ok"] is True
    assert "未创建" in by["config"]["detail"]

    # 跳过不算失败（否则全新安装永远 FAIL，与验收判据 4.4.1 矛盾）
    assert ok is True


def test_doctor_skips_are_labeled_not_disguised(fresh_install_data: Path, capsys) -> None:
    """CLI 呈现层：跳过必须打成 [SKIP]，绝不混入 [OK]（静默降级同型病）。"""
    from storyforge.cli import cmd_doctor

    class _Args:
        verbose = False

    assert cmd_doctor(_Args()) == 0
    out = capsys.readouterr().out
    assert "[SKIP]" in out, "跳过项没有独立标记 —— 会被读成通过"
    assert "[SKIP] database" in out
    assert "[OK  ] database" not in out
    assert "doctor: PASS" in out


def test_doctor_checks_existing_db_for_real(tmp_path: Path, monkeypatch) -> None:
    """库已存在：四项数据库检查必须**真跑**（不是全变跳过）。

    双向：若有人把"库存在"分支误改成一律跳过，本测试立即失败。
    注意 doctor **不再 create_all 自愈建表**（schema 归 init-db 管），
    所以这里先用与生产一致的 Database 层建一次真实 schema。
    """
    from storyforge.infra.storage.db import Database

    data = tmp_path / "data"
    Database.from_settings(Settings(data_dir=data)).create_all()

    monkeypatch.setenv("STORYFORGE_DATA_DIR", str(data))
    ok, results = run_doctor()
    by = _by_name(results)

    assert by["database"]["ok"] is True
    assert by["database"]["detail"] == "15 tables", (
        "表数漂移：schema 变更后请同步更新本锚点与 doctor 文档"
    )
    for name in _DB_CHECKS:
        assert by[name]["ok"] is True, f"{name} 对已初始化的库应真跑：{by[name]['detail']}"
    assert ok is True


def test_doctor_missing_db_dir_is_reported_not_created(fresh_install_data: Path) -> None:
    """config 项必须报出"将创建于哪"，而不是静默替用户建好。

    反向钉死：把 _probe_writable 改成"直接 mkdir"会让
    test_doctor_creates_nothing 失败；本测试补"报文必须含路径"这一半。
    """
    _, results = run_doctor()
    by = _by_name(results)
    assert str(fresh_install_data) in by["config"]["detail"], (
        "config 项应指出 data_dir 的目标路径"
    )


# ---------------------------------------------------------------------------
# PG-1C：报告完整性 —— 任何检查项都不得静默消失或假装通过
# ---------------------------------------------------------------------------


def test_doctor_reports_all_ten_items_when_providers_broken(monkeypatch) -> None:
    """★ 变异二（doc/11 §5.3）：providers 加载失败 → 报告仍含 **10 项**。

    PG-0 实测：providers 抛错后 extractor/api_keys 两项静默消失（10→8），
    读报告的人无从知晓少了什么。现在失败项自身 FAIL、
    依赖它的两项以占位 SKIP 呈现 —— 一项都不许蒸发。
    """
    import storyforge.infra.llm.capability as capability

    def _boom(*args, **kwargs):
        raise RuntimeError("providers exploded")

    monkeypatch.setattr(capability, "load_registry", _boom)
    ok, results = run_doctor()
    names = [r["check"] for r in results]
    assert len(results) == 10, f"报告应含 10 项，实际 {len(results)}：{names}"
    assert len(set(names)) == 10, "报告出现重复项"
    by = _by_name(results)
    assert by["providers"]["ok"] is False
    for name in ("extractor_thinking_minimized", "api_keys"):
        assert by[name]["ok"] is None, f"{name} 应为占位 SKIP，实际 {by[name]['ok']}"
        assert "占位" in by[name]["detail"]
    assert ok is False, "providers 本身失败 → 整体必须 FAIL（占位不改变判定）"


def test_domain_scan_skips_when_no_source_tree(monkeypatch, tmp_path) -> None:
    """★ 变异三（doc/11 §5.3）：无源码树 → domain_is_framework_free **不报 OK**。

    PG-0 实测（B-5）：安装态扫到 0 个文件也报 OK —— 架构契约成了恒真。
    无源码树是安装态的正常形态 → 显式 SKIP。
    """
    import storyforge.doctor as doctor_mod

    monkeypatch.setattr(doctor_mod, "REPO_ROOT", tmp_path)  # tmp 下无 src/
    ok, results = run_doctor()
    by = _by_name(results)
    assert by["domain_is_framework_free"]["ok"] is None, (
        f"无源码树应 SKIP，实际 {by['domain_is_framework_free']}"
    )
    assert "无源码树" in by["domain_is_framework_free"]["detail"]
    assert ok is True  # SKIP 不算失败


def test_domain_scan_empty_dir_fails_not_ok(monkeypatch, tmp_path) -> None:
    """domain 目录存在但扫到 0 个 .py → FAIL，绝不报 OK（B-5 的核心形态）。

    反向钉死：若有人把"0 文件"分支删掉退回 `rglob` 直接迭代，
    空目录会再次静默报 OK —— 本测试当场变红。
    """
    import storyforge.doctor as doctor_mod

    (tmp_path / "src" / "storyforge" / "domain").mkdir(parents=True)
    monkeypatch.setattr(doctor_mod, "REPO_ROOT", tmp_path)
    ok, results = run_doctor()
    by = _by_name(results)
    assert by["domain_is_framework_free"]["ok"] is False
    assert "0 个" in by["domain_is_framework_free"]["detail"]
    assert ok is False
