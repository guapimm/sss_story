"""`config/settings.toml` 真的被读取 —— 防"配置写了却零效果"。

## 这个断点防的是什么

`config/settings.toml` 自称「全局配置唯一入口（决策 D-02）」，
但 `SettingsConfigDict` **没有 `toml_file`**、全仓也无任何读它的代码 ——
于是**改它零效果**，真正生效的是 `config.py` 的默认值。

这与旧项目 L-06（`.env` 被 `llm_providers.json` 静默覆盖）是**同一类病**：
两个来源竞争同一字段，且**失败方向是静默的** —— 用户以为改了配置，
实际什么都没变。本项目明令禁止。

## 为什么必须测"改一个值真的变"

只断言"文件存在"或"没报错"是**恒真测试**（本项目已栽过三次）。
故这里**临时改 TOML 里的一个值**，断言它真的影响到 `Settings`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storyforge.config import Settings, get_settings, reset_settings

pytestmark = pytest.mark.unit


@pytest.fixture
def toml_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "settings.toml"


def test_toml_value_actually_reaches_settings(toml_path: Path) -> None:
    """★ 改 TOML → `Settings` 必须跟着变（否则该文件是死的）。"""
    original = toml_path.read_text(encoding="utf-8")
    assert "skeleton_batch_size = 50" in original, "锚点漂移，请更新本测试"
    try:
        toml_path.write_text(
            original.replace("skeleton_batch_size = 50", "skeleton_batch_size = 77"),
            encoding="utf-8",
        )
        reset_settings()
        assert Settings().pipeline.skeleton_batch_size == 77, (
            "改了 settings.toml 但 Settings 没变 —— 该文件未被读取（死配置）"
        )
    finally:
        toml_path.write_text(original, encoding="utf-8")
        reset_settings()


def test_environment_overrides_toml(toml_path: Path, monkeypatch) -> None:
    """★★ 环境变量必须**优先于** TOML —— 本机覆盖不能被仓库文件压住。

    ⚠️ pydantic-settings 的 `settings_customise_sources` 元组
    **越靠前优先级越高**（已实测确认，与直觉相反）。
    若把 TOML 排在 env 之前，本机设的 `STORYFORGE_*` 会被静默忽略。
    """
    monkeypatch.setenv("STORYFORGE_LLM__DEFAULT_TIMEOUT_S", "111")
    reset_settings()
    try:
        assert Settings().llm.default_timeout_s == 111.0, (
            "环境变量没能覆盖 settings.toml —— source 顺序反了"
        )
    finally:
        reset_settings()


def test_missing_toml_is_not_an_error(toml_path: Path, monkeypatch) -> None:
    """TOML 不存在时**静默跳过** —— 安装态可能没有它，那是合法状态。

    但**不得**因此崩掉或把默认值变成 None。
    """
    import storyforge.config as cfg

    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_DIR", toml_path.parent / "__nope__")
    reset_settings()
    try:
        s = Settings()
        # 回落到字段默认值，而不是报错/空值
        assert s.llm.default_timeout_s == 2400.0
        assert s.pipeline.detail_batch_size == 5
    finally:
        reset_settings()


def test_settings_toml_agrees_with_code_defaults(toml_path: Path) -> None:
    """★ TOML 与 `config.py` 默认值不得互相矛盾（含过期值）。

    两者并存时，**任何分歧都是一颗地雷**：文件里写着 15、代码里是 5，
    读的人无从判断哪个生效。这条把分歧钉成失败。

    例外：TOML 未声明的键不比较（它是子集，不是全集）。
    """
    import tomllib

    from storyforge.config import (
        LLMSettings,
        MemorySettings,
        ObservabilitySettings,
        PipelineSettings,
        ScorecardSettings,
        StructureGateSettings,
    )

    sections = {
        "llm": LLMSettings(),
        "memory": MemorySettings(),
        "scorecard": ScorecardSettings(),
        "structure_gate": StructureGateSettings(),
        "pipeline": PipelineSettings(),
        "observability": ObservabilitySettings(),
    }
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

    mismatches: list[str] = []
    for section, values in data.items():
        obj = sections.get(section)
        if obj is None:
            continue
        for key, toml_val in values.items():
            if not hasattr(obj, key):
                continue
            code_val = getattr(obj, key)
            if isinstance(toml_val, float | int) and isinstance(code_val, float | int):
                if abs(float(toml_val) - float(code_val)) > 1e-9:
                    mismatches.append(f"[{section}] {key}: toml={toml_val} code={code_val}")
            elif list(toml_val) != list(code_val) if isinstance(code_val, tuple | list) else toml_val != code_val:
                mismatches.append(f"[{section}] {key}: toml={toml_val} code={code_val}")

    assert not mismatches, (
        "settings.toml 与 config.py 默认值分歧（读的人无从判断哪个生效）：\n  "
        + "\n  ".join(mismatches)
    )
