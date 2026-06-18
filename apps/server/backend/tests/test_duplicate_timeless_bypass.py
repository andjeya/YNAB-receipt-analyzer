"""Time-less duplicate near-match behavior (plan B-02, report DUP-02).

A receipt with no extracted transaction_time has no full semantic signature
(which requires payee+date+time+total). Rather than bypass duplicate detection
entirely, the detector surfaces a NON-BLOCKING near-match WARNING when a time-less
receipt matches another on payee+date+total. It never hard-blocks
(duplicate_of_receipt_id stays None), so sync remains possible after the human
verifies it is not a duplicate. A lone time-less receipt (no twin) is not flagged.
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


def test_timeless_match_surfaces_near_match_warning(db_session: Any) -> None:
    """Same payee/date/total but NO time → non-blocking near-match warning (not a block)."""
    first = _receipt("r-notime-1")
    second = _receipt("r-notime-2")
    db_session.add_all([first, second])
    db_session.flush()

    res1 = apply_semantic_duplicate_state(db_session, receipt=first, payload=_payload(None))
    db_session.flush()
    res2 = apply_semantic_duplicate_state(db_session, receipt=second, payload=_payload(None))
    db_session.flush()

    # First receipt has nothing to match against yet.
    assert res1.near_match is False

    # Second matches the first on payee/date/total → near-match WARNING, no signature,
    # and NOT a hard duplicate block.
    assert res2.signature is None
    assert res2.near_match is True
    assert res2.duplicate_of_receipt_id is None
    assert second.status != ReceiptStatus.DUPLICATE_REVIEW.value
    assert second.status_reason is not None and "Near-match" in second.status_reason


def test_lone_timeless_receipt_is_not_flagged(db_session: Any) -> None:
    """A time-less receipt with no payee/date/total twin is not flagged at all."""
    only = _receipt("r-lonely")
    db_session.add(only)
    db_session.flush()

    res = apply_semantic_duplicate_state(db_session, receipt=only, payload=_payload(None))

    assert res.signature is None
    assert res.near_match is False
    assert res.duplicate_of_receipt_id is None
    assert only.status != ReceiptStatus.DUPLICATE_REVIEW.value


def test_same_pair_with_time_is_flagged(db_session: Any) -> None:
    """Contrast: the identical pair WITH a matching time IS a hard DUPLICATE_REVIEW block.

    Confirms the time-less case is intentionally softer (near-match) than the
    full-signature case, not a regression in real duplicate detection.
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
