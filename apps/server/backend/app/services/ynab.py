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
from app.models import Receipt, ReceiptCorrection, TimingMetric, Validation, YNABCache, YNABSync
from app.services.duplicates import apply_semantic_duplicate_state
from app.services.game import apply_sync_gamification
from app.services.incidents import record_incident
from app.services.validation import UNKNOWN_ACCOUNT_ID
from receipt_shared.money import dollars_to_milliunits, milliunits_to_dollars
from receipt_shared.ynab_client import YNABClient

logger = logging.getLogger(__name__)
RECEIPT_ID_MARKER_PREFIX = "[receipt_id:"


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
    update_payload: dict[str, Any] = dict(transaction_payload)
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
) -> tuple[dict[str, Any], str, str]:
    """Try PUT update; if YNAB ignores split/category changes, delete + create.

    Returns (ynab_response, final_transaction_id, sync_status).
    """
    update_payload = _build_update_transaction_payload(transaction_payload, existing_transaction)
    ynab_response = client.update_transaction(budget_id, target_transaction_id, update_payload)

    if _transaction_structure_matches_payload(ynab_response, transaction_payload):
        return ynab_response, target_transaction_id, YNABSyncStatus.MATCHED_UPDATED.value

    # YNAB silently ignored split/category structure changes.
    # The YNAB API does not support updating subtransactions on existing split
    # transactions, nor converting a split back to single-category.
    # Workaround: delete the old transaction and create a fresh one.
    logger.info(
        "YNAB ignored split/category changes on transaction %s; deleting and recreating",
        target_transaction_id,
    )
    client.delete_transaction(budget_id, target_transaction_id)
    ynab_response = client.create_transaction(budget_id, transaction_payload)
    new_id = ynab_response.get("id", "")
    return ynab_response, new_id, YNABSyncStatus.CREATED.value


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


def _sync_update_existing(
    client: YNABClient,
    budget_id: str,
    settings: Settings,
    sync_row: YNABSync,
    transaction_payload: dict[str, Any],
    prior_success_sync: YNABSync,
    validation_payload: dict[str, Any],
) -> None:
    """Update the YNAB transaction from a previous successful sync."""
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
    new_flag_color = settings.ynab_new_transaction_flag_color

    if existing_target_transaction is not None:
        update_payload_with_flags = dict(transaction_payload)
        update_payload_with_flags["approved"] = False
        if updated_flag_color:
            update_payload_with_flags["flag_color"] = updated_flag_color
        sync_row.raw_request = {"transaction": update_payload_with_flags}
        ynab_response, final_id, sync_status = _update_or_replace_transaction(
            client, budget_id, target_transaction_id, update_payload_with_flags, existing_target_transaction
        )
        sync_row.status = sync_status
        if sync_status == YNABSyncStatus.CREATED.value:
            sync_row.created_transaction_id = final_id
        else:
            sync_row.matched_transaction_id = final_id
        sync_row.raw_response = ynab_response
    else:
        # Previously-synced transaction was deleted from YNAB (manually
        # or by a prior failed delete+recreate).  Create a fresh one
        # instead of leaving the user stuck in an error loop.
        logger.warning(
            "Cannot find previous YNAB transaction for receipt_id=%s; creating new",
            sync_row.receipt_id,
        )
        new_transaction_payload = dict(transaction_payload)
        new_transaction_payload["approved"] = False
        if new_flag_color:
            new_transaction_payload["flag_color"] = new_flag_color
        sync_row.raw_request = {"transaction": new_transaction_payload}
        ynab_response = client.create_transaction(budget_id, new_transaction_payload)
        sync_row.status = YNABSyncStatus.CREATED.value
        sync_row.created_transaction_id = ynab_response.get("id")
        sync_row.raw_response = ynab_response


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
) -> None:
    """Match an existing YNAB transaction or create a new one."""
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
                    receipt.status = ReceiptStatus.NEEDS_REVIEW.value
                    receipt.status_reason = "YNAB transaction has mixed inflow/outflow splits; manual review required."
                    # Do not update the payload — leave as-is.
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
        else:
            # No user data in YNAB — receipt is source of truth (existing behavior).
            update_payload_with_flags = dict(transaction_payload)
            update_payload_with_flags["approved"] = False
            if updated_flag_color:
                update_payload_with_flags["flag_color"] = updated_flag_color
            sync_row.raw_request = {"transaction": update_payload_with_flags}
            ynab_response, final_id, sync_status = _update_or_replace_transaction(
                client, budget_id, matched["id"], update_payload_with_flags, matched
            )
            sync_row.status = sync_status
            if sync_status == YNABSyncStatus.CREATED.value:
                sync_row.created_transaction_id = final_id
            else:
                sync_row.matched_transaction_id = final_id
            sync_row.raw_response = ynab_response
    else:
        new_flag_color = settings.ynab_new_transaction_flag_color
        new_transaction_payload = dict(transaction_payload)
        new_transaction_payload["approved"] = False
        if new_flag_color:
            new_transaction_payload["flag_color"] = new_flag_color
        sync_row.raw_request = {"transaction": new_transaction_payload}
        ynab_response = client.create_transaction(budget_id, new_transaction_payload)
        sync_row.status = YNABSyncStatus.CREATED.value
        sync_row.created_transaction_id = ynab_response.get("id")
        sync_row.raw_response = ynab_response


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
) -> dict[str, Any]:
    """Record timing, run gamification, mark corrections resolved, commit, and return result."""
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    finished_at = utcnow()

    sync_row.duration_ms = duration_ms
    sync_row.completed_at = finished_at
    receipt.status = ReceiptStatus.SYNCED.value
    receipt.status_reason = None
    receipt.sync_completed_at = finished_at

    try:
        apply_sync_gamification(
            db,
            receipt=receipt,
            validation=validation,
            synced_at=finished_at,
            settings=settings,
        )
    except Exception:
        logger.exception("Gamification sync classification failed for receipt %s", receipt.id)
        record_incident(
            db,
            incident_type="gamification_sync_failure",
            severity="warning",
            title="Gamification sync classification failed",
            message=f"Failed to apply gamification for receipt {receipt.id} after sync",
            details={"receipt_id": receipt.id},
            idempotency_key=f"gamification_sync_failure:{receipt.id}:{idempotency_key}",
        )
        raise

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
    return {
        "status": sync_row.status,
        "idempotency_key": idempotency_key,
        "transaction_id": sync_row.created_transaction_id or sync_row.matched_transaction_id,
        "already_synced": False,
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

    receipt.status = ReceiptStatus.SYNCING.value
    receipt.status_reason = None
    receipt.sync_started_at = started_at

    if sync_row is None:
        sync_row = YNABSync(
            receipt_id=receipt.id,
            validation_id=validation.id,
            idempotency_key=idempotency_key,
            status=YNABSyncStatus.QUEUED.value,
            match_mode="force_create" if force_create else ("update_existing" if prior_success_sync is not None else "match_or_create"),
            started_at=started_at,
        )
        db.add(sync_row)

    sync_row.match_mode = "force_create" if force_create else ("update_existing" if prior_success_sync is not None else "match_or_create")
    sync_row.validation_id = validation.id
    sync_row.status = YNABSyncStatus.RUNNING.value
    sync_row.started_at = started_at
    sync_row.matched_transaction_id = None
    sync_row.created_transaction_id = None
    sync_row.raw_request = None
    sync_row.raw_response = None
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

        # Note: dry_run→live reuses the same sync_row; the field resets above clear stale dry-run fields.
        if prior_success_sync is not None and not force_create:
            _sync_update_existing(
                client, settings.ynab_budget_id, settings, sync_row,
                transaction_payload, prior_success_sync, validation.payload,
            )
        else:
            _sync_match_or_create(
                client, settings.ynab_budget_id, settings, sync_row,
                transaction_payload, validation, allow_update_match, receipt.id, force_create,
                receipt=receipt,
            )

        return _apply_post_sync(db, receipt, validation, sync_row, settings, idempotency_key, started_perf)

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
    rows = db.execute(select(Receipt.status, func.count()).group_by(Receipt.status)).all()
    return {status: count for status, count in rows}


def average_metric(db: Session, metric_name: str) -> float | None:
    value = db.scalar(select(func.avg(TimingMetric.metric_value_ms)).where(TimingMetric.metric_name == metric_name))
    return float(value) if value is not None else None
