from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .windows import WINDOWS, WindowName

SUPPORTED_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WindowLimit:
    unlimited: bool = False
    tokens: int | None = None
    usd: Decimal | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "unlimited": self.unlimited,
            "tokens": self.tokens,
            "usd": None if self.usd is None else float(self.usd),
        }


@dataclass(frozen=True)
class LimitsConfig:
    schema_version: int
    global_limits: dict[WindowName, WindowLimit] = field(default_factory=dict)
    model_limits: dict[str, dict[WindowName, WindowLimit]] = field(default_factory=dict)

    def get_global(self, window: WindowName) -> WindowLimit:
        return self.global_limits.get(window, WindowLimit(unlimited=True))

    def get_model(self, model_id: str, window: WindowName) -> WindowLimit:
        model_windows = self.model_limits.get(model_id, {})
        return model_windows.get(window, WindowLimit(unlimited=True))

    def all_models(self) -> list[str]:
        return sorted(self.model_limits.keys())

    def to_json_dict(self) -> dict[str, Any]:
        models_payload: dict[str, dict[str, Any]] = {}
        for model_id, window_map in self.model_limits.items():
            models_payload[model_id] = {window: limit.to_json() for window, limit in window_map.items()}

        return {
            "schema_version": self.schema_version,
            "global": {window: self.get_global(window).to_json() for window in WINDOWS},
            "models": models_payload,
        }


DEFAULT_LIMITS = LimitsConfig(
    schema_version=SUPPORTED_SCHEMA_VERSION,
    global_limits={window: WindowLimit(unlimited=True) for window in WINDOWS},
    model_limits={},
)


def _parse_window_limit(payload: Any) -> WindowLimit:
    if payload is None:
        return WindowLimit(unlimited=True)
    if isinstance(payload, (int, float)):
        return WindowLimit(unlimited=False, tokens=int(payload), usd=None)
    if not isinstance(payload, dict):
        return WindowLimit(unlimited=True)

    unlimited = bool(payload.get("unlimited", False))

    tokens_raw = payload.get("tokens")
    tokens = None
    if tokens_raw is not None:
        try:
            tokens = max(int(tokens_raw), 0)
        except Exception:
            tokens = None

    usd_raw = payload.get("usd")
    usd = None
    if usd_raw is not None:
        try:
            usd = max(Decimal(str(usd_raw)), Decimal("0"))
        except Exception:
            usd = None

    if unlimited:
        return WindowLimit(unlimited=True, tokens=tokens, usd=usd)

    if tokens is None and usd is None:
        return WindowLimit(unlimited=True)
    return WindowLimit(unlimited=False, tokens=tokens, usd=usd)


class LimitsConfigRepository:
    def __init__(self, path: Path):
        self.path = path
        self._cached_limits: LimitsConfig | None = None
        self._cached_mtime_ns: int | None = None

    def _parse(self, payload: dict[str, Any]) -> LimitsConfig:
        schema_version = int(payload.get("schema_version", 0))
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported limits schema_version={schema_version}; expected {SUPPORTED_SCHEMA_VERSION}"
            )

        global_payload = payload.get("global", {})
        global_limits: dict[WindowName, WindowLimit] = {}
        for window in WINDOWS:
            value = global_payload.get(window) if isinstance(global_payload, dict) else None
            global_limits[window] = _parse_window_limit(value)

        model_limits: dict[str, dict[WindowName, WindowLimit]] = {}
        models_payload = payload.get("models", {})
        if isinstance(models_payload, dict):
            for model_id, limits_payload in models_payload.items():
                if not isinstance(model_id, str) or not isinstance(limits_payload, dict):
                    continue
                parsed_windows: dict[WindowName, WindowLimit] = {}
                for window in WINDOWS:
                    parsed_windows[window] = _parse_window_limit(limits_payload.get(window))
                model_limits[model_id] = parsed_windows

        return LimitsConfig(
            schema_version=schema_version,
            global_limits=global_limits,
            model_limits=model_limits,
        )

    def parse_payload(self, payload: dict[str, Any]) -> LimitsConfig:
        return self._parse(payload)

    def load(self, *, force_reload: bool = False) -> LimitsConfig:
        if not self.path.exists():
            return DEFAULT_LIMITS

        mtime_ns = self.path.stat().st_mtime_ns
        if (
            not force_reload
            and self._cached_limits is not None
            and self._cached_mtime_ns is not None
            and self._cached_mtime_ns == mtime_ns
        ):
            return self._cached_limits

        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, dict):
            raise ValueError("Limits config JSON must be an object")

        parsed = self._parse(payload)
        self._cached_limits = parsed
        self._cached_mtime_ns = mtime_ns
        return parsed

    def save(self, limits: LimitsConfig) -> None:
        payload = limits.to_json_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self.path)

        self._cached_limits = limits
        self._cached_mtime_ns = self.path.stat().st_mtime_ns
