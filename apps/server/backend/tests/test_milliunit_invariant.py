"""Tests for the exact milliunit sum invariant in _build_sync_transaction_payload.

The payload builder is the authoritative gate: sum(subtransaction milliunits)
must equal the transaction total milliunits exactly (integer comparison).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.ynab import _build_sync_transaction_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCOUNT = "acct-1"
CAT_A = "cat-1"
CAT_B = "cat-1"  # re-use the single seeded category for simplicity


def _make_receipt(receipt_id: str = "rcpt-test") -> MagicMock:
    r = MagicMock()
    r.id = receipt_id
    return r


def _make_validation(payload: dict) -> MagicMock:
    v = MagicMock()
    v.payload = payload
    return v


def _single_payload(total: float = 50.0) -> dict:
    return {
        "account_id": ACCOUNT,
        "transaction_date": "2025-06-01",
        "total_amount": total,
        "payee_name": "Store",
        "memo": "test",
        "category_id": CAT_A,
        "splits": [],
    }


def _split_payload(total: float, splits: list[dict]) -> dict:
    return {
        "account_id": ACCOUNT,
        "transaction_date": "2025-06-01",
        "total_amount": total,
        "payee_name": "Store",
        "memo": "test",
        "splits": splits,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMilliunitInvariant:
    def test_single_category_no_splits_unaffected(
        self, db_with_cache: Session, test_settings: Settings
    ) -> None:
        """Single-category transactions have no split sum to check — must pass."""
        payload = _single_payload(total=50.0)
        result = _build_sync_transaction_payload(
            db_with_cache,
            _make_receipt(),
            _make_validation(payload),
            test_settings,
        )
        assert result["amount"] == -50000
        assert "subtransactions" not in result
        assert result["category_id"] == CAT_A

    def test_exact_sum_passes(
        self, db_with_cache: Session, test_settings: Settings
    ) -> None:
        """Two splits that sum exactly to the total in milliunits must pass."""
        # 45.74 + 73.45 = 119.19 exactly in milliunits: -45740 + -73450 = -119190
        splits = [
            {"amount": 45.74, "category_id": CAT_A, "memo": "part A"},
            {"amount": 73.45, "category_id": CAT_A, "memo": "part B"},
        ]
        payload = _split_payload(total=119.19, splits=splits)
        result = _build_sync_transaction_payload(
            db_with_cache,
            _make_receipt(),
            _make_validation(payload),
            test_settings,
        )
        assert result["amount"] == -119190
        assert len(result["subtransactions"]) == 2
        assert result["subtransactions"][0]["amount"] == -45740
        assert result["subtransactions"][1]["amount"] == -73450
        # Verify the invariant holds
        sub_sum = sum(s["amount"] for s in result["subtransactions"])
        assert sub_sum == result["amount"]

    def test_one_milliunit_drift_raises(
        self, db_with_cache: Session, test_settings: Settings
    ) -> None:
        """1-milliunit drift between split sum and total must raise ValueError."""
        # Total 10.00 → -10000 milliunits
        # Splits: 5.001 + 4.999 → round each: -5001 + -4999 = -10000 → this would pass.
        # For a genuine 1-milliunit drift: total=10.00 (-10000) but splits
        # designed to produce -10001 at the milliunit level.
        # Use 3.334 + 6.667 = 10.001 (dollar sum ≈ 10.001, but total=10.00):
        #   dollars_to_milliunits(3.334) = -3334, dollars_to_milliunits(6.667) = -6667
        #   sum = -10001 != total -10000
        splits = [
            {"amount": 3.334, "category_id": CAT_A, "memo": "a"},
            {"amount": 6.667, "category_id": CAT_A, "memo": "b"},
        ]
        payload = _split_payload(total=10.00, splits=splits)
        # Dollar-level check: |3.334+6.667 - 10.00| = 0.001 < 0.01, passes early warning.
        # Milliunit check: -3334 + -6667 = -10001 != -10000 → must raise.
        with pytest.raises(ValueError, match=r"-10001.*-10000|-10000.*-10001|milliunits"):
            _build_sync_transaction_payload(
                db_with_cache,
                _make_receipt(),
                _make_validation(payload),
                test_settings,
            )

    def test_three_decimal_poison_case_raises(
        self, db_with_cache: Session, test_settings: Settings
    ) -> None:
        """3-decimal extraction (total 10.005, splits 5.0025+5.0025) raises with clear message.

        dollars_to_milliunits(10.005)  → quantize to 0.001 = 10.005 → 10005 → -10005
        dollars_to_milliunits(5.0025)  → quantize to 0.001 = 5.003 (ROUND_HALF_UP) → 5003 → -5003
        sum of splits: -5003 + -5003 = -10006 != -10005
        """
        splits = [
            {"amount": 5.0025, "category_id": CAT_A, "memo": "half"},
            {"amount": 5.0025, "category_id": CAT_A, "memo": "other half"},
        ]
        payload = _split_payload(total=10.005, splits=splits)
        with pytest.raises(ValueError) as exc_info:
            _build_sync_transaction_payload(
                db_with_cache,
                _make_receipt(),
                _make_validation(payload),
                test_settings,
            )
        msg = str(exc_info.value)
        # Message must include both the actual sum and the expected total
        assert "-10006" in msg or "10006" in msg, f"Expected sum in message: {msg}"
        assert "-10005" in msg or "10005" in msg, f"Expected total in message: {msg}"

    def test_live_validated_case_119_19_passes(
        self, db_with_cache: Session, test_settings: Settings
    ) -> None:
        """Live-validated case: 119.19 with splits 45.74+73.45 passes exactly."""
        splits = [
            {"amount": 45.74, "category_id": CAT_A, "memo": ""},
            {"amount": 73.45, "category_id": CAT_A, "memo": ""},
        ]
        payload = _split_payload(total=119.19, splits=splits)
        result = _build_sync_transaction_payload(
            db_with_cache,
            _make_receipt(),
            _make_validation(payload),
            test_settings,
        )
        sub_sum = sum(s["amount"] for s in result["subtransactions"])
        assert sub_sum == result["amount"], (
            f"Invariant violated: sub_sum={sub_sum}, total={result['amount']}"
        )
        assert result["amount"] == -119190
