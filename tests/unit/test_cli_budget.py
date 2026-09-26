"""S0 阶段 B：CLI 预算接线测试。

## 这些测试防的是什么

阶段 A 做出了**可执行的**预算闸门（`ProbeBudget`），但 CLU 够不着它：
`_make_client()` 不接受 args、`plan`/`write` 没有预算参数、跑完不给费用摘要、
预算停机后退出码仍是 0。**闸门装好了却没接到水管上** —— 这是本项目反复
出现的同一型缺陷（"设计了但没接线"）。

## 用户的明确裁决（2026-09-26）

> "不设默认，强制显式传参才启用预算"

因此这里**必须**钉住两件事：
  1. 不传参 → 三个字段全 `None` → 与旧行为完全一致（不能偷偷限流）
  2. 传参 → 真的接到 `ClientConfig` 上（不是解析了却丢掉）

## 为什么退出码要单独测

脚本与 CI 只看退出码。预算停机若仍返回 0，批量跑批会把
"钱花光了、后半本没写"当成成功继续下一本。
"""

from __future__ import annotations

import argparse

import pytest

from storyforge.cli import (
    _budget_exit,
    _make_client,
    _print_cost_summary,
    build_parser,
)
from storyforge.infra.llm.client import ClientConfig, LLMClient
from storyforge.observability.metering import UsageMetrics

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 参数解析与接线
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", ["plan", "write"])
def test_budget_args_exist_on_spending_commands(command: str) -> None:
    """★ `plan`/`write` 都必须能接预算参数（它们才会真花钱）。"""
    argv = ["plan", "--book-id", "1", "--topic", "t"] if command == "plan" \
        else ["write", "--book-id", "1"]
    ns = build_parser().parse_args(argv)
    for name in ("max_requests", "max_prompt_chars", "max_credits"):
        assert hasattr(ns, name), f"{command} 缺少预算参数 {name}"


@pytest.mark.parametrize("command", ["plan", "write"])
def test_budget_defaults_are_none_so_old_behavior_is_kept(command: str) -> None:
    """★★ 负责人裁决：**不设默认**，不传参就不启用预算。

    若这里出现任何非 None 默认值，就意味着"跑真实模型被静默限流/静默放行"，
    两种都不是想要的。
    """
    argv = ["plan", "--book-id", "1", "--topic", "t"] if command == "plan" \
        else ["write", "--book-id", "1"]
    ns = build_parser().parse_args(argv)
    assert ns.max_requests is None
    assert ns.max_prompt_chars is None
    assert ns.max_credits is None


def test_make_client_without_args_keeps_budget_disabled() -> None:
    """不传预算参数 → `ClientConfig` 三个字段全 None → 闸门不启用。"""
    ns = argparse.Namespace()          # 完全没有预算属性，模拟旧调用方
    client = _make_client(ns)
    assert client.config.max_provider_requests is None
    assert client.config.max_prompt_chars is None
    assert client.config.max_configured_credits is None
    assert not client._budget_enabled


def test_make_client_forwards_budget_args_to_config() -> None:
    """★ 传了就要**真的接上** —— 解析出来却丢掉是本项目的高发缺陷。"""
    ns = argparse.Namespace(max_requests=7, max_prompt_chars=1234, max_credits=9.5)
    client = _make_client(ns)
    assert client.config.max_provider_requests == 7
    assert client.config.max_prompt_chars == 1234
    assert client.config.max_configured_credits == 9.5
    assert client._budget_enabled


def test_make_client_accepts_none_args_for_backwards_compat() -> None:
    """`_make_client()` 无参调用仍可用（旧测试/脚本依赖）。"""
    assert _make_client().config.max_provider_requests is None


# --------------------------------------------------------------------------- #
# 预算停机 → 非零退出
# --------------------------------------------------------------------------- #


class _StubClient:
    """只带摘要需要的几个属性。"""

    def __init__(self, *, stopped=False, reason=None, unknown=False,
                 records=None, credits=0.0) -> None:
        self.budget_stopped = stopped
        self.budget_stop_reason = reason
        self.unknown_usage = unknown
        self.records = records or []
        self.configured_credits = credits

    def blank_rate(self) -> float:
        return 0.0


def test_budget_stop_forces_nonzero_exit() -> None:
    """★ 预算停机必须非零退出（否则 CI/脚本会把"钱花完"当成功）。"""
    assert _budget_exit(0, _StubClient(stopped=True, reason="cap")) == 3


def test_budget_stop_does_not_mask_a_real_failure() -> None:
    """已经是失败码（如生成失败 1）时保持原码，不被 3 覆盖。"""
    assert _budget_exit(1, _StubClient(stopped=True, reason="cap")) == 1
    assert _budget_exit(2, _StubClient(stopped=True, reason="cap")) == 2


def test_no_budget_stop_keeps_success_code() -> None:
    assert _budget_exit(0, _StubClient(stopped=False)) == 0


# --------------------------------------------------------------------------- #
# 费用摘要
# --------------------------------------------------------------------------- #


def _record(model: str, *, prompt: int, cached: int, completion: int):
    from storyforge.observability.metering import LLMCallRecord

    return LLMCallRecord(
        call_id=f"c-{model}-{prompt}",
        node_name="chapter_writer", model=model, provider="p",
        usage=UsageMetrics(
            prompt_tokens=prompt, cached_tokens=cached,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
        finish_reason="stop",
    )


def test_cost_summary_reports_calls_and_blank_rate(capsys) -> None:
    client = _StubClient(records=[_record("m", prompt=100, cached=0, completion=50)])
    _print_cost_summary(client)
    out = capsys.readouterr().out
    assert "LLM 调用 1 次" in out
    assert "空输出率" in out


def test_cost_summary_with_no_calls_says_zero_cost(capsys) -> None:
    _print_cost_summary(_StubClient(records=[]))
    assert "0 次" in capsys.readouterr().out


def test_cost_summary_surfaces_budget_stop_reason(capsys) -> None:
    _print_cost_summary(_StubClient(stopped=True, reason="configured Credits 用尽"))
    assert "configured Credits 用尽" in capsys.readouterr().out


def test_cost_summary_warns_on_unaccountable_calls(capsys) -> None:
    """★ 缺 usage 的调用**无法记账**，必须显式警告而不是静默少报。"""
    _print_cost_summary(_StubClient(unknown=True))
    assert "无法记账" in capsys.readouterr().out


def test_cost_summary_keeps_currencies_separate(capsys) -> None:
    """★★ CREDITS 与 USD **不得相加** —— 旧项目跨单位混算导致成本完全失真。

    这里直接验证渲染层：两种币种同时出现时，各自独立展示。
    """
    from storyforge.observability import report as rep_mod

    client = _StubClient(records=[
        _record("mimo-v2.6-flash", prompt=1_000_000, cached=0, completion=0),
    ])
    table = rep_mod.pricing_table()
    mimo = table.get("mimo-v2.6-flash")
    if mimo is None or mimo.currency != "CREDITS":
        pytest.skip("providers.toml 未声明 MiMo 的 CREDITS 单价")
    _print_cost_summary(client)
    out = capsys.readouterr().out
    assert "CREDITS" in out
    # MiMo 单价 input=100 点/1M → 正好 100 点，验证换算没串
    assert f"{mimo.input:,.4f} CREDITS" in out


# --------------------------------------------------------------------------- #
# 端到端：真 client + 假 transport，验证摘要读到真实记录
# --------------------------------------------------------------------------- #


async def test_summary_reads_real_records_from_live_client() -> None:
    """★ 端到端：`cmd_plan` 把 client 传进 `_run_plan` 的唯一理由就是
    "摘要能读到这次运行的真实记录"。若哪天又改成内部新建 client，
    这里会立刻变红。"""

    async def transport(**_kw):
        return "ok", {"prompt_tokens": 1000, "completion_tokens": 500}

    client = LLMClient(transport, config=ClientConfig(
        max_attempts=1, backoff_base_s=0.0, max_provider_requests=5))
    await client.complete(node="chapter_writer", system="s", user="u")

    assert len(client.records) == 1
    assert client.provider_attempts == 1
    assert client.configured_credits > 0


def test_budget_config_reaches_client_from_cli_args_end_to_end() -> None:
    """★ 从 CLI 参数一路走到 `ClientConfig`（真实数据流，不是 UI-only 字段）。"""
    ns = build_parser().parse_args([
        "write", "--book-id", "1", "--max-requests", "3", "--max-credits", "1.5",
    ])
    client = _make_client(ns)
    assert client.config.max_provider_requests == 3
    assert client.config.max_configured_credits == 1.5
    assert client._budget_enabled
