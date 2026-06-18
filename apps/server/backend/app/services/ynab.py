from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import re

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.utils import utcnow
from app.models import GameReceiptStateModel, Receipt, ReceiptCorrection, ReceiptTwin, TimingMetric, Validation, YNABCache, YNABSync
from app.services.card_mapping import upsert_card_mapping
from app.services.duplicates import apply_semantic_duplicate_state
from app.services.game import (
    _evaluate_passes,
    _get_or_create_tokens,
    _load_week_fires_by_start,
    apply_sync_gamification,
)
from app.services.incidents import record_incident
from app.services.validation import UNKNOWN_ACCOUNT_ID
from receipt_shared.money import dollars_to_milliunits, milliunits_to_dollars
from receipt_shared.ynab_client import YNABClient, YNABConflictError

logger = logging.getLogger(__name__)
RECEIPT_ID_MARKER_PREFIX = "[receipt_id:"
IMPORT_ID_PREFIX = "RA:1:"


def _build_import_id(receipt_id: str) -> str:
    """Build a deterministic YNAB import_id from a receipt UUID.

    Format: RA:1:<receipt_id_no_dashes_truncated>
    Result is always ≤36 characters.
    """
    return f"{IMPORT_ID_PREFIX}{receipt_id.replace('-', '')[:31]}"


class YNABSyncDisabledError(RuntimeError):
    """Raised when a YNAB write is attempted while sync is disabled."""


def make_idempotency_key(
    receipt_id: str,
    validation_id: int,
    force_create: bool,
    allow_update_match: bool,
) -> str:
    raw = f"{receipt_id}:{validation_id}:{force_create}:{allow_update_match}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_ynab_client(settings: Settings) -> YNABClient:
    if not settings.ynab_access_token:
        raise ValueError("YNAB_ACCESS_TOKEN is not configured")
    return YNABClient(settings.ynab_access_token)


def refresh_ynab_cache(db: Session, settings: Settings) -> dict[str, int]:
    if not settings.ynab_budget_id:
        raise ValueError("YNAB_BUDGET_ID is not configured")

    client = get_ynab_client(settings)
    budget_id = settings.ynab_budget_id

    categories = client.list_categories(budget_id)
    accounts = client.list_accounts(budget_id)
    payees = client.list_payees(budget_id)

    now = utcnow()
    category_ids: set[str] = set()
    account_ids: set[str] = set()
    payee_ids: set[str] = set()

    for category in categories:
        category_ids.add(category.id)
        _upsert_cache_entity(
            db,
            budget_id,
            YNABCacheEntityType.CATEGORY.value,
            category.id,
            category.name,
            category.group_name,
            {
                "id": category.id,
                "name": category.name,
                "group_name": category.group_name,
            },
            now,
        )

    for account in accounts:
        account_ids.add(account["id"])
        _upsert_cache_entity(
            db,
            budget_id,
            YNABCacheEntityType.ACCOUNT.value,
            account["id"],
            account.get("name", ""),
            None,
            account,
            now,
        )

    for payee in payees:
        payee_ids.add(payee["id"])
        _upsert_cache_entity(
            db,
            budget_id,
            YNABCacheEntityType.PAYEE.value,
            payee["id"],
            payee.get("name", ""),
            None,
            payee,
            now,
        )

    _prune_cache_entities(db, budget_id, YNABCacheEntityType.CATEGORY.value, category_ids)
    _prune_cache_entities(db, budget_id, YNABCacheEntityType.ACCOUNT.value, account_ids)
    _prune_cache_entities(db, budget_id, YNABCacheEntityType.PAYEE.value, payee_ids)

    db.commit()
    return {
        "category_count": len(categories),
        "account_count": len(accounts),
        "payee_count": len(payees),
    }


def _upsert_cache_entity(
    db: Session,
    budget_id: str,
    entity_type: str,
    entity_id: str,
    name: str,
    group_name: str | None,
    raw_json: dict[str, Any],
    fetched_at: datetime,
) -> None:
    existing = db.scalar(
        select(YNABCache).where(
            and_(
                YNABCache.budget_id == budget_id,
                YNABCache.entity_type == entity_type,
                YNABCache.entity_id == entity_id,
            )
        )
    )
    if existing:
        existing.name = name
        existing.group_name = group_name
        existing.raw_json = raw_json
        existing.fetched_at = fetched_at
        return

    db.add(
        YNABCache(
            budget_id=budget_id,
            entity_type=entity_type,
            entity_id=entity_id,
            name=name,
            group_name=group_name,
            raw_json=raw_json,
            fetched_at=fetched_at,
        )
    )


def _prune_cache_entities(db: Session, budget_id: str, entity_type: str, valid_ids: set[str]) -> None:
    existing_rows = list(
        db.scalars(
            select(YNABCache).where(
                and_(
                    YNABCache.budget_id == budget_id,
                    YNABCache.entity_type == entity_type,
                )
            )
        )
    )
    for row in existing_rows:
        if row.entity_id not in valid_ids:
            db.delete(row)


def list_cached_entities(
    db: Session,
    entity_type: str | None = None,
    budget_id: str | None = None,
) -> list[YNABCache]:
    stmt = select(YNABCache).order_by(YNABCache.entity_type, YNABCache.name)
    if entity_type:
        stmt = stmt.where(YNABCache.entity_type == entity_type)
    if budget_id:
        stmt = stmt.where(YNABCache.budget_id == budget_id)
    return list(db.scalars(stmt))


def get_cached_reference_data(db: Session, settings: Settings) -> dict[str, list[YNABCache]]:
    budget_id = settings.ynab_budget_id
    if not budget_id:
        return {"categories": [], "accounts": [], "payees": []}

    categories = list_cached_entities(db, entity_type=YNABCacheEntityType.CATEGORY.value, budget_id=budget_id)
    accounts = list_cached_entities(db, entity_type=YNABCacheEntityType.ACCOUNT.value, budget_id=budget_id)
    payees = list_cached_entities(db, entity_type=YNABCacheEntityType.PAYEE.value, budget_id=budget_id)
    return {
        "categories": categories,
        "accounts": accounts,
        "payees": payees,
    }


def get_latest_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(Validation.receipt_id == receipt_id)
        .order_by(Validation.version.desc())
        .limit(1)
    )


def _receipt_id_marker(receipt_id: str) -> str:
    return f"{RECEIPT_ID_MARKER_PREFIX}{receipt_id}]"


def _append_receipt_id_marker(memo: str | None, receipt_id: str) -> str:
    memo_text = str(memo or "").strip()
    marker = _receipt_id_marker(receipt_id)
    if marker in memo_text:
        return memo_text
    if memo_text:
        return f"{memo_text} {marker}"
    return marker


def _strip_receipt_id_marker(memo: str | None) -> str:
    """Remove any [receipt_id:...] marker from a memo string."""
    memo_text = str(memo or "").strip()
    return re.sub(r"\s*\[receipt_id:[^\]]*\]", "", memo_text).strip()


REFUND_MEMO_PREFIX = "Return: "


def _ensure_refund_memo_prefix(memo: str | None) -> str:
    memo_text = str(memo or "").strip()
    if memo_text.lower().startswith(("return:", "returning", "refund")):
        return memo_text
    return f"{REFUND_MEMO_PREFIX}{memo_text}".strip()


def _ynab_has_user_data(
    transaction: dict[str, Any],
    desired_payload: dict[str, Any] | None = None,
) -> bool:
    """Return True if the YNAB transaction has user-entered data.

    Heuristics:
    - non-empty memo (ignoring receipt_id marker)
    - active subtransactions
    - category mismatch vs desired payload for single-category transactions
    """
    memo_without_marker = _strip_receipt_id_marker(transaction.get("memo"))
    if memo_without_marker:
        return True
    active_subtransactions = [sub for sub in transaction.get("subtransactions", []) if not sub.get("deleted")]
    if len(active_subtransactions) > 0:
        return True

    if desired_payload is not None:
        desired_subtransactions = desired_payload.get("subtransactions", [])
        if not desired_subtransactions:
            desired_category_id = str(desired_payload.get("category_id") or "")
            ynab_category_id = str(transaction.get("category_id") or "")
            if desired_category_id != ynab_category_id:
                return True

    return False


def _full_transaction_payload_from_ynab_transaction(
    transaction: dict[str, Any],
    *,
    memo_override: str | None = None,
    include_flags: bool = False,
    flag_color: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": transaction.get("account_id"),
        "date": transaction.get("date"),
        "amount": int(transaction.get("amount", 0)),
        "payee_name": transaction.get("payee_name") or "",
        "memo": str(transaction.get("memo") if memo_override is None else memo_override) or "",
    }

    active_subtransactions = [sub for sub in transaction.get("subtransactions", []) if not sub.get("deleted")]
    if active_subtransactions:
        payload["subtransactions"] = [
            {
                "amount": int(sub.get("amount", 0)),
                "category_id": sub.get("category_id"),
                "memo": str(sub.get("memo") or ""),
            }
            for sub in active_subtransactions
        ]
    else:
        payload["category_id"] = transaction.get("category_id")

    if include_flags:
        payload["approved"] = False
        if flag_color:
            payload["flag_color"] = flag_color

    return payload


def _build_subtransactions(validation_payload: dict[str, Any], *, outflow: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "amount": dollars_to_milliunits(split["amount"], outflow=outflow),
            "category_id": split["category_id"],
            "memo": split.get("memo", ""),
        }
        for split in validation_payload.get("splits", [])
    ]


def _match_transaction(
    transactions: list[dict[str, Any]],
    amount_milliunits: int,
    receipt_date: date,
    end_date: date,
    payee_name: str = "",
) -> dict[str, Any] | None:
    for transaction in transactions:
        if transaction.get("deleted"):
            continue
        if int(transaction.get("amount", 0)) != amount_milliunits:
            continue
        txn_date = date.fromisoformat(transaction["date"])
        if not (receipt_date <= txn_date <= end_date):
            continue
        if payee_name and str(transaction.get("payee_name") or "").lower() != payee_name.lower():
            continue
        return transaction
    return None


def _latest_successful_sync_for_receipt(db: Session, receipt_id: str) -> YNABSync | None:
    return db.scalar(
        select(YNABSync)
        .where(
            YNABSync.receipt_id == receipt_id,
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
        )
        .order_by(YNABSync.completed_at.desc(), YNABSync.id.desc())
        .limit(1)
    )


def _normalized_subtransaction_signature(subtransactions: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    signature: list[tuple[int, str, str]] = []
    for sub in subtransactions:
        if sub.get("deleted"):
            continue
        signature.append(
            (
                int(sub.get("amount", 0)),
                str(sub.get("category_id") or ""),
                str(sub.get("memo") or ""),
            )
        )
    return sorted(signature)


def _transaction_matches_payload(transaction: dict[str, Any], payload: dict[str, Any]) -> bool:
    if transaction.get("deleted"):
        return False
    if str(transaction.get("date", "")) != str(payload.get("date", "")):
        return False
    if int(transaction.get("amount", 0)) != int(payload.get("amount", 0)):
        return False
    if str(transaction.get("account_id", "")) != str(payload.get("account_id", "")):
        return False
    if str(transaction.get("payee_name") or "") != str(payload.get("payee_name") or ""):
        return False
    if str(transaction.get("memo") or "") != str(payload.get("memo") or ""):
        return False

    payload_subtransactions = payload.get("subtransactions", [])
    transaction_subtransactions = transaction.get("subtransactions", [])
    if payload_subtransactions:
        return _normalized_subtransaction_signature(transaction_subtransactions) == _normalized_subtransaction_signature(
            payload_subtransactions
        )

    if any(not sub.get("deleted") for sub in transaction_subtransactions):
        return False
    return str(transaction.get("category_id") or "") == str(payload.get("category_id") or "")


def _find_exact_transaction_match(
    client: YNABClient,
    budget_id: str,
    transaction_payload: dict[str, Any],
    since_date: str,
) -> dict[str, Any] | None:
    candidates = client.list_transactions_since(budget_id, since_date)
    matches = [candidate for candidate in candidates if _transaction_matches_payload(candidate, transaction_payload)]
    if len(matches) == 1:
        return matches[0]
    return None


def _get_transaction_by_id(client: YNABClient, budget_id: str, transaction_id: str) -> dict[str, Any] | None:
    try:
        transaction = client.get_transaction(budget_id, transaction_id)
    except RuntimeError as exc:
        if "YNAB API error 404" in str(exc):
            return None
        raise
    if transaction.get("deleted"):
        return None
    return transaction


def _build_update_transaction_payload(
    transaction_payload: dict[str, Any],
    existing_transaction: dict[str, Any] | None,
) -> dict[str, Any]:
    # YNAB's PUT /transactions/{id} schema does not include import_id — strip it.
    update_payload: dict[str, Any] = {k: v for k, v in transaction_payload.items() if k != "import_id"}
    desired_subtransactions = transaction_payload.get("subtransactions")
    existing_subtransactions = [
        sub
        for sub in (existing_transaction or {}).get("subtransactions", [])
        if not sub.get("deleted")
    ]

    if desired_subtransactions is None:
        if existing_subtransactions:
            # Ask YNAB to clear split rows when transitioning back to single-category mode.
            update_payload["subtransactions"] = []
        return update_payload

    merged_subtransactions: list[dict[str, Any]] = []
    for index, desired_subtransaction in enumerate(desired_subtransactions):
        next_subtransaction = dict(desired_subtransaction)
        if index < len(existing_subtransactions):
            existing_subtransaction_id = existing_subtransactions[index].get("id")
            if existing_subtransaction_id:
                next_subtransaction["id"] = existing_subtransaction_id
        merged_subtransactions.append(next_subtransaction)

    for existing_subtransaction in existing_subtransactions[len(desired_subtransactions) :]:
        existing_subtransaction_id = existing_subtransaction.get("id")
        if existing_subtransaction_id:
            merged_subtransactions.append({"id": existing_subtransaction_id, "deleted": True})

    update_payload["subtransactions"] = merged_subtransactions
    return update_payload


def _transaction_structure_matches_payload(transaction: dict[str, Any], payload: dict[str, Any]) -> bool:
    payload_subtransactions = payload.get("subtransactions", [])
    transaction_subtransactions = transaction.get("subtransactions", [])
    if payload_subtransactions:
        return _normalized_subtransaction_signature(transaction_subtransactions) == _normalized_subtransaction_signature(
            payload_subtransactions
        )

    if any(not sub.get("deleted") for sub in transaction_subtransactions):
        return False
    return str(transaction.get("category_id") or "") == str(payload.get("category_id") or "")


def _update_or_replace_transaction(
    client: YNABClient,
    budget_id: str,
    target_transaction_id: str,
    transaction_payload: dict[str, Any],
    existing_transaction: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, str, bool]:
    """Try PUT update.  Delete+recreate is PROHIBITED (TASK 5a).

    If YNAB ignores split/category structure changes, we flag it for manual
    review instead of destroying and recreating the transaction.  Bank-imported
    transactions are locked in YNAB and cannot have their structure changed via
    the API; the user must fix the split structure manually in YNAB.

    Returns (ynab_response, target_transaction_id, sync_status, structure_applied).
    structure_applied=True  → YNAB applied the structure change (normal case).
    structure_applied=False → YNAB ignored the change; callers must set NEEDS_REVIEW.
    """
    update_payload = _build_update_transaction_payload(transaction_payload, existing_transaction)
    ynab_response = client.update_transaction(budget_id, target_transaction_id, update_payload)

    if _transaction_structure_matches_payload(ynab_response, transaction_payload):
        return ynab_response, target_transaction_id, YNABSyncStatus.MATCHED_UPDATED.value, True

    # YNAB silently ignored split/category structure changes (likely a bank-imported
    # transaction).  We do NOT delete-and-recreate — that window risks data loss.
    # Return structure_applied=False; callers will mark the receipt NEEDS_REVIEW.
    logger.warning(
        "YNAB ignored split/category structure change on transaction %s; "
        "flagging receipt for manual review (delete+recreate prohibited)",
        target_transaction_id,
    )
    return ynab_response, target_transaction_id, YNABSyncStatus.MATCHED_UPDATED.value, False


def _build_sync_transaction_payload(
    db: Session,
    receipt: Receipt,
    validation: Validation,
    settings: Settings,
) -> dict[str, Any]:
    """Validate sync prerequisites and build the YNAB transaction payload dict."""
    payload = validation.payload
    account_id = payload.get("account_id") or settings.ynab_default_account_id
    if not account_id:
        raise ValueError("Validation payload is missing account_id and no default account is configured")
    if account_id == UNKNOWN_ACCOUNT_ID:
        raise ValueError("Validation payload account is unknown. Select a valid YNAB account before syncing")

    reference_data = get_cached_reference_data(db, settings)
    allowed_account_ids = {item.entity_id for item in reference_data["accounts"]}
    allowed_category_ids = {item.entity_id for item in reference_data["categories"]}

    if account_id not in allowed_account_ids:
        raise ValueError(f"Validation payload has invalid account_id: {account_id}")

    splits = payload.get("splits", [])
    split_rows = splits if isinstance(splits, list) else []
    has_splits = len(split_rows) > 0
    category_id = payload.get("category_id")

    kind = payload.get("transaction_kind") or "purchase"
    outflow = kind != "refund"

    total_milliunits = dollars_to_milliunits(payload["total_amount"], outflow=outflow)

    if has_splits:
        split_total = sum((Decimal(str(split.get("amount", 0))) for split in split_rows), Decimal("0"))
        if abs(split_total - Decimal(str(payload["total_amount"]))) > Decimal("0.01"):
            raise ValueError("Split amounts must sum to total amount")
        for split in split_rows:
            split_category_id = split.get("category_id")
            if split_category_id not in allowed_category_ids:
                raise ValueError(f"Validation payload has invalid split category_id: {split_category_id}")

        # Exact milliunit invariant: sum of split milliunits must equal total milliunits exactly.
        # The dollar-level check above is the user-facing early warning; this is the
        # authoritative gate.  Independent per-split rounding can produce a legitimate
        # 1-milliunit drift — that is a bug upstream (the allocation workspace is
        # responsible for producing exact-cent splits); do not silently adjust here.
        split_milliunits = [dollars_to_milliunits(split["amount"], outflow=outflow) for split in split_rows]
        split_milliunits_sum = sum(split_milliunits)
        if split_milliunits_sum != total_milliunits:
            raise ValueError(
                f"Split milliunits sum {split_milliunits_sum} != total milliunits {total_milliunits}. "
                "Splits must sum exactly to the transaction total in integer milliunits. "
                "Adjust split amounts upstream (allocation workspace) to resolve the mismatch."
            )
    else:
        if not category_id:
            raise ValueError("Validation payload must include category_id for single-category mode")
        if category_id not in allowed_category_ids:
            raise ValueError(f"Validation payload has invalid category_id: {category_id}")

    base_memo = payload.get("memo", "")
    if kind == "refund":
        base_memo = _ensure_refund_memo_prefix(base_memo)

    transaction_payload: dict[str, Any] = {
        "account_id": account_id,
        "date": payload["transaction_date"],
        "amount": total_milliunits,
        "payee_name": payload["payee_name"],
        "memo": _append_receipt_id_marker(base_memo, receipt.id),
        "import_id": _build_import_id(receipt.id),
    }
    if has_splits:
        transaction_payload["subtransactions"] = [
            {
                "amount": split_milliunits[i],
                "category_id": split_rows[i]["category_id"],
                "memo": split_rows[i].get("memo", ""),
            }
            for i in range(len(split_rows))
        ]
    else:
        transaction_payload["category_id"] = category_id
    return transaction_payload


_STRUCTURE_IGNORED_REASON = (
    "YNAB ignored the split-structure update (likely a bank-imported transaction). "
    "The YNAB transaction was left unchanged; fix the split structure manually in YNAB."
)

_PRIOR_TRANSACTION_DELETED_REASON = (
    "The previously-synced YNAB transaction was deleted in YNAB. A new transaction "
    "was NOT created to avoid duplication — review and re-sync manually if it should "
    "be re-added."
)


def _create_transaction_idempotent(
    client: YNABClient,
    budget_id: str,
    transaction_payload: dict[str, Any],
    *,
    receipt_id: str,
) -> dict[str, Any]:
    """Call client.create_transaction and resolve HTTP 409 idempotently.

    YNAB returns 409 (not an echo) when a transaction with the same import_id
    already exists on that account.  Resolution:
    1. Call list_transactions_since(budget_id, since_date=payload["date"]) to fetch
       all transactions from the receipt date onward.
    2. Match the transaction whose import_id == _build_import_id(receipt_id).
       Fall back to matching the [receipt_id:...] memo marker if import_id is absent
       from the list response (some endpoints omit it).
    3. If found: return it as if the create succeeded (idempotent success).
    4. If not found: re-raise (genuine conflict — should not happen with deterministic
       per-receipt import_ids, but avoids silent data loss if YNAB behavior changes).
    """
    try:
        return client.create_transaction(budget_id, transaction_payload)
    except YNABConflictError:
        expected_import_id = _build_import_id(receipt_id)
        since_date = str(transaction_payload.get("date", ""))
        candidates = client.list_transactions_since(budget_id, since_date)
        memo_marker = _receipt_id_marker(receipt_id)
        for txn in candidates:
            if txn.get("deleted"):
                continue
            # Primary match: import_id (most reliable)
            if txn.get("import_id") == expected_import_id:
                logger.info(
                    "409 resolved via import_id match: receipt_id=%s txn_id=%s",
                    receipt_id,
                    txn.get("id"),
                )
                return txn
            # Fallback match: [receipt_id:...] memo marker
            if memo_marker in str(txn.get("memo") or ""):
                logger.info(
                    "409 resolved via memo marker fallback: receipt_id=%s txn_id=%s",
                    receipt_id,
                    txn.get("id"),
                )
                return txn
        # No matching transaction found — genuine (unexpected) conflict; re-raise.
        logger.error(
            "409 conflict but no matching transaction found for receipt_id=%s import_id=%s",
            receipt_id,
            expected_import_id,
        )
        raise


def _sync_update_existing(
    client: YNABClient,
    budget_id: str,
    settings: Settings,
    sync_row: YNABSync,
    transaction_payload: dict[str, Any],
    prior_success_sync: YNABSync,
    validation_payload: dict[str, Any],
    *,
    receipt: Receipt,
) -> tuple[bool, str | None]:
    """Update the YNAB transaction from a previous successful sync.

    Returns (structure_applied, review_reason):
      - structure_applied True = YNAB applied the change.
      - structure_applied False = caller must write NEEDS_REVIEW; review_reason (when
        set) overrides the default structure-ignored message. False is returned both
        when YNAB ignored the split/category structure change AND when the
        previously-synced transaction was deleted in YNAB (B-01: we do NOT recreate).
    """
    target_transaction_id = (
        prior_success_sync.created_transaction_id
        or prior_success_sync.matched_transaction_id
    )
    previous_request = prior_success_sync.raw_request or {}
    previous_transaction = previous_request.get("transaction", {})
    lookup_transaction_payload = previous_transaction if previous_transaction else transaction_payload
    previous_date = str(previous_transaction.get("date") or validation_payload["transaction_date"])
    since_date = min(str(validation_payload["transaction_date"]), previous_date)

    existing_target_transaction: dict[str, Any] | None = None
    if target_transaction_id:
        existing_target_transaction = _get_transaction_by_id(client, budget_id, target_transaction_id)

    if existing_target_transaction is None:
        if target_transaction_id:
            logger.warning(
                "Previously synced transaction missing in YNAB receipt_id=%s transaction_id=%s; searching exact match",
                sync_row.receipt_id,
                target_transaction_id,
            )
        matched_exact = _find_exact_transaction_match(
            client, budget_id, lookup_transaction_payload, since_date=since_date
        )
        if matched_exact is not None:
            target_transaction_id = matched_exact["id"]
            existing_target_transaction = matched_exact

    updated_flag_color = settings.ynab_updated_transaction_flag_color

    if existing_target_transaction is not None:
        update_payload_with_flags = dict(transaction_payload)
        update_payload_with_flags["approved"] = False
        if updated_flag_color:
            update_payload_with_flags["flag_color"] = updated_flag_color
        sync_row.raw_request = {"transaction": update_payload_with_flags}
        ynab_response, final_id, sync_status, structure_applied = _update_or_replace_transaction(
            client, budget_id, target_transaction_id, update_payload_with_flags, existing_target_transaction
        )
        # Always record the matched id (delete-recreate is prohibited; final_id == target_transaction_id).
        sync_row.status = sync_status
        sync_row.matched_transaction_id = final_id
        sync_row.raw_response = ynab_response

        return structure_applied, None
    else:
        # Previously-synced transaction was deleted in YNAB (manually) and no exact
        # match was found. B-01: do NOT recreate — that would silently duplicate or
        # resurrect a deliberately-removed transaction. Flag the receipt for human
        # review instead; the caller writes NEEDS_REVIEW with the reason below.
        logger.warning(
            "Previously-synced YNAB transaction missing for receipt_id=%s; "
            "flagging for review instead of recreating",
            sync_row.receipt_id,
        )
        sync_row.raw_request = {"transaction": transaction_payload}
        sync_row.status = YNABSyncStatus.FAILED.value
        sync_row.error_text = _PRIOR_TRANSACTION_DELETED_REASON
        return False, _PRIOR_TRANSACTION_DELETED_REASON


def _sync_match_or_create(
    client: YNABClient,
    budget_id: str,
    settings: Settings,
    sync_row: YNABSync,
    transaction_payload: dict[str, Any],
    validation: Validation,
    allow_update_match: bool,
    receipt_id: str,
    force_create: bool,
    *,
    receipt: Receipt,
) -> bool:
    """Match an existing YNAB transaction or create a new one.

    Returns structure_applied (False → caller must write NEEDS_REVIEW status directly).
    """
    receipt_date = date.fromisoformat(transaction_payload["date"])
    total_milliunits = transaction_payload["amount"]
    validation_payload = validation.payload

    matched: dict[str, Any] | None = None
    if not force_create:
        transaction_candidates = client.list_transactions_since(budget_id, validation_payload["transaction_date"])
        matched = _match_transaction(
            transaction_candidates,
            total_milliunits,
            receipt_date,
            receipt_date + timedelta(days=3),
            payee_name=validation_payload.get("payee_name", ""),
        )

    if matched and allow_update_match:
        updated_flag_color = settings.ynab_updated_transaction_flag_color
        if _ynab_has_user_data(matched, desired_payload=transaction_payload):
            # YNAB is source of truth: the user has manually entered data
            # (memo/splits/category). Preserve all YNAB fields; only append the
            # receipt_id marker to the existing memo.
            ynab_memo_with_marker = _append_receipt_id_marker(matched.get("memo"), receipt_id)
            minimal_update = {
                "memo": ynab_memo_with_marker,
                "approved": False,
                "flag_color": updated_flag_color or None,
            }
            full_preserved_payload = _full_transaction_payload_from_ynab_transaction(
                matched,
                memo_override=ynab_memo_with_marker,
                include_flags=True,
                flag_color=updated_flag_color,
            )
            sync_row.raw_request = {
                "transaction": full_preserved_payload,
                "applied_update": minimal_update,
            }
            ynab_response = client.update_transaction(budget_id, matched["id"], minimal_update)
            sync_row.status = YNABSyncStatus.MATCHED_UPDATED.value
            sync_row.matched_transaction_id = matched["id"]
            sync_row.raw_response = ynab_response

            # Update the validation payload to reflect YNAB's data so the
            # frontend form shows what YNAB actually has.
            ynab_category_id = matched.get("category_id")
            active_subtransactions = [sub for sub in matched.get("subtransactions", []) if not sub.get("deleted")]
            updated_payload = dict(validation.payload)
            updated_payload["memo"] = _strip_receipt_id_marker(matched.get("memo"))
            updated_payload["payee_name"] = matched.get("payee_name") or validation.payload.get("payee_name", "")
            if active_subtransactions:
                sub_amounts = [int(sub.get("amount", 0)) for sub in active_subtransactions]
                has_positive = any(a > 0 for a in sub_amounts)
                has_negative = any(a < 0 for a in sub_amounts)
                if has_positive and has_negative:
                    # Mixed inflow/outflow splits — flag for manual review, do NOT overwrite splits.
                    # Return False so the caller writes NEEDS_REVIEW directly (no transient SYNCED).
                    receipt.status = ReceiptStatus.NEEDS_REVIEW.value
                    receipt.status_reason = "YNAB transaction has mixed inflow/outflow splits; manual review required."
                    # Do not update the payload — leave as-is.
                    return False
                else:
                    total_amount_mu = int(matched.get("amount", 0))
                    ynab_kind = "refund" if total_amount_mu > 0 else "purchase"
                    updated_payload["transaction_kind"] = ynab_kind
                    updated_payload["category_id"] = None
                    updated_payload["splits"] = [
                        {
                            "category_id": sub.get("category_id", ""),
                            "amount": abs(milliunits_to_dollars(int(sub.get("amount", 0)))),
                            "memo": str(sub.get("memo") or ""),
                        }
                        for sub in active_subtransactions
                    ]
                    validation.payload = updated_payload
                    flag_modified(validation, "payload")
            elif ynab_category_id:
                total_amount_mu = int(matched.get("amount", 0))
                ynab_kind = "refund" if total_amount_mu > 0 else "purchase"
                updated_payload["transaction_kind"] = ynab_kind
                updated_payload["category_id"] = ynab_category_id
                updated_payload["splits"] = []
                validation.payload = updated_payload
                flag_modified(validation, "payload")
            return True
        else:
            # No user data in YNAB — receipt is source of truth (existing behavior).
            update_payload_with_flags = dict(transaction_payload)
            update_payload_with_flags["approved"] = False
            if updated_flag_color:
                update_payload_with_flags["flag_color"] = updated_flag_color
            sync_row.raw_request = {"transaction": update_payload_with_flags}
            ynab_response, final_id, sync_status, structure_applied = _update_or_replace_transaction(
                client, budget_id, matched["id"], update_payload_with_flags, matched
            )
            # Always record matched id (delete-recreate is prohibited).
            sync_row.status = sync_status
            sync_row.matched_transaction_id = final_id
            sync_row.raw_response = ynab_response
            return structure_applied
    else:
        new_flag_color = settings.ynab_new_transaction_flag_color
        new_transaction_payload = dict(transaction_payload)
        new_transaction_payload["approved"] = False
        if new_flag_color:
            new_transaction_payload["flag_color"] = new_flag_color
        sync_row.raw_request = {"transaction": new_transaction_payload}
        ynab_response = _create_transaction_idempotent(
            client, budget_id, new_transaction_payload, receipt_id=receipt_id
        )
        sync_row.status = YNABSyncStatus.CREATED.value
        sync_row.created_transaction_id = ynab_response.get("id")
        sync_row.raw_response = ynab_response
        return True


def _apply_dry_run(
    db: Session,
    receipt: Receipt,
    validation: Validation,
    sync_row: YNABSync,
    persisted_payload: dict[str, Any],
    idempotency_key: str,
    started_perf: float,
) -> dict[str, Any]:
    """Persist the would-be transaction payload without calling YNAB."""
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    finished_at = utcnow()

    sync_row.status = YNABSyncStatus.DRY_RUN.value
    sync_row.raw_request = {"transaction": persisted_payload}
    sync_row.raw_response = None
    sync_row.matched_transaction_id = None
    sync_row.created_transaction_id = None
    sync_row.duration_ms = duration_ms
    sync_row.completed_at = finished_at

    receipt.status = ReceiptStatus.NEEDS_REVIEW.value
    receipt.status_reason = "Dry run: transaction payload built and saved but not sent to YNAB."
    receipt.sync_completed_at = finished_at

    db.add(
        TimingMetric(
            receipt_id=receipt.id,
            metric_name="sync_duration_ms",
            metric_value_ms=duration_ms,
            metadata_json={"sync_id": sync_row.id, "idempotency_key": idempotency_key, "dry_run": True},
        )
    )

    logger.info(
        "YNAB dry-run for receipt_id=%s: payload persisted, no write performed request=%s",
        receipt.id,
        sync_row.raw_request,
    )
    db.commit()
    return {
        "status": YNABSyncStatus.DRY_RUN.value,
        "idempotency_key": idempotency_key,
        "transaction_id": None,
        "already_synced": False,
        "dry_run": True,
    }


def _apply_post_sync(
    db: Session,
    receipt: Receipt,
    validation: Validation,
    sync_row: YNABSync,
    settings: Settings,
    idempotency_key: str,
    started_perf: float,
    *,
    structure_applied: bool = True,
    review_reason: str | None = None,
) -> dict[str, Any]:
    """Persist YNAB write result, then run bookkeeping (gamification + corrections).

    TASK 4 — isolation guarantee:
    1. The YNAB write result (sync_row status/timing, receipt final status) is committed
       immediately in the FIRST commit below.  This is durable before any bookkeeping
       runs.  When structure_applied=False the final status is written as NEEDS_REVIEW
       directly in commit-1 — there is no transient SYNCED window.
    2. Gamification and corrections-resolution are wrapped in a single try/except.
       Failures are logged and recorded as incidents WITHOUT re-raising — they do NOT
       fail the sync.  bookkeeping_ok=False is included in the returned dict.
    3. The outer exception handler in sync_receipt_to_ynab handles only genuine YNAB
       write failures, not bookkeeping failures (those are fully caught here).
    """
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    finished_at = utcnow()

    # --- Commit 1: YNAB write result (durable before bookkeeping) ---
    sync_row.duration_ms = duration_ms
    sync_row.completed_at = finished_at
    sync_row.structure_applied = structure_applied
    receipt.sync_completed_at = finished_at
    if not structure_applied:
        # Write NEEDS_REVIEW directly (no transient SYNCED → NEEDS_REVIEW window).
        # review_reason lets callers supply a specific message (e.g. B-01 deleted-txn);
        # default is the structure-ignored message.
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        receipt.status_reason = review_reason or _STRUCTURE_IGNORED_REASON
    else:
        receipt.status = ReceiptStatus.SYNCED.value
        receipt.status_reason = None

    db.add(
        TimingMetric(
            receipt_id=receipt.id,
            metric_name="sync_duration_ms",
            metric_value_ms=duration_ms,
            metadata_json={"sync_id": sync_row.id, "idempotency_key": idempotency_key},
        )
    )
    logger.info(
        "Finished YNAB sync receipt_id=%s status=%s transaction_id=%s response=%s",
        receipt.id,
        sync_row.status,
        sync_row.created_transaction_id or sync_row.matched_transaction_id,
        sync_row.raw_response,
    )
    db.commit()

    # --- Bookkeeping: gamification + corrections (non-fatal) ---
    bookkeeping_ok = True
    try:
        apply_sync_gamification(
            db,
            receipt=receipt,
            validation=validation,
            synced_at=finished_at,
            settings=settings,
        )

        # Evaluate skip-pass awards in this committing context so they persist.
        # Using the bookkeeping path (which calls db.commit() below) guarantees
        # idempotent awards survive process restart; the dashboard GET merely
        # reads the already-committed balance.
        try:
            _now = utcnow()
            _tokens = _get_or_create_tokens(db)
            _all_rows = list(db.scalars(select(GameReceiptStateModel)))
            _week_fires = _load_week_fires_by_start(db)
            _evaluate_passes(
                db,
                _tokens,
                current_streak=0,
                max_streak=0,
                all_rows=_all_rows,
                week_fires_by_start=_week_fires,
                now=_now,
                settings=settings,
            )
        except Exception:
            logger.exception(
                "Pass evaluation failed for receipt %s (non-fatal)", receipt.id
            )

        unresolved_corrections = list(
            db.scalars(
                select(ReceiptCorrection).where(
                    ReceiptCorrection.receipt_id == receipt.id,
                    ReceiptCorrection.resynced_at.is_(None),
                )
            )
        )
        for correction in unresolved_corrections:
            correction.resynced_at = finished_at

        # Learn card→account mapping from this sync (non-blocking inner try).
        try:
            latest_twin = db.scalar(
                select(ReceiptTwin)
                .where(ReceiptTwin.receipt_id == receipt.id)
                .order_by(ReceiptTwin.version.desc())
            )
            if latest_twin is not None and isinstance(latest_twin.payload, dict):
                card_last_four = latest_twin.payload.get("card_last_four")
                synced_account_id = validation.payload.get("account_id") if isinstance(validation.payload, dict) else None
                if card_last_four and synced_account_id and settings.ynab_budget_id:
                    upsert_card_mapping(
                        db,
                        budget_id=settings.ynab_budget_id,
                        card_last_four=card_last_four,
                        account_id=synced_account_id,
                    )
        except Exception:
            logger.exception(
                "Card mapping upsert failed for receipt %s (non-fatal, sync already committed)",
                receipt.id,
            )

        # Learn payee→category memory from this sync (non-blocking inner try).
        try:
            if isinstance(validation.payload, dict) and settings.ynab_budget_id:
                _latest_twin = db.scalar(
                    select(ReceiptTwin)
                    .where(ReceiptTwin.receipt_id == receipt.id)
                    .order_by(ReceiptTwin.version.desc())
                )
                payee_name = None
                if _latest_twin is not None and isinstance(_latest_twin.payload, dict):
                    payee_name = _latest_twin.payload.get("store_name") or validation.payload.get("payee_name")
                else:
                    payee_name = validation.payload.get("payee_name")
                # On the adopt/update path the payload splits were rewritten from
                # YNAB but the workspace assignments were not — a template built
                # from that pair maps items to the wrong lanes. Only learn from
                # clean creates.
                adopted = sync_row.status == YNABSyncStatus.MATCHED_UPDATED.value
                if payee_name and settings.ynab_budget_id and not adopted:
                    from app.services.payee_memory import build_template_from_validation, upsert_payee_memory
                    template = build_template_from_validation(
                        validation.payload,
                        validation.allocation_workspace if isinstance(validation.allocation_workspace, dict) else None,
                    )
                    category_id_to_learn: str | None = None
                    if template is None:
                        category_id_to_learn = validation.payload.get("category_id") or None
                    upsert_payee_memory(
                        db,
                        budget_id=settings.ynab_budget_id,
                        payee_name=payee_name,
                        category_id=category_id_to_learn,
                        template=template,
                    )
        except Exception:
            logger.exception(
                "Payee memory upsert failed for receipt %s (non-fatal, sync already committed)",
                receipt.id,
            )

        db.commit()
    except Exception:
        logger.exception("Post-sync bookkeeping failed for receipt %s (sync is already committed)", receipt.id)
        bookkeeping_ok = False
        # Record a non-fatal incident (mirror existing incident idiom; do NOT re-raise).
        try:
            record_incident(
                db,
                incident_type="bookkeeping_sync_failure",
                severity="warning",
                title="Post-sync bookkeeping failed",
                message=f"Gamification or corrections update failed for receipt {receipt.id} after sync. Sync is committed.",
                details={"receipt_id": receipt.id},
                idempotency_key=f"bookkeeping_sync_failure:{receipt.id}:{idempotency_key}",
            )
            receipt.status_reason = "Sync committed; post-sync bookkeeping failed (non-fatal)."
            db.commit()
        except Exception:
            logger.exception("Failed to record bookkeeping incident for receipt %s", receipt.id)
            try:
                db.rollback()
            except Exception:
                pass

    return {
        "status": sync_row.status,
        "idempotency_key": idempotency_key,
        "transaction_id": sync_row.created_transaction_id or sync_row.matched_transaction_id,
        "already_synced": False,
        "bookkeeping_ok": bookkeeping_ok,
    }


def sync_receipt_to_ynab(
    db: Session,
    settings: Settings,
    receipt_id: str,
    force_create: bool,
    allow_update_match: bool,
) -> dict[str, Any]:
    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise ValueError(f"Receipt not found: {receipt_id}")

    validation = get_latest_validation(db, receipt_id)
    if not validation or not validation.is_valid:
        raise ValueError("Receipt does not have a valid validation draft")

    duplicate_state = apply_semantic_duplicate_state(
        db,
        receipt=receipt,
        payload=validation.payload,
    )
    if duplicate_state.duplicate_of_receipt_id:
        db.commit()
        raise ValueError(
            f"Duplicate detected against receipt {duplicate_state.duplicate_of_receipt_id}. Resolve duplicate review before syncing."
        )

    prior_success_sync = _latest_successful_sync_for_receipt(db, receipt.id)
    idempotency_key = make_idempotency_key(receipt_id, validation.id, force_create, allow_update_match)
    sync_row = db.scalar(select(YNABSync).where(YNABSync.idempotency_key == idempotency_key))

    started_at = utcnow()
    started_perf = time.perf_counter()

    # --- Atomic claim on the YNABSync row ---
    # If a row with this idempotency_key already exists, inspect its status
    # before deciding whether to proceed.
    stuck_cutoff = started_at - timedelta(minutes=settings.stuck_job_timeout_minutes)
    row_was_preexisting = sync_row is not None

    if not row_was_preexisting:
        # INSERT a new row; guard against a concurrent INSERT with the same key
        # (race between two workers) by catching IntegrityError.
        from sqlalchemy.exc import IntegrityError
        new_row = YNABSync(
            receipt_id=receipt.id,
            validation_id=validation.id,
            idempotency_key=idempotency_key,
            status=YNABSyncStatus.RUNNING.value,
            match_mode="force_create" if force_create else ("update_existing" if prior_success_sync is not None else "match_or_create"),
            started_at=started_at,
        )
        db.add(new_row)
        try:
            db.commit()
            sync_row = new_row
        except IntegrityError:
            db.rollback()
            # Another worker inserted first; re-select and treat as preexisting.
            sync_row = db.scalar(select(YNABSync).where(YNABSync.idempotency_key == idempotency_key))
            row_was_preexisting = True

    # Deduplication guard: only applies to rows that existed before this
    # invocation (row_was_preexisting==True) and are currently RUNNING.
    if row_was_preexisting and sync_row is not None and sync_row.status == YNABSyncStatus.RUNNING.value:
        row_started_at = sync_row.started_at
        if row_started_at is not None:
            # Make comparable regardless of timezone-awareness.
            row_started_naive = row_started_at.replace(tzinfo=None) if row_started_at.tzinfo is not None else row_started_at
            stuck_cutoff_naive = stuck_cutoff.replace(tzinfo=None) if stuck_cutoff.tzinfo is not None else stuck_cutoff
            if row_started_naive > stuck_cutoff_naive:
                # Row is fresh — another worker is genuinely still running.
                logger.info(
                    "Skipping duplicate worker invocation for receipt_id=%s idempotency_key=%s",
                    receipt_id,
                    idempotency_key,
                )
                return {
                    "status": YNABSyncStatus.RUNNING.value,
                    "idempotency_key": idempotency_key,
                    "transaction_id": None,
                    "already_synced": False,
                    "skipped_duplicate": True,
                }
            # Row is stale (started_at older than timeout) — reclaim it and proceed.
            logger.warning(
                "Reclaiming stale RUNNING sync row for receipt_id=%s idempotency_key=%s",
                receipt_id,
                idempotency_key,
            )

    # Claim/reclaim the row: update status to RUNNING and refresh started_at.
    # For a newly-inserted row (status already RUNNING), this refreshes metadata.
    receipt.status = ReceiptStatus.SYNCING.value
    receipt.status_reason = None
    receipt.sync_started_at = started_at

    sync_row.match_mode = "force_create" if force_create else ("update_existing" if prior_success_sync is not None else "match_or_create")
    sync_row.validation_id = validation.id
    sync_row.status = YNABSyncStatus.RUNNING.value
    sync_row.started_at = started_at
    # Evidence preservation: do NOT blank matched/created IDs or raw evidence.
    # Only clear the error from the previous attempt.
    sync_row.error_text = None
    db.commit()

    try:
        if not settings.ynab_budget_id:
            raise ValueError("YNAB_BUDGET_ID is not configured")

        if not settings.ynab_sync_enabled:
            raise YNABSyncDisabledError(
                "YNAB sync is disabled (YNAB_SYNC_ENABLED=false). "
                "No transaction was written."
            )

        # Build + validate the full payload BEFORE any client construction so
        # dry-run exercises the exact same validation as the live path.
        transaction_payload = _build_sync_transaction_payload(db, receipt, validation, settings)

        new_flag_color = settings.ynab_new_transaction_flag_color
        persisted_payload = dict(transaction_payload)
        persisted_payload["approved"] = False
        if new_flag_color:
            persisted_payload["flag_color"] = new_flag_color

        if settings.ynab_dry_run:
            return _apply_dry_run(
                db, receipt, validation, sync_row,
                persisted_payload, idempotency_key, started_perf,
            )

        client = get_ynab_client(settings)

        logger.info(
            "Starting YNAB sync receipt_id=%s validation_id=%s mode=%s request=%s",
            receipt.id,
            validation.id,
            sync_row.match_mode,
            {"transaction": transaction_payload},
        )

        # --- Task 3: verify-before-create ---
        # If a prior attempt left a created_transaction_id on this row, check
        # whether that transaction still exists in YNAB.  If it does, we can
        # treat this as an idempotent success without a second create call.
        if sync_row.created_transaction_id:
            budget_id = settings.ynab_budget_id
            live_txn = _get_transaction_by_id(client, budget_id, sync_row.created_transaction_id)
            if live_txn is not None:
                logger.info(
                    "Idempotent: transaction %s still exists in YNAB for receipt_id=%s; skipping create",
                    sync_row.created_transaction_id,
                    receipt.id,
                )
                sync_row.status = YNABSyncStatus.CREATED.value
                sync_row.raw_response = live_txn
                return _apply_post_sync(db, receipt, validation, sync_row, settings, idempotency_key, started_perf)
            # Transaction was deleted — fall through to normal flow (fresh create).
            logger.info(
                "Previously created transaction %s no longer in YNAB for receipt_id=%s; proceeding with fresh create",
                sync_row.created_transaction_id,
                receipt.id,
            )
            # Clear stale evidence so the new create result overwrites cleanly.
            sync_row.created_transaction_id = None
            sync_row.raw_response = None

        # Note: dry_run→live reuses the same sync_row; only error_text is cleared above.
        review_reason: str | None = None
        if prior_success_sync is not None and not force_create:
            structure_applied, review_reason = _sync_update_existing(
                client, settings.ynab_budget_id, settings, sync_row,
                transaction_payload, prior_success_sync, validation.payload,
                receipt=receipt,
            )
        else:
            # force_create semantics: with deterministic per-receipt import_id,
            # force_create bypasses the match-existing step and always calls create.
            # After Finding 1's fix, a 409 response is resolved idempotently by
            # _create_transaction_idempotent — so force_create can never double-create
            # a receipt; it simply resolves to the already-existing transaction.
            structure_applied = _sync_match_or_create(
                client, settings.ynab_budget_id, settings, sync_row,
                transaction_payload, validation, allow_update_match, receipt.id, force_create,
                receipt=receipt,
            )

        result = _apply_post_sync(
            db, receipt, validation, sync_row, settings, idempotency_key, started_perf,
            structure_applied=structure_applied,
            review_reason=review_reason,
        )
        result["structure_applied"] = structure_applied
        return result

    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        failed_at = utcnow()

        sync_row.status = YNABSyncStatus.FAILED.value
        sync_row.error_text = str(exc)
        sync_row.duration_ms = duration_ms
        sync_row.completed_at = failed_at

        receipt.status = ReceiptStatus.ERROR_SYNC.value
        receipt.status_reason = str(exc)
        receipt.sync_completed_at = failed_at

        logger.exception("YNAB sync failed receipt_id=%s error=%s", receipt.id, exc)
        db.commit()
        raise


def compute_status_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Receipt.status, func.count())
        .where(Receipt.deleted_at.is_(None))
        .group_by(Receipt.status)
    ).all()
    return {status: count for status, count in rows}


def average_metric(db: Session, metric_name: str) -> float | None:
    value = db.scalar(select(func.avg(TimingMetric.metric_value_ms)).where(TimingMetric.metric_name == metric_name))
    return float(value) if value is not None else None
