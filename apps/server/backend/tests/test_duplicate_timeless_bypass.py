"""Time-less duplicate bypass regression test (plan T1-11, report DUP-02).

CURRENT behavior: the semantic duplicate signature requires payee+date+time+total.
When `transaction_time` is missing the signature is None and duplicate detection
is skipped entirely — two receipts with the same payee/date/total but no time are
NOT flagged. This test documents that current limitation and contrasts it with
the with-time case (which DOES flag), so the gap is explicit.

The desired upgrade (a non-blocking near-match warning for time-less matches) is
tracked as backlog B-02 in test_backlog_desired_behavior.py.
"""

from __future__ import annotations

from typing import Any

from app.enums import ReceiptStatus
from app.models import Receipt
from app.services.duplicates import apply_semantic_duplicate_state


def _receipt(rid: str) -> Receipt:
    return Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="dup.jpg",
        file_hash=f"hash-dup-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )


def _payload(time_text: str | None) -> dict[str, Any]:
    return {
        "payee_name": "Costco",
        "transaction_date": "2026-06-01",
        "transaction_time": time_text,
        "total_amount": 89.21,
        "transaction_kind": "purchase",
    }


def test_timeless_receipts_are_not_flagged_as_duplicates(db_session: Any) -> None:
    """Same payee/date/total but NO time → signature None → no duplicate detection."""
    first = _receipt("r-notime-1")
    second = _receipt("r-notime-2")
    db_session.add_all([first, second])
    db_session.flush()

    res1 = apply_semantic_duplicate_state(db_session, receipt=first, payload=_payload(None))
    res2 = apply_semantic_duplicate_state(db_session, receipt=second, payload=_payload(None))
    db_session.flush()

    # No signature is computed, so nothing can match.
    assert res1.signature is None
    assert res2.signature is None
    assert res2.duplicate_of_receipt_id is None
    assert second.status != ReceiptStatus.DUPLICATE_REVIEW.value


def test_same_pair_with_time_is_flagged(db_session: Any) -> None:
    """Contrast: the identical pair WITH a matching time IS flagged DUPLICATE_REVIEW.

    Proves the bypass above is caused specifically by the missing time, not by a
    non-matching payload.
    """
    first = _receipt("r-time-1")
    second = _receipt("r-time-2")
    db_session.add_all([first, second])
    db_session.flush()

    apply_semantic_duplicate_state(db_session, receipt=first, payload=_payload("09:41"))
    db_session.flush()
    res2 = apply_semantic_duplicate_state(db_session, receipt=second, payload=_payload("09:41"))
    db_session.flush()

    assert res2.signature is not None
    assert res2.duplicate_of_receipt_id == first.id
    assert second.status == ReceiptStatus.DUPLICATE_REVIEW.value
