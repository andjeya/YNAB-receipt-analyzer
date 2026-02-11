from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.models import Receipt, TimingMetric, Validation, YNABCache, YNABSync
from receipt_shared.money import dollars_to_milliunits
from receipt_shared.ynab_client import YNABClient


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    for category in categories:
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


def list_cached_entities(db: Session, entity_type: str | None = None) -> list[YNABCache]:
    stmt = select(YNABCache).order_by(YNABCache.entity_type, YNABCache.name)
    if entity_type:
        stmt = stmt.where(YNABCache.entity_type == entity_type)
    return list(db.scalars(stmt))


def get_latest_validation(db: Session, receipt_id: str) -> Validation | None:
    return db.scalar(
        select(Validation)
        .where(Validation.receipt_id == receipt_id)
        .order_by(Validation.version.desc())
        .limit(1)
    )


def _build_subtransactions(validation_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "amount": dollars_to_milliunits(split["amount"], outflow=True),
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
) -> dict[str, Any] | None:
    for transaction in transactions:
        if transaction.get("deleted"):
            continue
        if int(transaction.get("amount", 0)) != amount_milliunits:
            continue
        txn_date = date.fromisoformat(transaction["date"])
        if receipt_date <= txn_date <= end_date:
            return transaction
    return None


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

    idempotency_key = make_idempotency_key(receipt_id, validation.id, force_create, allow_update_match)
    sync_row = db.scalar(select(YNABSync).where(YNABSync.idempotency_key == idempotency_key))
    if sync_row and sync_row.status in {
        YNABSyncStatus.MATCHED_UPDATED.value,
        YNABSyncStatus.CREATED.value,
    }:
        return {
            "status": sync_row.status,
            "idempotency_key": idempotency_key,
            "transaction_id": sync_row.created_transaction_id or sync_row.matched_transaction_id,
            "already_synced": True,
        }

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
            match_mode="force_create" if force_create else "match_or_create",
            started_at=started_at,
        )
        db.add(sync_row)

    sync_row.validation_id = validation.id
    sync_row.status = YNABSyncStatus.RUNNING.value
    sync_row.started_at = started_at
    sync_row.error_text = None
    db.commit()

    try:
        if not settings.ynab_budget_id:
            raise ValueError("YNAB_BUDGET_ID is not configured")

        client = get_ynab_client(settings)
        payload = validation.payload

        receipt_date = date.fromisoformat(payload["transaction_date"])
        total_milliunits = dollars_to_milliunits(payload["total_amount"], outflow=True)
        account_id = payload.get("account_id") or settings.ynab_default_account_id
        if not account_id:
            raise ValueError("Validation payload is missing account_id and no default account is configured")

        transaction_payload = {
            "account_id": account_id,
            "date": payload["transaction_date"],
            "amount": total_milliunits,
            "payee_name": payload["payee_name"],
            "memo": payload.get("memo", ""),
            "subtransactions": _build_subtransactions(payload),
        }
        sync_row.raw_request = {"transaction": transaction_payload}

        matched = None
        if not force_create:
            transaction_candidates = client.list_transactions_since(settings.ynab_budget_id, payload["transaction_date"])
            matched = _match_transaction(
                transaction_candidates,
                total_milliunits,
                receipt_date,
                receipt_date + timedelta(days=3),
            )

        if matched and allow_update_match:
            ynab_response = client.update_transaction(settings.ynab_budget_id, matched["id"], transaction_payload)
            sync_row.status = YNABSyncStatus.MATCHED_UPDATED.value
            sync_row.matched_transaction_id = matched["id"]
            sync_row.raw_response = ynab_response
        else:
            ynab_response = client.create_transaction(settings.ynab_budget_id, transaction_payload)
            sync_row.status = YNABSyncStatus.CREATED.value
            sync_row.created_transaction_id = ynab_response.get("id")
            sync_row.raw_response = ynab_response

        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        finished_at = utcnow()

        sync_row.duration_ms = duration_ms
        sync_row.completed_at = finished_at
        receipt.status = ReceiptStatus.SYNCED.value
        receipt.status_reason = None
        receipt.sync_completed_at = finished_at

        db.add(
            TimingMetric(
                receipt_id=receipt.id,
                metric_name="sync_duration_ms",
                metric_value_ms=duration_ms,
                metadata_json={"sync_id": sync_row.id, "idempotency_key": idempotency_key},
            )
        )
        db.commit()
        return {
            "status": sync_row.status,
            "idempotency_key": idempotency_key,
            "transaction_id": sync_row.created_transaction_id or sync_row.matched_transaction_id,
            "already_synced": False,
        }
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

        db.commit()
        raise


def compute_status_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Receipt.status, func.count()).group_by(Receipt.status)).all()
    return {status: count for status, count in rows}


def average_metric(db: Session, metric_name: str) -> float | None:
    value = db.scalar(select(func.avg(TimingMetric.metric_value_ms)).where(TimingMetric.metric_name == metric_name))
    return float(value) if value is not None else None
