"""Optional in-process budget gates for the offline S0 probe."""

from __future__ import annotations

from storyforge.infra.llm.capability import ModelCapability
from storyforge.observability.metering import ModelPricing, UsageMetrics


class ProbeBudgetError(RuntimeError):
    """A local probe limit or accounting rule blocked a provider request."""


class ProbeBudget:
    """Small per-client budget state; no dependency on the client or transport."""

    def __init__(
        self,
        *,
        max_provider_requests: int | None = None,
        max_prompt_chars: int | None = None,
        max_configured_credits: float | None = None,
    ) -> None:
        self.max_provider_requests = max_provider_requests
        self.max_prompt_chars = max_prompt_chars
        self.max_configured_credits = max_configured_credits
        self.provider_attempts = 0
        self.configured_credits = 0.0
        self.unknown_usage = False
        self.budget_stopped = False
        self.budget_stop_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return any(value is not None for value in (
            self.max_provider_requests, self.max_prompt_chars,
            self.max_configured_credits,
        ))

    def stop(self, reason: str) -> str:
        self.budget_stopped = True
        if self.budget_stop_reason is None:
            self.budget_stop_reason = reason
        return self.budget_stop_reason

    def prepare(self, model: ModelCapability, prompt_chars: int) -> ModelPricing | None:
        if not self.enabled:
            return None
        if self.budget_stopped:
            raise ProbeBudgetError(f"budget stopped: {self.budget_stop_reason}")
        if self.max_prompt_chars is not None and prompt_chars > self.max_prompt_chars:
            raise ProbeBudgetError(
                f"prompt character budget exceeded: {prompt_chars} > {self.max_prompt_chars}"
            )
        if model.pricing_currency != "CREDITS" or not model.has_pricing:
            message = (
                f"budget requires priced CREDITS for routed model {model.id}; "
                f"currency={model.pricing_currency}, has_pricing={model.has_pricing}"
            )
            self.stop(message)
            raise ProbeBudgetError(message)
        return ModelPricing(
            cached_input=model.pricing_cached_input,
            input=model.pricing_input,
            output=model.pricing_output,
            currency=model.pricing_currency,
        )

    def reserve_attempt(self, output_tokens: int | None, output_cap: int) -> None:
        if not self.enabled:
            return
        if self.budget_stopped:
            raise ProbeBudgetError(f"budget stopped: {self.budget_stop_reason}")
        if output_tokens is not None and output_tokens > output_cap:
            raise ProbeBudgetError(
                f"max_output_tokens exceeds max_output_tokens_cap: "
                f"{output_tokens} > {output_cap}"
            )
        if self.max_provider_requests is not None and self.provider_attempts >= self.max_provider_requests:
            message = (
                f"provider request budget reached: {self.provider_attempts} / "
                f"{self.max_provider_requests}"
            )
            raise ProbeBudgetError(self.stop(message))
        if (self.max_configured_credits is not None
                and self.configured_credits >= self.max_configured_credits):
            message = (
                "configured Credits budget reached before provider request: "
                f"{self.configured_credits:.6f} >= {self.max_configured_credits:.6f}"
            )
            raise ProbeBudgetError(self.stop(message))
        self.provider_attempts += 1

    def unknown(self, reason: str) -> str:
        self.unknown_usage = True
        return self.stop(f"budget stopped: {reason}")

    def observe_usage(self, usage: UsageMetrics, pricing: ModelPricing) -> str | None:
        if not self.enabled:
            return None
        if usage.prompt_tokens is None or usage.completion_tokens is None:
            missing = ", ".join(
                name for name in ("prompt_tokens", "completion_tokens")
                if getattr(usage, name) is None
            )
            return self.unknown(f"usage missing {missing}; cannot account CREDITS")
        self.configured_credits += pricing.cost_of(usage)
        if (self.max_configured_credits is not None
                and self.configured_credits > self.max_configured_credits):
            return self.stop(
                "configured Credits budget exceeded after provider response "
                f"({self.configured_credits:.6f} > "
                f"{self.max_configured_credits:.6f}); last request may cross cap"
            )
        return None
