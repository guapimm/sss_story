"""PG-1A/1B 路径解析规则的单元测试 —— 防止"装完读不到 / 装完写错地方"回归。

## 防的是什么（doc/10 实测、doc/11 §3.3/§4.2 的解析顺序）

PG-0 实测：非 editable 安装后 `providers` / `prompts` / `config` 三条链路同时断，
根因是 import 期冻结的 `REPO_ROOT` 系常量在安装态指向 `<venv>\\Lib`。
本文件钉住 `resolve_config_dir()` 与 `resolve_data_dir()` 的**完整优先级**及
`Settings` / `load_registry` 对它们的接线——每一层都必须**双向可区分**
（不只是"能解析"，还要"错序必失败"）。

解析顺序（高 → 低）：
  config: 1. `STORYFORGE_CONFIG_DIR`  2. 包内 `_resources/config`（安装态）
          3. `<REPO_ROOT>/config`（源码树）
  data:   1. `STORYFORGE_DATA_DIR`    2. 源码树 `<REPO_ROOT>/data`
          3. 安装态用户目录（仅安装态切换，§4.2 硬约束）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storyforge.config import (
    REPO_ROOT,
    Settings,
    resolve_config_dir,
    resolve_data_dir,
    reset_settings,
)
from storyforge.infra.llm.capability import ProviderConfigError, load_registry

pytestmark = pytest.mark.unit

#: 最小合法 providers.toml（够 `_parse_capability` 的必填字段即可）
_MINIMAL_PROVIDERS_TOML = """\
[providers.p1]
base_url = "http://localhost"
api_key_env = "TEST_KEY"

[[models]]
id = "m1"
provider = "p1"
tier = "balanced"
context_window = 1000
max_output_tokens = 100
"""


def test_env_var_wins_over_both_candidates(tmp_path: Path, monkeypatch) -> None:
    """环境变量是最高优先级 —— 即使包内与仓库两个候选都存在也必须让位。"""
    env_dir = tmp_path / "envcfg"
    env_dir.mkdir()
    got = resolve_config_dir(
        env_value=str(env_dir),
        package_dir=tmp_path / "pkg" / "_resources" / "config",  # 也不存在，但不影响结论
        repo_dir=tmp_path / "repo" / "config",
    )
    assert got == env_dir, "STORYFORGE_CONFIG_DIR 没有赢过其他候选 —— 优先级 1 失效"


def test_blank_env_value_is_ignored(tmp_path: Path) -> None:
    """空串/纯空白的环境变量视为未设置 —— 不得解析成空路径。"""
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)
    assert resolve_config_dir(env_value="   ", repo_dir=repo) == repo
    assert resolve_config_dir(env_value="", repo_dir=repo) == repo


def test_package_resources_win_when_present(tmp_path: Path) -> None:
    """★ 安装态模拟：包内 `_resources/config` 存在 → 必须选中它。

    两个候选**同时存在**时选包内 —— 若选了仓库候选，说明顺序 2/3 颠倒，
    安装态会退回"读 REPO_ROOT"的老病（正是 B-2）。
    """
    pkg = tmp_path / "pkg" / "_resources" / "config"
    repo = tmp_path / "repo" / "config"
    pkg.mkdir(parents=True)
    repo.mkdir(parents=True)
    assert resolve_config_dir(env_value=None, package_dir=pkg, repo_dir=repo) == pkg


def test_falls_back_to_repo_when_package_missing(tmp_path: Path) -> None:
    """★ 源码树模拟：包内目录不存在（force-include 只作用于 wheel）→ 回落仓库。"""
    repo = tmp_path / "repo" / "config"
    repo.mkdir(parents=True)
    got = resolve_config_dir(
        env_value=None,
        package_dir=tmp_path / "pkg" / "_resources" / "config",  # 源码树中不存在
        repo_dir=repo,
    )
    assert got == repo


def test_source_tree_default_is_repo_config() -> None:
    """无参调用（生产路径）在本仓库中必须解析到 `<REPO_ROOT>/config`。

    这是"既有测试零影响"的保证（doc/11 §2.7）：源码树里
    `src/storyforge/_resources/config` 不存在，所以默认解析不变。
    """
    import os

    assert os.environ.get("STORYFORGE_CONFIG_DIR") in (None, "")
    assert resolve_config_dir() == REPO_ROOT / "config"
    assert resolve_config_dir().exists(), "仓库 config/ 竟然不存在 —— 环境错了，不是代码错了"


def test_package_config_dir_is_inside_storyforge_package() -> None:
    """★ 默认包内候选必须在 `storyforge/` 包**内部**（off-by-one 防线）。

    首版把包根算成 `parents[1]`（= src / site-packages，包的**上级**），
    安装态下包内候选永不命中、config_dir 落回 `<venv>\\Lib\\config` 旧病灶
    —— 由隔离安装验收当场抓出。本测试把该错误钉死：
    若有人再改成 `parents[1]`，`_PACKAGE_CONFIG_DIR.parent.parent.name`
    会变成 `"src"`（源码树）而非 `"storyforge"`，立即失败。
    """
    import storyforge.config as cfg

    # `<pkg>/_resources/config` 的上两级 = 包目录本身
    assert cfg._PACKAGE_CONFIG_DIR.parent.name == "_resources"
    assert cfg._PACKAGE_CONFIG_DIR.parent.parent.name == "storyforge"
    assert cfg._PACKAGE_CONFIG_DIR == Path(cfg.__file__).parent / "_resources" / "config"
    # 源码树中该目录不存在（force-include 只作用于 wheel）——这是回落仓库根的前提
    assert not cfg._PACKAGE_CONFIG_DIR.exists()


def test_settings_config_dir_follows_env(tmp_path: Path, monkeypatch) -> None:
    """★ `Settings.config_dir` 必须按需解析，不得 import 期冻结（doc/11 §2.4）。

    反向钉死：若有人把 `default_factory` 改回 `= 某常量`，
    运行期设的环境变量将不再生效 —— 本测试立即抓住。
    """
    env_dir = tmp_path / "envcfg"
    env_dir.mkdir()
    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(env_dir))
    reset_settings()
    try:
        assert Settings().config_dir == env_dir
        # data_dir 不受 config 环境变量影响（PG-1B 才改它的安装态分支）
        assert Settings().data_dir == REPO_ROOT / "data"
    finally:
        reset_settings()


def test_load_registry_reads_config_dir(tmp_path: Path, monkeypatch) -> None:
    """★ `load_registry()` 必须跟随 `config_dir`（doc/11 §3.4 顺带修的缺陷）。

    PG-0 实测：旧代码用 import 期常量读 providers.toml，
    设 `STORYFORGE_CONFIG_DIR` 改不动它（与 modes/ 加载器行为不一致）。
    """
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "providers.toml").write_text(_MINIMAL_PROVIDERS_TOML, encoding="utf-8")
    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(cfg))
    reset_settings()
    try:
        assert load_registry().get_model("m1").id == "m1"
    finally:
        reset_settings()


def test_load_registry_missing_providers_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """config_dir 里没有 providers.toml → **抛错**，不得静默回落仓库配置。

    回落 = 同名文件两份时用户改了其中一份却毫无效果（静默覆盖同型病）。
    """
    cfg = tmp_path / "empty_cfg"
    cfg.mkdir()
    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(cfg))
    reset_settings()
    try:
        with pytest.raises(ProviderConfigError) as ei:
            load_registry()
        assert str(cfg) in str(ei.value), "报错应指向实际找过的路径，便于诊断"
    finally:
        reset_settings()


# ---------------------------------------------------------------------------
# PG-1B：resolve_data_dir（doc/11 §4.2 —— 仅安装态切用户目录）
# ---------------------------------------------------------------------------


def _make_source_repo(root: Path) -> Path:
    """造一个"源码树"候选根（有 pyproject.toml）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    return root


def test_data_dir_env_wins(tmp_path: Path) -> None:
    """STORYFORGE_DATA_DIR 是最高优先级 —— 源码树/用户目录两个候选都让位。"""
    repo = _make_source_repo(tmp_path / "repo")
    user = tmp_path / "user"
    env_dir = tmp_path / "envdir"
    got = resolve_data_dir(env_value=str(env_dir), repo_root=repo, user_dir=user)
    assert got == env_dir


def test_data_dir_blank_env_ignored(tmp_path: Path) -> None:
    """空串/纯空白的环境变量视为未设置 —— 不得解析成空路径。"""
    repo = _make_source_repo(tmp_path / "repo")
    assert resolve_data_dir(env_value="  ", repo_root=repo) == repo / "data"


def test_data_dir_source_tree_stays_repo_data(tmp_path: Path) -> None:
    """★ 源码树（候选根有 pyproject.toml）→ `<repo>/data` —— 开发机数据不动。"""
    repo = _make_source_repo(tmp_path / "repo")
    assert resolve_data_dir(repo_root=repo, user_dir=tmp_path / "user") == repo / "data"


def test_data_dir_installed_uses_user_dir(tmp_path: Path) -> None:
    """★ 安装态（候选根无 pyproject.toml）→ 用户目录（注入的 user_dir）。

    反向钉死：若有人把"仅安装态切换"改成无条件切默认值，
    开发机 data/ 会被移走（doc/11 §4.2 硬约束）——本测试与上一条
    一起把两种形态分开钉住。
    """
    bare = tmp_path / "installed_venv_lib"  # 无 pyproject.toml
    bare.mkdir()
    user = tmp_path / "user"
    assert resolve_data_dir(repo_root=bare, user_dir=user) == user


def test_user_data_dir_stdlib_platforms(tmp_path: Path, monkeypatch) -> None:
    """用户目录的平台分支（仅 stdlib；doc/11 §10 决策 6：不用 platformdirs）。"""
    import os
    import sys

    import storyforge.config as cfg

    home = Path.home()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert cfg._user_data_dir() == tmp_path / "roaming" / "storyforge"

    monkeypatch.delenv("APPDATA", raising=False)
    assert cfg._user_data_dir() == home / "AppData" / "Roaming" / "storyforge", (
        "APPDATA 缺失时必须有确定性回落，不能返回 None 或崩"
    )

    monkeypatch.setattr(sys, "platform", "darwin")
    assert cfg._user_data_dir() == home / "Library" / "Application Support" / "storyforge"

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert cfg._user_data_dir() == tmp_path / "xdg" / "storyforge"

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert cfg._user_data_dir() == home / ".local" / "share" / "storyforge"
    assert os.environ.get("XDG_DATA_HOME") in (None, "")  # delenv 已生效的旁证


def test_settings_data_dir_follows_env(tmp_path: Path, monkeypatch) -> None:
    """★ `Settings.data_dir` 按需解析，不得 import 期冻结；源码树默认仍是 REPO_ROOT/data。"""
    env_dir = tmp_path / "envdata"
    monkeypatch.setenv("STORYFORGE_DATA_DIR", str(env_dir))
    reset_settings()
    try:
        assert Settings().data_dir == env_dir
    finally:
        reset_settings()
    # 反向：环境变量拿掉后回落源码树语义（若有人改成无条件用户目录，这里会失败）
    monkeypatch.delenv("STORYFORGE_DATA_DIR", raising=False)
    reset_settings()
    try:
        assert Settings().data_dir == REPO_ROOT / "data"
    finally:
        reset_settings()
