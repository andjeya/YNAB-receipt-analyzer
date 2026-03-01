from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .types import TokenUsage, UnknownModelError

SUPPORTED_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PricingDimension:
    unit: str
    price_usd: Decimal


@dataclass(frozen=True)
class ModelPricing:
    currency: str
    version: str
    dimensions: dict[str, PricingDimension]
    assumptions: list[str] = field(default_factory=list)

    def _unit_scale(self, unit: str) -> Decimal:
        normalized = unit.strip().lower()
        if normalized == "1m_tokens":
            return Decimal("1000000")
        if normalized == "1m_token_hours":
            return Decimal("1000000")
        if normalized == "1k_tokens":
            return Decimal("1000")
        if normalized == "token":
            return Decimal("1")
        raise ValueError(f"Unsupported pricing unit: {unit}")

    def compute_cost_usd(self, usage: TokenUsage) -> Decimal:
        usage_full = usage.with_total_if_missing()
        total = Decimal("0")
        dimension_values: dict[str, int] = {}
        if usage_full.input_tokens is not None:
            dimension_values["input_tokens"] = usage_full.input_tokens
        if usage_full.output_tokens is not None:
            dimension_values["output_tokens"] = usage_full.output_tokens
        if usage_full.cached_input_tokens is not None:
            dimension_values["cached_input_tokens"] = usage_full.cached_input_tokens
        for key, value in usage_full.extra_dimensions.items():
            dimension_values[key] = value

        for key, value in dimension_values.items():
            if key not in self.dimensions:
                continue
            dimension = self.dimensions[key]
            scale = self._unit_scale(dimension.unit)
            total += (Decimal(str(value)) / scale) * dimension.price_usd

        return total.quantize(Decimal("0.00000001"))


@dataclass(frozen=True)
class ModelDefinition:
    model_id: str
    provider: str
    provider_model: str
    pricing: ModelPricing
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def estimated_output_tokens(self) -> int:
        raw = self.metadata.get("default_output_tokens_estimate", 1024)
        try:
            parsed = int(raw)
        except Exception:
            return 1024
        return max(parsed, 0)


@dataclass(frozen=True)
class ModelRegistry:
    schema_version: int
    models: dict[str, ModelDefinition]

    def require(self, model_id: str) -> ModelDefinition:
        model = self.models.get(model_id)
        if model is None:
            raise UnknownModelError(model_id, list(self.models.keys()))
        return model

    def available_model_ids(self) -> list[str]:
        return sorted(self.models.keys())


class ModelRegistryRepository:
    def __init__(self, path: Path):
        self.path = path
        self._cached_registry: ModelRegistry | None = None
        self._cached_mtime_ns: int | None = None

    def _parse_registry(self, payload: dict[str, Any]) -> ModelRegistry:
        schema_version = int(payload.get("schema_version", 0))
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported model registry schema_version={schema_version}; expected {SUPPORTED_SCHEMA_VERSION}"
            )

        models_payload = payload.get("models")
        if not isinstance(models_payload, list):
            raise ValueError("Model registry must include a list at 'models'")

        models: dict[str, ModelDefinition] = {}
        for entry in models_payload:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("id", "")).strip()
            provider = str(entry.get("provider", "")).strip()
            provider_model = str(entry.get("provider_model", model_id)).strip()
            pricing_payload = entry.get("pricing", {})
            if not model_id or not provider:
                continue

            dimensions_payload = pricing_payload.get("dimensions", {})
            dimensions: dict[str, PricingDimension] = {}
            if isinstance(dimensions_payload, dict):
                for key, dim in dimensions_payload.items():
                    if not isinstance(dim, dict):
                        continue
                    unit = str(dim.get("unit", "1M_tokens")).strip()
                    price = Decimal(str(dim.get("price_usd", "0")))
                    dimensions[key] = PricingDimension(unit=unit, price_usd=price)

            pricing = ModelPricing(
                currency=str(pricing_payload.get("currency", "USD")).upper(),
                version=str(pricing_payload.get("version", "unknown")),
                dimensions=dimensions,
                assumptions=[str(item) for item in pricing_payload.get("assumptions", []) if isinstance(item, str)],
            )
            metadata = entry.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}

            models[model_id] = ModelDefinition(
                model_id=model_id,
                provider=provider,
                provider_model=provider_model,
                pricing=pricing,
                metadata=metadata,
            )

        if not models:
            raise ValueError("Model registry does not define any models")

        return ModelRegistry(schema_version=schema_version, models=models)

    def load(self, *, force_reload: bool = False) -> ModelRegistry:
        if not self.path.exists():
            raise FileNotFoundError(f"Model registry file not found: {self.path}")

        mtime_ns = self.path.stat().st_mtime_ns
        if (
            not force_reload
            and self._cached_registry is not None
            and self._cached_mtime_ns is not None
            and self._cached_mtime_ns == mtime_ns
        ):
            return self._cached_registry

        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            raise ValueError("Model registry JSON must be an object")

        parsed = self._parse_registry(payload)
        self._cached_registry = parsed
        self._cached_mtime_ns = mtime_ns
        return parsed
