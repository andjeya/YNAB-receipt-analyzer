from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .clock import Clock, SystemClock
from .limits import LimitsConfigRepository
from .providers import GeminiProvider
from .providers.base import AIProvider
from .registry import ModelRegistryRepository
from .store import UsageLedgerStore
from .types import (
    AIError,
    AIProviderError,
    AIRequest,
    AIResponse,
    AILimitExceededError,
    TokenUsage,
)

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "resources" / "ai_model_registry.v1.json"
DEFAULT_LIMITS_PATH = Path("./config/ai_limits.v1.json")
DEFAULT_USAGE_DB_URL = "sqlite:///./data/ai_usage.db"


class AIClient:
    def __init__(
        self,
        *,
        api_key: str,
        max_retries: int = 3,
        registry_path: Path | None = None,
        limits_path: Path | None = None,
        usage_db_url: str | None = None,
        clock: Clock | None = None,
        providers: Mapping[str, AIProvider] | None = None,
    ):
        resolved_registry = Path(os.environ.get("AI_MODEL_REGISTRY_PATH", "")).expanduser() if os.environ.get("AI_MODEL_REGISTRY_PATH") else (registry_path or DEFAULT_REGISTRY_PATH)
        resolved_limits = Path(os.environ.get("AI_LIMITS_CONFIG_PATH", "")).expanduser() if os.environ.get("AI_LIMITS_CONFIG_PATH") else (limits_path or DEFAULT_LIMITS_PATH)
        resolved_usage_db = usage_db_url or os.environ.get("AI_USAGE_DB_URL") or DEFAULT_USAGE_DB_URL

        self.registry_repo = ModelRegistryRepository(resolved_registry)
        self.limits_repo = LimitsConfigRepository(resolved_limits)
        self.store = UsageLedgerStore(resolved_usage_db)
        self.clock = clock or SystemClock()

        if providers is not None:
            self.providers: dict[str, AIProvider] = dict(providers)
        else:
            self.providers = {
                "google_gemini": GeminiProvider(api_key=api_key, max_retries=max_retries),
            }

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, object]) -> dict[str, object]:
        blocked = {
            "prompt",
            "prompt_text",
            "raw_prompt",
            "receipt_text",
            "receipt_content",
            "input_text",
            "contents",
        }
        sanitized: dict[str, object] = {}
        for key, value in metadata.items():
            if key in blocked:
                continue
            sanitized[key] = value
        return sanitized

    @staticmethod
    def _empty_usage() -> TokenUsage:
        return TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)

    def _with_request_ids(self, request: AIRequest) -> tuple[str, str]:
        request_id = request.request_id or str(uuid.uuid4())
        correlation_id = request.correlation_id or request_id
        return request_id, correlation_id

    def _generate(self, request: AIRequest) -> AIResponse:
        started = datetime.now(timezone.utc)
        request_id, correlation_id = self._with_request_ids(request)

        registry = self.registry_repo.load()
        model = registry.require(request.model_id)

        provider = self.providers.get(model.provider)
        if provider is None:
            raise ValueError(
                f"No provider adapter configured for provider='{model.provider}' model='{request.model_id}'"
            )

        estimated_usage = provider.estimate_usage(request, model).with_total_if_missing()
        estimated_cost = model.pricing.compute_cost_usd(estimated_usage)
        metadata = self._sanitize_metadata(dict(request.metadata))

        reservation = self.store.reserve(
            timestamp_utc=self.clock.now_utc(),
            provider=model.provider,
            model_id=model.model_id,
            request_id=request_id,
            correlation_id=correlation_id,
            route=request.route,
            estimated_usage=estimated_usage,
            estimated_cost_usd=estimated_cost,
            limits=self.limits_repo.load(),
            pricing_version=model.pricing.version,
            metadata={
                **metadata,
                "usage_estimated": True,
            },
        )

        if not reservation.allowed:
            if request.limit_behavior == "soft_fail":
                duration_ms = int((self.clock.now_utc() - started).total_seconds() * 1000)
                return AIResponse(
                    status="limit_rejected",
                    text="",
                    parsed=None,
                    usage=self._empty_usage(),
                    cost_usd=Decimal("0"),
                    request_id=request_id,
                    duration_ms=max(duration_ms, 0),
                    error=AIError(
                        code="limit_exceeded",
                        message=f"AI usage limit exceeded for model '{model.model_id}'",
                        details={
                            "model_id": model.model_id,
                            "violations": [
                                {
                                    "scope": item.scope,
                                    "window": item.window,
                                    "dimension": item.dimension,
                                    "limit": str(item.limit),
                                    "current": str(item.current),
                                    "projected": str(item.projected),
                                }
                                for item in reservation.violations
                            ],
                        },
                    ),
                )

            raise AILimitExceededError(model.model_id, request_id, reservation.violations)

        assert reservation.event_id is not None

        try:
            provider_result = provider.generate(request, model)
        except Exception as exc:
            self.store.finalize(
                event_id=reservation.event_id,
                status="provider_error",
                usage=self._empty_usage(),
                cost_usd=Decimal("0"),
                error_text=str(exc),
                metadata_updates={"usage_estimated": False},
            )
            raise AIProviderError(model.model_id, request_id, str(exc)) from exc

        final_usage = provider_result.usage.with_total_if_missing()
        if final_usage.total_tokens is None:
            # Preserve an auditable approximation when provider usage is unavailable.
            final_usage = TokenUsage(
                input_tokens=estimated_usage.input_tokens,
                output_tokens=estimated_usage.output_tokens,
                cached_input_tokens=estimated_usage.cached_input_tokens,
                total_tokens=estimated_usage.total_tokens,
                extra_dimensions=dict(estimated_usage.extra_dimensions),
                estimated=True,
            )

        final_cost = model.pricing.compute_cost_usd(final_usage)
        self.store.finalize(
            event_id=reservation.event_id,
            status="success",
            usage=final_usage,
            cost_usd=final_cost,
            metadata_updates={"usage_estimated": final_usage.estimated},
        )

        return AIResponse(
            status="success",
            text=provider_result.text,
            parsed=provider_result.parsed,
            usage=final_usage,
            cost_usd=final_cost,
            request_id=request_id,
            duration_ms=provider_result.duration_ms,
            error=None,
        )

    def generate_text(self, request: AIRequest) -> AIResponse:
        return self._generate(request)

    def generate_structured(self, request: AIRequest, *, schema: type | None = None) -> AIResponse:
        if schema is None:
            return self._generate(request)

        request_with_schema = AIRequest(
            model_id=request.model_id,
            prompt_text=request.prompt_text,
            file_path=request.file_path,
            mime_type=request.mime_type,
            response_schema=schema,
            route=request.route,
            metadata=dict(request.metadata),
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            limit_behavior=request.limit_behavior,
        )
        return self._generate(request_with_schema)
