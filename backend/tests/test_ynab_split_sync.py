"""Regression tests for YNAB split transaction sync.

Covers the scenarios where the YNAB API silently ignores subtransaction
updates on existing split transactions, requiring a delete + recreate
workaround.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from datetime import date

from app.services.ynab import (
    _build_subtransactions,
    _build_update_transaction_payload,
    _full_transaction_payload_from_ynab_transaction,
    _match_transaction,
    _normalized_subtransaction_signature,
    _strip_receipt_id_marker,
    _transaction_structure_matches_payload,
    _update_or_replace_transaction,
    _ynab_has_user_data,
)
from receipt_shared.ynab_client import YNABClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUDGET_ID = "budget-1"
TXN_ID = "txn-existing-1"
NEW_TXN_ID = "txn-new-1"
CATEGORY_A = "cat-groceries"
CATEGORY_B = "cat-household"
CATEGORY_C = "cat-clothing"
ACCOUNT = "acct-checking"


def _make_single_payload(
    category_id: str = CATEGORY_A,
    amount: int = -50000,
    memo: str = "groceries",
) -> dict[str, Any]:
    return {
        "account_id": ACCOUNT,
        "date": "2025-06-01",
        "amount": amount,
        "payee_name": "Store",
        "memo": memo,
        "category_id": category_id,
    }


def _make_split_payload(
    splits: list[dict[str, Any]],
    amount: int = -50000,
    memo: str = "",
) -> dict[str, Any]:
    return {
        "account_id": ACCOUNT,
        "date": "2025-06-01",
        "amount": amount,
        "payee_name": "Store",
        "memo": memo,
        "subtransactions": splits,
    }


def _make_ynab_response(
    payload: dict[str, Any],
    transaction_id: str = TXN_ID,
    sub_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Simulate a YNAB API response that reflects the payload (success case)."""
    resp = dict(payload)
    resp["id"] = transaction_id
    resp["deleted"] = False
    if "subtransactions" in resp and sub_ids:
        for i, sub in enumerate(resp["subtransactions"]):
            if i < len(sub_ids):
                sub["id"] = sub_ids[i]
    return resp


def _make_ynab_response_ignoring_splits(
    original_transaction: dict[str, Any],
) -> dict[str, Any]:
    """Simulate YNAB returning the *original* transaction unchanged after a PUT
    that tried to update splits (API ignores the split changes)."""
    return dict(original_transaction)


def _mock_client(
    update_response: dict[str, Any] | None = None,
    create_response: dict[str, Any] | None = None,
) -> MagicMock:
    client = MagicMock(spec=YNABClient)
    if update_response is not None:
        client.update_transaction.return_value = update_response
    if create_response is not None:
        client.create_transaction.return_value = create_response
    client.delete_transaction.return_value = {}
    return client


# ---------------------------------------------------------------------------
# Tests: _build_update_transaction_payload
# ---------------------------------------------------------------------------


class TestBuildUpdateTransactionPayload:
    def test_single_to_split_no_existing_subs(self):
        """Single-category transaction being converted to split."""
        desired = _make_split_payload(
            splits=[
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        )
        existing = {"id": TXN_ID, "subtransactions": [], "category_id": CATEGORY_A}
        result = _build_update_transaction_payload(desired, existing)
        assert len(result["subtransactions"]) == 2
        # No IDs should be merged since existing has none
        assert "id" not in result["subtransactions"][0]
        assert "id" not in result["subtransactions"][1]

    def test_split_edit_preserves_existing_ids(self):
        """Updating splits on a transaction that already has splits.
        Existing subtransaction IDs should be merged by position."""
        desired = _make_split_payload(
            splits=[
                {"amount": -25000, "category_id": CATEGORY_A, "memo": "updated food"},
                {"amount": -25000, "category_id": CATEGORY_B, "memo": "updated cleaning"},
            ]
        )
        existing = {
            "id": TXN_ID,
            "subtransactions": [
                {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ],
        }
        result = _build_update_transaction_payload(desired, existing)
        assert result["subtransactions"][0]["id"] == "sub-1"
        assert result["subtransactions"][1]["id"] == "sub-2"
        assert result["subtransactions"][0]["amount"] == -25000
        assert result["subtransactions"][1]["amount"] == -25000

    def test_add_split_to_existing(self):
        """Adding a third split to a 2-split transaction."""
        desired = _make_split_payload(
            splits=[
                {"amount": -20000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -15000, "category_id": CATEGORY_B, "memo": "cleaning"},
                {"amount": -15000, "category_id": CATEGORY_C, "memo": "clothes"},
            ]
        )
        existing = {
            "id": TXN_ID,
            "subtransactions": [
                {"id": "sub-1", "amount": -20000, "category_id": CATEGORY_A, "memo": "food"},
                {"id": "sub-2", "amount": -30000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ],
        }
        result = _build_update_transaction_payload(desired, existing)
        assert len(result["subtransactions"]) == 3
        assert result["subtransactions"][0]["id"] == "sub-1"
        assert result["subtransactions"][1]["id"] == "sub-2"
        assert "id" not in result["subtransactions"][2]

    def test_remove_split_marks_deleted(self):
        """Removing a split from a 3-split transaction marks excess as deleted."""
        desired = _make_split_payload(
            splits=[
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        )
        existing = {
            "id": TXN_ID,
            "subtransactions": [
                {"id": "sub-1", "amount": -20000, "category_id": CATEGORY_A, "memo": "food"},
                {"id": "sub-2", "amount": -15000, "category_id": CATEGORY_B, "memo": "cleaning"},
                {"id": "sub-3", "amount": -15000, "category_id": CATEGORY_C, "memo": "clothes"},
            ],
        }
        result = _build_update_transaction_payload(desired, existing)
        assert len(result["subtransactions"]) == 3
        assert result["subtransactions"][2] == {"id": "sub-3", "deleted": True}

    def test_split_to_single_clears_subtransactions(self):
        """Converting from split back to single-category sends empty subtransactions."""
        desired = _make_single_payload()
        existing = {
            "id": TXN_ID,
            "subtransactions": [
                {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ],
        }
        result = _build_update_transaction_payload(desired, existing)
        assert result["subtransactions"] == []
        assert result["category_id"] == CATEGORY_A


# ---------------------------------------------------------------------------
# Tests: _transaction_structure_matches_payload
# ---------------------------------------------------------------------------


class TestTransactionStructureMatchesPayload:
    def test_matching_splits(self):
        txn = {
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        }
        payload = {
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        }
        assert _transaction_structure_matches_payload(txn, payload) is True

    def test_mismatched_splits(self):
        txn = {
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        }
        payload = {
            "subtransactions": [
                {"amount": -25000, "category_id": CATEGORY_A, "memo": "updated"},
                {"amount": -25000, "category_id": CATEGORY_B, "memo": "updated"},
            ]
        }
        assert _transaction_structure_matches_payload(txn, payload) is False

    def test_single_category_match(self):
        txn = {"category_id": CATEGORY_A, "subtransactions": []}
        payload = {"category_id": CATEGORY_A}
        assert _transaction_structure_matches_payload(txn, payload) is True

    def test_single_category_with_active_splits_mismatch(self):
        """Trying to go to single-category but YNAB still has active splits."""
        txn = {
            "category_id": CATEGORY_A,
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            ],
        }
        payload = {"category_id": CATEGORY_A}
        assert _transaction_structure_matches_payload(txn, payload) is False


# ---------------------------------------------------------------------------
# Tests: _update_or_replace_transaction  (the core fix)
# ---------------------------------------------------------------------------


class TestUpdateOrReplaceTransaction:
    def test_simple_update_succeeds(self):
        """PUT update works when YNAB applies changes (e.g., memo update on non-split)."""
        payload = _make_single_payload(memo="updated memo")
        response = _make_ynab_response(payload)
        client = _mock_client(update_response=response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, {"id": TXN_ID, "subtransactions": []},
        )

        client.update_transaction.assert_called_once()
        client.delete_transaction.assert_not_called()
        client.create_transaction.assert_not_called()
        assert status == "matched_updated"
        assert final_id == TXN_ID

    def test_single_to_split_first_time_via_update(self):
        """Converting single to split works via PUT (YNAB supports this)."""
        splits = [
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        payload = _make_split_payload(splits=splits)
        response = _make_ynab_response(payload, sub_ids=["sub-1", "sub-2"])
        client = _mock_client(update_response=response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, {"id": TXN_ID, "subtransactions": [], "category_id": CATEGORY_A},
        )

        client.update_transaction.assert_called_once()
        client.delete_transaction.assert_not_called()
        assert status == "matched_updated"

    def test_split_edit_triggers_delete_recreate(self):
        """Editing splits on existing split triggers delete+create (YNAB ignores PUT)."""
        old_splits = [
            {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        existing = {"id": TXN_ID, "subtransactions": old_splits, "deleted": False}

        new_splits = [
            {"amount": -25000, "category_id": CATEGORY_A, "memo": "updated food"},
            {"amount": -25000, "category_id": CATEGORY_B, "memo": "updated cleaning"},
        ]
        payload = _make_split_payload(splits=new_splits)

        # YNAB ignores the split update — returns original transaction unchanged
        stale_response = _make_ynab_response_ignoring_splits(existing)
        new_response = _make_ynab_response(payload, transaction_id=NEW_TXN_ID, sub_ids=["sub-new-1", "sub-new-2"])
        client = _mock_client(update_response=stale_response, create_response=new_response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, existing,
        )

        client.update_transaction.assert_called_once()
        client.delete_transaction.assert_called_once_with(BUDGET_ID, TXN_ID)
        client.create_transaction.assert_called_once_with(BUDGET_ID, payload)
        assert status == "created"
        assert final_id == NEW_TXN_ID

    def test_add_split_triggers_delete_recreate(self):
        """Adding a third split to a 2-split transaction triggers delete+create."""
        old_splits = [
            {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        existing = {"id": TXN_ID, "subtransactions": old_splits, "deleted": False}

        new_splits = [
            {"amount": -20000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -15000, "category_id": CATEGORY_B, "memo": "cleaning"},
            {"amount": -15000, "category_id": CATEGORY_C, "memo": "clothes"},
        ]
        payload = _make_split_payload(splits=new_splits)

        stale_response = _make_ynab_response_ignoring_splits(existing)
        new_response = _make_ynab_response(payload, transaction_id=NEW_TXN_ID)
        client = _mock_client(update_response=stale_response, create_response=new_response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, existing,
        )

        client.delete_transaction.assert_called_once_with(BUDGET_ID, TXN_ID)
        client.create_transaction.assert_called_once()
        assert status == "created"
        assert final_id == NEW_TXN_ID

    def test_remove_split_triggers_delete_recreate(self):
        """Removing one split from a 3-split transaction triggers delete+create."""
        old_splits = [
            {"id": "sub-1", "amount": -20000, "category_id": CATEGORY_A, "memo": "food"},
            {"id": "sub-2", "amount": -15000, "category_id": CATEGORY_B, "memo": "cleaning"},
            {"id": "sub-3", "amount": -15000, "category_id": CATEGORY_C, "memo": "clothes"},
        ]
        existing = {"id": TXN_ID, "subtransactions": old_splits, "deleted": False}

        new_splits = [
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        payload = _make_split_payload(splits=new_splits)

        stale_response = _make_ynab_response_ignoring_splits(existing)
        new_response = _make_ynab_response(payload, transaction_id=NEW_TXN_ID)
        client = _mock_client(update_response=stale_response, create_response=new_response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, existing,
        )

        client.delete_transaction.assert_called_once_with(BUDGET_ID, TXN_ID)
        client.create_transaction.assert_called_once()
        assert status == "created"

    def test_split_to_single_triggers_delete_recreate(self):
        """Converting split back to single-category triggers delete+create."""
        old_splits = [
            {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        existing = {"id": TXN_ID, "subtransactions": old_splits, "deleted": False}

        payload = _make_single_payload()

        # YNAB ignores the attempt to unsplit — returns original with splits still active
        stale_response = _make_ynab_response_ignoring_splits(existing)
        new_response = _make_ynab_response(payload, transaction_id=NEW_TXN_ID)
        client = _mock_client(update_response=stale_response, create_response=new_response)

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, existing,
        )

        client.delete_transaction.assert_called_once_with(BUDGET_ID, TXN_ID)
        client.create_transaction.assert_called_once()
        assert status == "created"
        assert final_id == NEW_TXN_ID

    def test_memo_update_on_split_no_structure_change(self):
        """Updating only top-level memo on a split transaction should succeed via PUT
        (structure hasn't changed, so no delete+create needed)."""
        splits = [
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        payload = _make_split_payload(splits=splits, memo="updated top memo")
        # YNAB applies top-level changes and returns the same structure
        response = _make_ynab_response(payload, sub_ids=["sub-1", "sub-2"])
        client = _mock_client(update_response=response)

        existing = {
            "id": TXN_ID,
            "subtransactions": [
                {"id": "sub-1", "amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
                {"id": "sub-2", "amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            ],
        }

        ynab_resp, final_id, status = _update_or_replace_transaction(
            client, BUDGET_ID, TXN_ID, payload, existing,
        )

        client.update_transaction.assert_called_once()
        client.delete_transaction.assert_not_called()
        assert status == "matched_updated"


# ---------------------------------------------------------------------------
# Tests: _build_subtransactions (from validation payload)
# ---------------------------------------------------------------------------


class TestBuildSubtransactions:
    def test_converts_dollars_to_milliunits(self):
        payload = {
            "splits": [
                {"amount": 30.0, "category_id": CATEGORY_A, "memo": "food"},
                {"amount": 20.0, "category_id": CATEGORY_B, "memo": "cleaning"},
            ]
        }
        result = _build_subtransactions(payload)
        # dollars_to_milliunits with outflow=True negates positive amounts
        assert result[0]["amount"] == -30000
        assert result[1]["amount"] == -20000
        assert result[0]["category_id"] == CATEGORY_A
        assert result[1]["memo"] == "cleaning"


# ---------------------------------------------------------------------------
# Tests: _normalized_subtransaction_signature
# ---------------------------------------------------------------------------


class TestNormalizedSubtransactionSignature:
    def test_order_independent(self):
        subs_a = [
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
        ]
        subs_b = [
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning"},
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
        ]
        assert _normalized_subtransaction_signature(subs_a) == _normalized_subtransaction_signature(subs_b)

    def test_excludes_deleted(self):
        subs = [
            {"amount": -30000, "category_id": CATEGORY_A, "memo": "food"},
            {"amount": -20000, "category_id": CATEGORY_B, "memo": "cleaning", "deleted": True},
        ]
        sig = _normalized_subtransaction_signature(subs)
        assert len(sig) == 1


# ---------------------------------------------------------------------------
# Tests: _strip_receipt_id_marker
# ---------------------------------------------------------------------------


class TestStripReceiptIdMarker:
    def test_strips_marker_at_end(self):
        memo = "coffee shop [receipt_id:abc123]"
        assert _strip_receipt_id_marker(memo) == "coffee shop"

    def test_strips_marker_only(self):
        assert _strip_receipt_id_marker("[receipt_id:abc123]") == ""

    def test_no_marker_unchanged(self):
        assert _strip_receipt_id_marker("just a memo") == "just a memo"

    def test_none_returns_empty(self):
        assert _strip_receipt_id_marker(None) == ""

    def test_empty_returns_empty(self):
        assert _strip_receipt_id_marker("") == ""

    def test_strips_marker_with_leading_space(self):
        memo = "my memo [receipt_id:xyz-999]"
        assert _strip_receipt_id_marker(memo) == "my memo"


# ---------------------------------------------------------------------------
# Tests: _ynab_has_user_data
# ---------------------------------------------------------------------------


class TestYnabHasUserData:
    def test_empty_transaction_no_user_data(self):
        txn: dict = {"memo": None, "subtransactions": []}
        assert _ynab_has_user_data(txn) is False

    def test_memo_only_marker_no_user_data(self):
        txn = {"memo": "[receipt_id:abc123]", "subtransactions": []}
        assert _ynab_has_user_data(txn) is False

    def test_real_memo_has_user_data(self):
        txn = {"memo": "anniversary dinner", "subtransactions": []}
        assert _ynab_has_user_data(txn) is True

    def test_memo_plus_marker_has_user_data(self):
        txn = {"memo": "groceries [receipt_id:abc123]", "subtransactions": []}
        assert _ynab_has_user_data(txn) is True

    def test_active_subtransactions_has_user_data(self):
        txn = {
            "memo": None,
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "deleted": False},
            ],
        }
        assert _ynab_has_user_data(txn) is True

    def test_only_deleted_subtransactions_no_user_data(self):
        txn = {
            "memo": "",
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "deleted": True},
            ],
        }
        assert _ynab_has_user_data(txn) is False

    def test_category_only_edit_detected_as_user_data(self):
        txn = {
            "memo": "",
            "category_id": CATEGORY_B,
            "subtransactions": [],
        }
        desired_payload = {
            "category_id": CATEGORY_A,
            "subtransactions": [],
        }
        assert _ynab_has_user_data(txn, desired_payload=desired_payload) is True

    def test_matching_category_not_user_data_when_no_other_signals(self):
        txn = {
            "memo": "",
            "category_id": CATEGORY_A,
            "subtransactions": [],
        }
        desired_payload = {
            "category_id": CATEGORY_A,
            "subtransactions": [],
        }
        assert _ynab_has_user_data(txn, desired_payload=desired_payload) is False


class TestFullTransactionPayloadFromYnabTransaction:
    def test_single_category_payload_shape(self):
        txn = {
            "account_id": ACCOUNT,
            "date": "2025-06-01",
            "amount": -50000,
            "payee_name": "Store",
            "memo": "ynab memo",
            "category_id": CATEGORY_A,
            "subtransactions": [],
        }
        payload = _full_transaction_payload_from_ynab_transaction(
            txn,
            memo_override="ynab memo [receipt_id:abc123]",
            include_flags=True,
            flag_color="blue",
        )
        assert payload["account_id"] == ACCOUNT
        assert payload["category_id"] == CATEGORY_A
        assert "subtransactions" not in payload
        assert payload["approved"] is False
        assert payload["flag_color"] == "blue"
        assert payload["memo"] == "ynab memo [receipt_id:abc123]"

    def test_split_payload_shape_omits_top_level_category(self):
        txn = {
            "account_id": ACCOUNT,
            "date": "2025-06-01",
            "amount": -50000,
            "payee_name": "Store",
            "memo": "",
            "category_id": CATEGORY_A,
            "subtransactions": [
                {"amount": -30000, "category_id": CATEGORY_A, "memo": "food", "deleted": False},
                {"amount": -20000, "category_id": CATEGORY_B, "memo": "home", "deleted": False},
            ],
        }
        payload = _full_transaction_payload_from_ynab_transaction(txn)
        assert "category_id" not in payload
        assert len(payload["subtransactions"]) == 2
        assert payload["subtransactions"][0]["amount"] == -30000
        assert payload["subtransactions"][1]["category_id"] == CATEGORY_B


# ---------------------------------------------------------------------------
# Tests: _match_transaction (payee matching added)
# ---------------------------------------------------------------------------


class TestMatchTransaction:
    RECEIPT_DATE = date(2025, 6, 1)
    END_DATE = date(2025, 6, 4)
    AMOUNT = -50000

    def _base_txn(self, payee: str = "Store", date_str: str = "2025-06-01") -> dict:
        return {
            "id": "txn-1",
            "amount": self.AMOUNT,
            "date": date_str,
            "payee_name": payee,
            "deleted": False,
        }

    def test_matches_amount_date_payee(self):
        txns = [self._base_txn()]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is not None
        assert result["id"] == "txn-1"

    def test_payee_mismatch_no_match(self):
        txns = [self._base_txn(payee="Other Store")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is None

    def test_payee_case_insensitive(self):
        txns = [self._base_txn(payee="STORE")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="store")
        assert result is not None

    def test_no_payee_filter_matches_any(self):
        """When payee_name is empty, any payee matches (backward compat)."""
        txns = [self._base_txn(payee="Anywhere")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="")
        assert result is not None

    def test_date_before_window_no_match(self):
        txns = [self._base_txn(date_str="2025-05-31")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is None

    def test_date_after_window_no_match(self):
        txns = [self._base_txn(date_str="2025-06-05")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is None

    def test_date_at_boundary_matches(self):
        txns = [self._base_txn(date_str="2025-06-04")]
        result = _match_transaction(txns, self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is not None

    def test_skips_deleted(self):
        txn = self._base_txn()
        txn["deleted"] = True
        result = _match_transaction([txn], self.AMOUNT, self.RECEIPT_DATE, self.END_DATE, payee_name="Store")
        assert result is None

    def test_amount_mismatch_no_match(self):
        result = _match_transaction(
            [self._base_txn()], -99999, self.RECEIPT_DATE, self.END_DATE, payee_name="Store"
        )
        assert result is None
