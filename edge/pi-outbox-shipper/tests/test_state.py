from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from shipper.state import DeliveryStateStore, compute_backoff_seconds


def test_compute_backoff_seconds_exponential_with_cap() -> None:
    assert compute_backoff_seconds(1, 5, 60) == 5
    assert compute_backoff_seconds(2, 5, 60) == 10
    assert compute_backoff_seconds(3, 5, 60) == 20
    assert compute_backoff_seconds(5, 5, 60) == 60


def test_retry_state_transitions(tmp_path: Path) -> None:
    store = DeliveryStateStore(tmp_path / "state.db")
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

    record = store.ensure_record("receipt.pdf", now=now)
    assert record.status == "pending"
    assert store.due_for_send("receipt.pdf", now=now)

    first_failure = store.mark_retry(
        "receipt.pdf",
        error_text="network down",
        backoff_seconds=5,
        now=now,
    )
    assert first_failure.status == "retry"
    assert first_failure.attempts == 1
    assert first_failure.next_retry_at == now + timedelta(seconds=5)
    assert not store.due_for_send("receipt.pdf", now=now + timedelta(seconds=4))
    assert store.due_for_send("receipt.pdf", now=now + timedelta(seconds=5))

    second_failure = store.mark_retry(
        "receipt.pdf",
        error_text="nas offline",
        backoff_seconds=10,
        now=now + timedelta(seconds=6),
    )
    assert second_failure.attempts == 2
    assert second_failure.next_retry_at == now + timedelta(seconds=16)

    sent = store.mark_sent("receipt.pdf", now=now + timedelta(seconds=20))
    assert sent.status == "sent"
    assert sent.sent_at == now + timedelta(seconds=20)
    assert not store.due_for_send("receipt.pdf", now=now + timedelta(seconds=30))
