from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

LimitBehavior = Literal["hard_fail", "soft_fail"]
ResponseStatus = Literal["success", "limit_rejected", "provider_error"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None
    extra_dimensions: dict[str, int] = field(default_factory=dict)
    estimated: bool = False

    def with_total_if_missing(self) -> "TokenUsage":
        if self.total_tokens is not None:
            return self
        known_total = 0
        has_known_component = False
        for value in (self.input_tokens, self.output_tokens, self.cached_input_tokens):
            if value is not None:
                known_total += int(value)
                has_known_component = True
        for value in self.extra_dimensions.values():
            known_total += int(value)
            has_known_component = True
        if not has_known_component:
            return self
        return TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=known_total,
            extra_dimensions=dict(self.extra_dimensions),
            estimated=self.estimated,
        )

    @property
    def limit_tokens(self) -> int:
        value = self.with_total_if_missing().total_tokens
        return int(value or 0)


@dataclass(frozen=True)
class AIRequest:
    model_id: str
    prompt_text: str
    file_path: Path | None = None
    mime_type: str | None = None
    response_schema: type[BaseModel] | None = None
    route: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    correlation_id: str | None = None
    limit_behavior: LimitBehavior = "hard_fail"


@dataclass(frozen=True)
class AIError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIResponse:
    status: ResponseStatus
    text: str
    parsed: Any
    usage: TokenUsage
    cost_usd: Decimal
    request_id: str
    duration_ms: int
    error: AIError | None = None


@dataclass(frozen=True)
class ProviderResult:
    text: str
    parsed: Any
    usage: TokenUsage
    duration_ms: int


@dataclass(frozen=True)
class LimitViolation:
    scope: Literal["global", "model"]
    model_id: str | None
    window: Literal["hourly", "daily", "weekly", "monthly"]
    dimension: Literal["tokens", "usd"]
    limit: int | Decimal
    current: int | Decimal
    projected: int | Decimal


@dataclass(frozen=True)
class ReservationResult:
    allowed: bool
    event_id: int | None
    violations: list[LimitViolation] = field(default_factory=list)


@dataclass(frozen=True)
class UsageWindowTotals:
    tokens: int
    usd: Decimal


@dataclass(frozen=True)
class LedgerEvent:
    id: int
    timestamp_utc: datetime
    provider: str
    model_id: str
    request_id: str
    correlation_id: str | None
    route: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal
    status: str
    pricing_version: str | None
    metadata: dict[str, Any]
    error_text: str | None


class AIUsageError(RuntimeError):
    """Base exception for AI usage gateway failures."""


class UnknownModelError(AIUsageError):
    def __init__(self, model_id: str, available_models: list[str]):
        joined = ", ".join(sorted(available_models)) if available_models else "(none)"
        super().__init__(f"Unknown model '{model_id}'. Add it to the model registry. Available: {joined}")
        self.model_id = model_id
        self.available_models = available_models


class AILimitExceededError(AIUsageError):
    def __init__(self, model_id: str, request_id: str, violations: list[LimitViolation]):
        detail = "; ".join(
            f"{v.scope}:{v.window}:{v.dimension} projected={v.projected} limit={v.limit}"
            for v in violations
        )
        super().__init__(f"AI usage limit exceeded for model '{model_id}' request_id={request_id}: {detail}")
        self.model_id = model_id
        self.request_id = request_id
        self.violations = violations


class AIProviderError(AIUsageError):
    def __init__(self, model_id: str, request_id: str, message: str):
        super().__init__(f"AI provider call failed for model '{model_id}' request_id={request_id}: {message}")
        self.model_id = model_id
        self.request_id = request_id
