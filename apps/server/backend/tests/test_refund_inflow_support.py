"""Tests for refund/inflow support (Opus spec implementation).

Covers:
- money.py: negative rejection + -0.0004 boundary
- contracts.py: transaction_kind validation + TRANSACTION_KINDS constant
- validation.py: refund kind valid / negative-total message / default purchase / invalid kind
- ynab.py: refund total +12340, purchase -12340, refund splits all-positive,
           purchase splits all-negative, memo prefix applied/idempotent/marker-at-end,
           adopt inflow→refund kind, adopt mixed-sign→NEEDS_REVIEW no overwrite
- reconciliation.py: inflow→refund kind, mixed→prior payload unchanged
- duplicates.py: signature excludes transaction_kind
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from typing import Any

from receipt_shared.money import dollars_to_milliunits
from receipt_shared.contracts import TRANSACTION_KINDS, ValidationPayload
from app.services.validation import validate_payload, build_initial_validation_payload
from app.services.ynab import (
    REFUND_MEMO_PREFIX,
    _build_sync_transaction_payload,
    _ensure_refund_memo_prefix,
    _append_receipt_id_marker,
)
from app.services.reconciliation import _build_corrected_payload
from app.services.duplicates import build_semantic_signature


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-groceries"
CATEGORY_B = "cat-household"
RECEIPT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _make_validation(payload: dict[str, Any]) -> Any:
    """Create a mock Validation object with the given payload."""
    v = MagicMock()
    v.payload = payload
    return v


def _make_receipt(receipt_id: str = RECEIPT_ID) -> Any:
    r = MagicMock()
    r.id = receipt_id
    r.status = "needs_review"
    r.status_reason = None
    return r


def _make_settings() -> Any:
    s = MagicMock()
    s.ynab_default_account_id = None
    return s


def _make_db_with_cache(
    account_ids: list[str] | None = None,
    category_ids: list[str] | None = None,
) -> Any:
    from app.services.ynab import get_cached_reference_data
    db = MagicMock()

    def _make_entity(entity_id: str) -> Any:
        e = MagicMock()
        e.entity_id = entity_id
        return e

    accts = [_make_entity(a) for a in (account_ids or [ACCOUNT_ID])]
    cats = [_make_entity(c) for c in (category_ids or [CATEGORY_ID, CATEGORY_B])]

    with patch("app.services.ynab.get_cached_reference_data") as mock_ref:
        mock_ref.return_value = {"accounts": accts, "categories": cats, "payees": []}
        yield db, mock_ref


# ---------------------------------------------------------------------------
# 1. money.py — negative rejection + boundary
# ---------------------------------------------------------------------------


class TestMoneyNegativeRejection:
    def test_negative_dollar_raises(self):
        with pytest.raises(ValueError, match="non-negative amount"):
            dollars_to_milliunits(-5.0)

    def test_negative_dollar_outflow_false_also_raises(self):
        with pytest.raises(ValueError, match="non-negative amount"):
            dollars_to_milliunits(-1.0, outflow=False)

    def test_negative_0004_quantizes_to_zero_and_is_accepted(self):
        # -0.0004 → quantize(0.001, ROUND_HALF_UP) → 0.000 → accepted; returns 0
        result = dollars_to_milliunits(-0.0004)
        assert result == 0

    def test_negative_0004_outflow_false_accepted(self):
        assert dollars_to_milliunits(-0.0004, outflow=False) == 0

    def test_slightly_over_boundary_still_raises(self):
        # -0.0005 rounds to -0.001 which is still negative → rejected
        with pytest.raises(ValueError, match="non-negative amount"):
            dollars_to_milliunits(-0.0005)


# ---------------------------------------------------------------------------
# 2. contracts.py — TRANSACTION_KINDS + ValidationPayload.transaction_kind
# ---------------------------------------------------------------------------


class TestTransactionKindContract:
    def test_transaction_kinds_tuple(self):
        assert TRANSACTION_KINDS == ("purchase", "refund")

    def test_default_is_purchase(self):
        p = ValidationPayload.model_validate(
            {
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-01-01",
                "memo": "test",
                "total_amount": 10.0,
                "category_id": "cat-1",
                "splits": [],
            }
        )
        assert p.transaction_kind == "purchase"

    def test_refund_kind_accepted(self):
        p = ValidationPayload.model_validate(
            {
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-01-01",
                "memo": "test",
                "total_amount": 10.0,
                "transaction_kind": "refund",
                "category_id": "cat-1",
                "splits": [],
            }
        )
        assert p.transaction_kind == "refund"

    def test_invalid_kind_rejected(self):
        with pytest.raises(Exception, match="transaction_kind must be one of"):
            ValidationPayload.model_validate(
                {
                    "payee_name": "Store",
                    "account_id": "acct-1",
                    "transaction_date": "2026-01-01",
                    "memo": "test",
                    "total_amount": 10.0,
                    "transaction_kind": "expense",
                    "category_id": "cat-1",
                    "splits": [],
                }
            )

    def test_none_normalizes_to_purchase(self):
        p = ValidationPayload.model_validate(
            {
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-01-01",
                "memo": "test",
                "total_amount": 10.0,
                "transaction_kind": None,
                "category_id": "cat-1",
                "splits": [],
            }
        )
        assert p.transaction_kind == "purchase"

    def test_empty_string_normalizes_to_purchase(self):
        p = ValidationPayload.model_validate(
            {
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-01-01",
                "memo": "test",
                "total_amount": 10.0,
                "transaction_kind": "  ",
                "category_id": "cat-1",
                "splits": [],
            }
        )
        assert p.transaction_kind == "purchase"

    def test_case_insensitive_normalization(self):
        p = ValidationPayload.model_validate(
            {
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-01-01",
                "memo": "test",
                "total_amount": 10.0,
                "transaction_kind": "REFUND",
                "category_id": "cat-1",
                "splits": [],
            }
        )
        assert p.transaction_kind == "refund"


# ---------------------------------------------------------------------------
# 3. validation.py — negative total message / kind passthrough
# ---------------------------------------------------------------------------


class TestValidationPayloadKind:
    def _base(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "test",
            "total_amount": 10.0,
            "category_id": CATEGORY_ID,
            "splits": [],
        }
        base.update(overrides)
        return base

    def test_negative_total_message_updated(self):
        _, is_valid, errors = validate_payload(self._base(total_amount=-5.0))
        assert not is_valid
        assert any("use transaction_kind='refund'" in e for e in errors)

    def test_refund_kind_accepted_in_validate_payload(self):
        _, is_valid, errors = validate_payload(self._base(transaction_kind="refund"))
        assert is_valid
        assert errors == []

    def test_default_kind_purchase_in_normalized_output(self):
        normalized, _, _ = validate_payload(self._base())
        assert normalized.get("transaction_kind") == "purchase"

    def test_refund_kind_preserved_in_normalized_output(self):
        normalized, _, _ = validate_payload(self._base(transaction_kind="refund"))
        assert normalized.get("transaction_kind") == "refund"

    def test_invalid_kind_causes_validation_error(self):
        _, is_valid, errors = validate_payload(self._base(transaction_kind="expense"))
        assert not is_valid

    def test_build_initial_validation_payload_defaults_kind(self):
        extraction = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "test",
            "total_amount": 10.0,
            "category_id": CATEGORY_ID,
        }
        result = build_initial_validation_payload(extraction, default_account_id=None)
        assert result.get("transaction_kind") == "purchase"

    def test_build_initial_validation_payload_passes_refund_kind(self):
        extraction = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "Return",
            "total_amount": 10.0,
            "category_id": CATEGORY_ID,
            "transaction_kind": "refund",
        }
        result = build_initial_validation_payload(extraction, default_account_id=None)
        assert result.get("transaction_kind") == "refund"


# ---------------------------------------------------------------------------
# 4. ynab.py — _build_sync_transaction_payload + memo prefix
# ---------------------------------------------------------------------------


class TestBuildSyncTransactionPayloadRefund:
    def _call_builder(
        self,
        total_amount: float,
        kind: str = "purchase",
        splits: list[dict] | None = None,
        category_id: str = CATEGORY_ID,
        memo: str = "Grocery run",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": memo,
            "total_amount": total_amount,
            "transaction_kind": kind,
            "splits": splits or [],
        }
        if splits:
            payload["category_id"] = None
        else:
            payload["category_id"] = category_id

        receipt = _make_receipt()
        validation = _make_validation(payload)
        settings = _make_settings()

        from app.services.ynab import get_cached_reference_data

        def _make_entity(entity_id: str) -> Any:
            e = MagicMock()
            e.entity_id = entity_id
            return e

        with patch("app.services.ynab.get_cached_reference_data") as mock_ref:
            mock_ref.return_value = {
                "accounts": [_make_entity(ACCOUNT_ID)],
                "categories": [_make_entity(CATEGORY_ID), _make_entity(CATEGORY_B)],
                "payees": [],
            }
            return _build_sync_transaction_payload(
                MagicMock(), receipt, validation, settings
            )

    def test_purchase_total_is_negative(self):
        result = self._call_builder(12.34, kind="purchase")
        assert result["amount"] == -12340

    def test_refund_total_is_positive(self):
        result = self._call_builder(12.34, kind="refund")
        assert result["amount"] == 12340

    def test_purchase_splits_all_negative(self):
        result = self._call_builder(
            45.74 + 73.45,
            kind="purchase",
            splits=[
                {"amount": 45.74, "category_id": CATEGORY_ID, "memo": ""},
                {"amount": 73.45, "category_id": CATEGORY_B, "memo": ""},
            ],
        )
        subs = result["subtransactions"]
        assert all(s["amount"] < 0 for s in subs)
        assert subs[0]["amount"] == -45740
        assert subs[1]["amount"] == -73450

    def test_refund_splits_all_positive(self):
        result = self._call_builder(
            45.74 + 73.45,
            kind="refund",
            splits=[
                {"amount": 45.74, "category_id": CATEGORY_ID, "memo": ""},
                {"amount": 73.45, "category_id": CATEGORY_B, "memo": ""},
            ],
        )
        subs = result["subtransactions"]
        assert all(s["amount"] > 0 for s in subs)
        assert subs[0]["amount"] == 45740
        assert subs[1]["amount"] == 73450

    def test_refund_splits_sum_equals_total(self):
        total = 45.74 + 73.45
        result = self._call_builder(
            total,
            kind="refund",
            splits=[
                {"amount": 45.74, "category_id": CATEGORY_ID, "memo": ""},
                {"amount": 73.45, "category_id": CATEGORY_B, "memo": ""},
            ],
        )
        subs = result["subtransactions"]
        assert sum(s["amount"] for s in subs) == result["amount"]

    def test_refund_memo_prefix_applied(self):
        result = self._call_builder(10.0, kind="refund", memo="jacket return")
        # Strip the receipt_id marker to check memo content
        memo_without_marker = result["memo"].split(" [receipt_id:")[0]
        assert memo_without_marker.startswith("Return: ")

    def test_refund_memo_prefix_idempotent_return_prefix(self):
        result = self._call_builder(10.0, kind="refund", memo="Return: jacket")
        memo_without_marker = result["memo"].split(" [receipt_id:")[0]
        # Should not double-prefix
        assert not memo_without_marker.startswith("Return: Return: ")
        assert memo_without_marker.startswith("Return: ")

    def test_refund_memo_prefix_idempotent_refund_word(self):
        result = self._call_builder(10.0, kind="refund", memo="refund for broken item")
        memo_without_marker = result["memo"].split(" [receipt_id:")[0]
        assert not memo_without_marker.startswith("Return: ")

    def test_receipt_id_marker_at_end(self):
        result = self._call_builder(10.0, kind="refund", memo="jacket return")
        assert result["memo"].endswith(f"[receipt_id:{RECEIPT_ID}]")

    def test_purchase_memo_no_prefix(self):
        result = self._call_builder(10.0, kind="purchase", memo="groceries")
        assert "Return:" not in result["memo"]


# ---------------------------------------------------------------------------
# 5. ynab.py — _ensure_refund_memo_prefix helper
# ---------------------------------------------------------------------------


class TestEnsureRefundMemoPrefix:
    def test_plain_memo_gets_prefix(self):
        assert _ensure_refund_memo_prefix("jacket") == "Return: jacket"

    def test_none_gets_prefix_trimmed(self):
        # empty/None → "Return: ".strip() → "Return:"
        result = _ensure_refund_memo_prefix(None)
        assert result == "Return:"

    def test_empty_string_gets_prefix_trimmed(self):
        result = _ensure_refund_memo_prefix("")
        assert result == "Return:"

    def test_return_prefix_idempotent(self):
        assert _ensure_refund_memo_prefix("Return: jacket") == "Return: jacket"

    def test_returning_prefix_idempotent(self):
        assert _ensure_refund_memo_prefix("returning jacket") == "returning jacket"

    def test_refund_prefix_idempotent(self):
        assert _ensure_refund_memo_prefix("refund for item") == "refund for item"

    def test_case_insensitive_return(self):
        assert _ensure_refund_memo_prefix("RETURN: item") == "RETURN: item"


# ---------------------------------------------------------------------------
# 6. ynab.py — adopt inflow → sets refund kind
#              adopt mixed-sign → NEEDS_REVIEW, no overwrite
# ---------------------------------------------------------------------------


class TestSyncMatchOrCreateAdoptPath:
    """Tests for the adopt-user-data path in _sync_match_or_create."""

    def _run_adopt_path(
        self,
        ynab_transaction: dict[str, Any],
        initial_kind: str = "purchase",
        initial_category: str = CATEGORY_ID,
        txn_amount: int = -12340,
    ) -> tuple[Any, Any]:
        """Set up and run _sync_match_or_create with allow_update_match=True.

        The transaction_payload amount is set to txn_amount so it matches the
        ynab_transaction amount (required for _match_transaction to return a match).
        """
        from app.services.ynab import _sync_match_or_create

        validation_payload: dict[str, Any] = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "test",
            "total_amount": abs(txn_amount / 1000),
            "transaction_kind": initial_kind,
            "category_id": initial_category,
            "splits": [],
        }
        validation = MagicMock()
        validation.payload = validation_payload

        receipt = MagicMock()
        receipt.id = RECEIPT_ID
        receipt.status = "synced"
        receipt.status_reason = None

        sync_row = MagicMock()
        sync_row.receipt_id = RECEIPT_ID

        client = MagicMock()
        client.list_transactions_since.return_value = [ynab_transaction]
        client.update_transaction.return_value = ynab_transaction

        settings = MagicMock()
        settings.ynab_updated_transaction_flag_color = None

        # transaction_payload amount must match the YNAB transaction amount for matching to succeed
        transaction_payload: dict[str, Any] = {
            "date": "2026-01-01",
            "amount": txn_amount,
            "account_id": ACCOUNT_ID,
            "payee_name": "Store",
            "memo": f"test [receipt_id:{RECEIPT_ID}]",
            "category_id": initial_category,
        }

        _sync_match_or_create(
            client,
            "budget-1",
            settings,
            sync_row,
            transaction_payload,
            validation,
            allow_update_match=True,
            receipt_id=RECEIPT_ID,
            force_create=False,
            receipt=receipt,
        )

        return validation, receipt

    def test_adopt_inflow_sets_refund_kind(self):
        # YNAB has a positive-amount single-category transaction (inflow = refund).
        # Our transaction_payload also uses +12340 to trigger the match.
        ynab_txn: dict[str, Any] = {
            "id": "ynab-txn-1",
            "amount": 12340,  # positive = inflow = refund
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "custom user memo",
            "category_id": CATEGORY_ID,
            "subtransactions": [],
            "deleted": False,
        }
        validation, _ = self._run_adopt_path(
            ynab_txn, initial_kind="refund", txn_amount=12340
        )
        assert validation.payload.get("transaction_kind") == "refund"

    def test_adopt_outflow_sets_purchase_kind(self):
        # YNAB has a negative-amount single-category transaction (outflow = purchase).
        ynab_txn: dict[str, Any] = {
            "id": "ynab-txn-2",
            "amount": -12340,  # negative = outflow = purchase
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "custom user memo",
            "category_id": CATEGORY_ID,
            "subtransactions": [],
            "deleted": False,
        }
        validation, _ = self._run_adopt_path(
            ynab_txn, initial_kind="purchase", txn_amount=-12340
        )
        assert validation.payload.get("transaction_kind") == "purchase"

    def test_adopt_mixed_sign_splits_sets_needs_review(self):
        # YNAB has mixed-sign subtransactions — must flag NEEDS_REVIEW, don't overwrite.
        ynab_txn: dict[str, Any] = {
            "id": "ynab-txn-3",
            "amount": 2000,
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "custom user memo",
            "category_id": None,
            "subtransactions": [
                {"amount": 5000, "category_id": CATEGORY_ID, "memo": "", "deleted": False},
                {"amount": -3000, "category_id": CATEGORY_B, "memo": "", "deleted": False},
            ],
            "deleted": False,
        }

        validation_payload: dict[str, Any] = {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "test",
            "total_amount": 2.0,
            "transaction_kind": "purchase",
            "category_id": CATEGORY_ID,
            "splits": [],
        }
        from app.services.ynab import _sync_match_or_create
        validation = MagicMock()
        validation.payload = validation_payload
        original_payload = dict(validation_payload)

        receipt = MagicMock()
        receipt.id = RECEIPT_ID
        receipt.status = "synced"
        receipt.status_reason = None

        sync_row = MagicMock()
        sync_row.receipt_id = RECEIPT_ID

        client = MagicMock()
        client.list_transactions_since.return_value = [ynab_txn]
        client.update_transaction.return_value = ynab_txn

        settings = MagicMock()
        settings.ynab_updated_transaction_flag_color = None

        transaction_payload: dict[str, Any] = {
            "date": "2026-01-01",
            "amount": 2000,  # match the ynab_txn amount
            "account_id": ACCOUNT_ID,
            "payee_name": "Store",
            "memo": f"test [receipt_id:{RECEIPT_ID}]",
            "category_id": CATEGORY_ID,
        }

        _sync_match_or_create(
            client,
            "budget-1",
            settings,
            sync_row,
            transaction_payload,
            validation,
            allow_update_match=True,
            receipt_id=RECEIPT_ID,
            force_create=False,
            receipt=receipt,
        )

        # Splits must NOT be overwritten — payload unchanged
        assert validation.payload == original_payload
        # Receipt must be flagged for review
        assert receipt.status == "needs_review"
        assert "mixed" in receipt.status_reason.lower()


# ---------------------------------------------------------------------------
# 7. reconciliation.py — inflow → refund kind, mixed → prior payload unchanged
# ---------------------------------------------------------------------------


class TestBuildCorrectedPayloadKind:
    def _prior(self, kind: str = "purchase") -> dict[str, Any]:
        return {
            "payee_name": "Store",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-01-01",
            "memo": "test",
            "total_amount": 10.0,
            "transaction_kind": kind,
            "category_id": CATEGORY_ID,
            "splits": [],
        }

    def test_inflow_single_category_sets_refund_kind(self):
        ynab_txn: dict[str, Any] = {
            "id": "txn-1",
            "amount": 12340,  # positive = inflow = refund
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "memo",
            "category_id": CATEGORY_ID,
            "subtransactions": [],
        }
        result = _build_corrected_payload(self._prior(), ynab_txn)
        assert result.get("transaction_kind") == "refund"

    def test_outflow_single_category_sets_purchase_kind(self):
        ynab_txn: dict[str, Any] = {
            "id": "txn-2",
            "amount": -12340,  # negative = outflow = purchase
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "memo",
            "category_id": CATEGORY_ID,
            "subtransactions": [],
        }
        result = _build_corrected_payload(self._prior(), ynab_txn)
        assert result.get("transaction_kind") == "purchase"

    def test_inflow_splits_sets_refund_kind(self):
        ynab_txn: dict[str, Any] = {
            "id": "txn-3",
            "amount": 12340,  # positive = inflow
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "memo",
            "category_id": None,
            "subtransactions": [
                {"amount": 7340, "category_id": CATEGORY_ID, "memo": "", "deleted": False},
                {"amount": 5000, "category_id": CATEGORY_B, "memo": "", "deleted": False},
            ],
        }
        result = _build_corrected_payload(self._prior(), ynab_txn)
        assert result.get("transaction_kind") == "refund"
        assert all(s["amount"] > 0 for s in result.get("splits", []))

    def test_mixed_sign_splits_returns_prior_unchanged(self):
        prior = self._prior()
        ynab_txn: dict[str, Any] = {
            "id": "txn-4",
            "amount": 2000,
            "date": "2026-01-01",
            "payee_name": "Store",
            "memo": "memo",
            "category_id": None,
            "subtransactions": [
                {"amount": 5000, "category_id": CATEGORY_ID, "memo": "", "deleted": False},
                {"amount": -3000, "category_id": CATEGORY_B, "memo": "", "deleted": False},
            ],
        }
        result = _build_corrected_payload(prior, ynab_txn)
        # Must return the prior payload unchanged
        assert result is prior


# ---------------------------------------------------------------------------
# 8. Duplicate signature excludes transaction_kind
# ---------------------------------------------------------------------------


class TestSignatureExcludesTransactionKind:
    """transaction_kind must NOT be part of the semantic duplicate signature.

    Two receipts with identical payee/date/time/total but different kinds
    (purchase vs refund) must produce the same signature — they are the same
    financial event; kind is a classification attribute, not an identity attribute.
    """

    def test_signature_excludes_transaction_kind(self):
        base_payload: dict[str, Any] = {
            "payee_name": "Costco",
            "account_id": "acct-1",
            "transaction_date": "2026-02-21",
            "transaction_time": "16:49",
            "memo": "Groceries",
            "total_amount": 119.19,
            "category_id": "cat-1",
            "splits": [],
        }
        purchase_payload = {**base_payload, "transaction_kind": "purchase"}
        refund_payload = {**base_payload, "transaction_kind": "refund"}

        sig_purchase = build_semantic_signature(purchase_payload)
        sig_refund = build_semantic_signature(refund_payload)

        assert sig_purchase is not None
        assert sig_purchase == sig_refund, (
            "transaction_kind must not influence the duplicate signature; "
            "purchase and refund for the same event should be treated as the same receipt"
        )
