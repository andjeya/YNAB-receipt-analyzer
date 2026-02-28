from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from receipt_shared.ai import AIClient, AIRequest, AILimitExceededError, TokenUsage
from receipt_shared.ai.types import ProviderResult


class _FixedClock:
    def __init__(self, now: datetime):
        self._now = now

    def now_utc(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class _FakeProvider:
    def __init__(self, usage_map: dict[str, TokenUsage], *, sleep_seconds: float = 0.0):
        self.usage_map = usage_map
        self.sleep_seconds = sleep_seconds
        self.call_count = 0
        self.lock = threading.Lock()

    def estimate_usage(self, request: AIRequest, model):
        return self.usage_map.get(request.model_id, TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0))

    def generate(self, request: AIRequest, model):
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        with self.lock:
            self.call_count += 1
        usage = self.usage_map.get(request.model_id, TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0))
        return ProviderResult(text='{"ok":true}', parsed={"ok": True}, usage=usage, duration_ms=5)


def _write_registry(path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "models": [
            {
                "id": "model-a",
                "provider": "google_gemini",
                "provider_model": "model-a",
                "pricing": {
                    "currency": "USD",
                    "version": "test",
                    "dimensions": {
                        "input_tokens": {"unit": "1M_tokens", "price_usd": 1.0},
                        "output_tokens": {"unit": "1M_tokens", "price_usd": 1.0},
                    },
                },
                "metadata": {"default_output_tokens_estimate": 0},
            },
            {
                "id": "model-b",
                "provider": "google_gemini",
                "provider_model": "model-b",
                "pricing": {
                    "currency": "USD",
                    "version": "test",
                    "dimensions": {
                        "input_tokens": {"unit": "1M_tokens", "price_usd": 1.0},
                        "output_tokens": {"unit": "1M_tokens", "price_usd": 1.0},
                    },
                },
                "metadata": {"default_output_tokens_estimate": 0},
            },
        ],
    }
    target = path / "registry.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _write_limits(path: Path, payload: dict) -> Path:
    target = path / "limits.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _base_limits() -> dict:
    return {
        "schema_version": 1,
        "global": {
            "hourly": {"unlimited": True, "tokens": None, "usd": None},
            "daily": {"unlimited": True, "tokens": None, "usd": None},
            "weekly": {"unlimited": True, "tokens": None, "usd": None},
            "monthly": {"unlimited": True, "tokens": None, "usd": None},
        },
        "models": {
            "model-a": {
                "hourly": {"unlimited": True, "tokens": None, "usd": None},
                "daily": {"unlimited": True, "tokens": None, "usd": None},
                "weekly": {"unlimited": True, "tokens": None, "usd": None},
                "monthly": {"unlimited": True, "tokens": None, "usd": None},
            },
            "model-b": {
                "hourly": {"unlimited": True, "tokens": None, "usd": None},
                "daily": {"unlimited": True, "tokens": None, "usd": None},
                "weekly": {"unlimited": True, "tokens": None, "usd": None},
                "monthly": {"unlimited": True, "tokens": None, "usd": None},
            },
        },
    }


def _build_client(
    *,
    tmp_path: Path,
    limits_payload: dict,
    usage_map: dict[str, TokenUsage],
    clock: _FixedClock,
    sleep_seconds: float = 0.0,
):
    registry_path = _write_registry(tmp_path)
    limits_path = _write_limits(tmp_path, limits_payload)
    db_url = f"sqlite:///{tmp_path / 'ai_usage.db'}"

    provider = _FakeProvider(usage_map=usage_map, sleep_seconds=sleep_seconds)
    client = AIClient(
        api_key="test-key",
        registry_path=registry_path,
        limits_path=limits_path,
        usage_db_url=db_url,
        clock=clock,
        providers={"google_gemini": provider},
    )
    return SimpleNamespace(client=client, provider=provider)


def test_limit_not_reached_allows_call(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["daily"] = {"unlimited": False, "tokens": 100, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)},
        clock=clock,
    )

    response = setup.client.generate_text(
        AIRequest(model_id="model-a", prompt_text="hello", limit_behavior="hard_fail")
    )

    assert response.status == "success"
    assert setup.provider.call_count == 1


def test_limit_reached_rejects_before_provider_call(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["daily"] = {"unlimited": False, "tokens": 10, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=8, output_tokens=8, total_tokens=16)},
        clock=clock,
    )

    with pytest.raises(AILimitExceededError):
        setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="hello"))

    assert setup.provider.call_count == 0


def test_soft_fail_returns_structured_error(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["daily"] = {"unlimited": False, "tokens": 5, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=6, output_tokens=2, total_tokens=8)},
        clock=clock,
    )

    response = setup.client.generate_text(
        AIRequest(model_id="model-a", prompt_text="hello", limit_behavior="soft_fail")
    )

    assert response.status == "limit_rejected"
    assert response.error is not None
    assert response.error.code == "limit_exceeded"
    assert setup.provider.call_count == 0


def test_unknown_model_fails_fast_with_helpful_error(tmp_path: Path):
    limits = _base_limits()
    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)},
        clock=clock,
    )

    with pytest.raises(Exception) as exc:
        setup.client.generate_text(AIRequest(model_id="unknown-model", prompt_text="hello"))

    message = str(exc.value)
    assert "Unknown model 'unknown-model'" in message
    assert "model-a" in message
    assert setup.provider.call_count == 0


def test_unlimited_windows_never_reject(tmp_path: Path):
    limits = _base_limits()
    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=1000, output_tokens=1000, total_tokens=2000)},
        clock=clock,
    )

    for _ in range(5):
        response = setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="hello"))
        assert response.status == "success"

    assert setup.provider.call_count == 5


def test_multiple_windows_reject_if_any_window_exceeded(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["daily"] = {"unlimited": False, "tokens": 1000, "usd": None}
    limits["global"]["monthly"] = {"unlimited": False, "tokens": 100, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=90, output_tokens=0, total_tokens=90)},
        clock=clock,
    )

    first = setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="seed"))
    assert first.status == "success"

    setup.provider.usage_map["model-a"] = TokenUsage(input_tokens=15, output_tokens=0, total_tokens=15)
    with pytest.raises(AILimitExceededError) as exc:
        setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="next"))

    assert any(item.window == "monthly" for item in exc.value.violations)


def test_per_model_and_global_limits_both_enforced(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["daily"] = {"unlimited": False, "tokens": 100, "usd": None}
    limits["models"]["model-a"]["daily"] = {"unlimited": False, "tokens": 80, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={
            "model-a": TokenUsage(input_tokens=70, output_tokens=0, total_tokens=70),
            "model-b": TokenUsage(input_tokens=35, output_tokens=0, total_tokens=35),
        },
        clock=clock,
    )

    assert setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="first")).status == "success"

    setup.provider.usage_map["model-a"] = TokenUsage(input_tokens=15, output_tokens=0, total_tokens=15)
    with pytest.raises(AILimitExceededError) as model_exc:
        setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="model-limit"))
    assert any(v.scope == "model" for v in model_exc.value.violations)

    with pytest.raises(AILimitExceededError) as global_exc:
        setup.client.generate_text(AIRequest(model_id="model-b", prompt_text="global-limit"))
    assert any(v.scope == "global" for v in global_exc.value.violations)


def test_concurrent_requests_do_not_exceed_cap(tmp_path: Path):
    limits = _base_limits()
    limits["global"]["hourly"] = {"unlimited": False, "tokens": 100, "usd": None}

    clock = _FixedClock(datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc))
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=60, output_tokens=0, total_tokens=60)},
        clock=clock,
        sleep_seconds=0.2,
    )

    outcomes: list[str] = []
    errors: list[Exception] = []

    def _run_one() -> None:
        try:
            result = setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="race"))
            outcomes.append(result.status)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run_one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("success") == 1
    assert any(isinstance(exc, AILimitExceededError) for exc in errors)
    assert setup.provider.call_count == 1


@pytest.mark.parametrize(
    "window,first_ts,second_ts",
    [
        (
            "hourly",
            datetime(2026, 2, 28, 10, 59, tzinfo=timezone.utc),
            datetime(2026, 2, 28, 11, 0, tzinfo=timezone.utc),
        ),
        (
            "daily",
            datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        ),
        (
            "weekly",
            datetime(2026, 3, 1, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
        ),
        (
            "monthly",
            datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_window_boundaries_reset_usage(window: str, first_ts: datetime, second_ts: datetime, tmp_path: Path):
    limits = _base_limits()
    limits["global"][window] = {"unlimited": False, "tokens": 10, "usd": None}

    clock = _FixedClock(first_ts)
    setup = _build_client(
        tmp_path=tmp_path,
        limits_payload=limits,
        usage_map={"model-a": TokenUsage(input_tokens=10, output_tokens=0, total_tokens=10)},
        clock=clock,
    )

    assert setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="first")).status == "success"

    clock.set(second_ts)
    assert setup.client.generate_text(AIRequest(model_id="model-a", prompt_text="second")).status == "success"
