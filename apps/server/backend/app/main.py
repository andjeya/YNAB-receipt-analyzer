from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import card_mappings, config, game, health, ingestion, receipts, stats, ynab
from app.config import get_settings
from app.db import SessionLocal
from app.enums import ReceiptStatus, YNABSyncStatus
from app.log_setup import configure_logging
from app.models import Receipt, YNABSync
from app.services.ynab import refresh_ynab_cache
from app.services.retention import purge_soft_deleted_receipts
from app.migrations import ensure_schema_current
from app.utils import utcnow

settings = get_settings()
configure_logging(settings.log_file_path)
logger = logging.getLogger(__name__)


def _reset_stuck_jobs() -> None:
    timeout = timedelta(minutes=settings.stuck_job_timeout_minutes)
    cutoff = utcnow() - timeout
    with SessionLocal() as db:
        stuck_extracting = list(
            db.scalars(
                select(Receipt).where(
                    Receipt.status == ReceiptStatus.EXTRACTING.value,
                    Receipt.extraction_started_at < cutoff,
                    Receipt.deleted_at.is_(None),
                )
            )
        )
        stuck_syncing = list(
            db.scalars(
                select(Receipt).where(
                    Receipt.status == ReceiptStatus.SYNCING.value,
                    Receipt.sync_started_at < cutoff,
                    Receipt.deleted_at.is_(None),
                )
            )
        )
        # TASK 5c — also mark stale RUNNING YNABSync rows as FAILED so that
        # receipt and sync-row state remain coherent after a crash or restart.
        stuck_sync_rows = list(
            db.scalars(
                select(YNABSync).where(
                    YNABSync.status == YNABSyncStatus.RUNNING.value,
                    YNABSync.started_at < cutoff,
                )
            )
        )
        for receipt in stuck_extracting:
            logger.warning("Resetting stuck EXTRACTING receipt %s (started %s)", receipt.id, receipt.extraction_started_at)
            receipt.status = ReceiptStatus.INGESTED.value
            receipt.status_reason = "Reset from stuck EXTRACTING state on startup"
        for receipt in stuck_syncing:
            logger.warning("Resetting stuck SYNCING receipt %s (started %s)", receipt.id, receipt.sync_started_at)
            receipt.status = ReceiptStatus.NEEDS_REVIEW.value
            receipt.status_reason = "Reset from stuck SYNCING state on startup"
        for sync_row in stuck_sync_rows:
            logger.warning(
                "Resetting stuck RUNNING YNABSync row id=%s receipt_id=%s (started %s)",
                sync_row.id,
                sync_row.receipt_id,
                sync_row.started_at,
            )
            sync_row.status = YNABSyncStatus.FAILED.value
            sync_row.error_text = "Reset by stuck-job recovery"
        if stuck_extracting or stuck_syncing or stuck_sync_rows:
            db.commit()
            logger.info(
                "Reset %d stuck extracting, %d stuck syncing receipts, %d stuck sync rows",
                len(stuck_extracting),
                len(stuck_syncing),
                len(stuck_sync_rows),
            )


def _purge_soft_deleted() -> None:
    with SessionLocal() as db:
        purge_soft_deleted_receipts(db, settings)


def _refresh_cache_once() -> None:
    if not settings.ynab_access_token or not settings.ynab_budget_id:
        return

    with SessionLocal() as db:
        counts = refresh_ynab_cache(db, settings)
        logger.info(
            "YNAB cache refreshed: categories=%s accounts=%s payees=%s",
            counts["category_count"],
            counts["account_count"],
            counts["payee_count"],
        )


async def _periodic_cache_refresh() -> None:
    interval_seconds = settings.ynab_cache_refresh_interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(_refresh_cache_once)
        except Exception:
            logger.exception("Periodic YNAB cache refresh failed")


async def _periodic_purge() -> None:
    """Sweep expired soft-deletes on an interval so the Undo buffer is short
    (not just cleared on the next restart)."""
    interval_seconds = max(settings.soft_delete_purge_interval_seconds, 1)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(_purge_soft_deleted)
        except Exception:
            logger.exception("Periodic soft-delete purge failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_file_path)
    await asyncio.to_thread(ensure_schema_current)
    try:
        await asyncio.to_thread(_reset_stuck_jobs)
    except Exception:
        logger.exception("Stuck job reset failed on startup")
    try:
        await asyncio.to_thread(_purge_soft_deleted)
    except Exception:
        logger.exception("Soft-delete purge failed on startup")
    refresh_task: asyncio.Task[None] | None = None
    if settings.ynab_access_token and settings.ynab_budget_id:
        try:
            await asyncio.to_thread(_refresh_cache_once)
        except Exception:
            logger.exception("Startup YNAB cache refresh failed")
        refresh_task = asyncio.create_task(_periodic_cache_refresh())

    purge_task = asyncio.create_task(_periodic_purge())

    try:
        yield
    finally:
        for task in (refresh_task, purge_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

app = FastAPI(title=settings.app_name, lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Accept"],
    )

app.include_router(health.router)
app.include_router(receipts.router, prefix=settings.api_prefix)
app.include_router(ingestion.router, prefix=settings.api_prefix)
app.include_router(ynab.router, prefix=settings.api_prefix)
app.include_router(stats.router, prefix=settings.api_prefix)
app.include_router(game.router, prefix=settings.api_prefix)
app.include_router(config.router, prefix=settings.api_prefix)
app.include_router(card_mappings.router, prefix=settings.api_prefix)
