from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import game, health, ingestion, receipts, stats, ynab
from app.config import get_settings
from app.db import SessionLocal
from app.log_setup import configure_logging
from app.services.ynab import refresh_ynab_cache
from app.migrations import ensure_schema_current

settings = get_settings()
configure_logging(settings.log_file_path)
logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(ensure_schema_current)
    refresh_task: asyncio.Task[None] | None = None
    if settings.ynab_access_token and settings.ynab_budget_id:
        try:
            await asyncio.to_thread(_refresh_cache_once)
        except Exception:
            logger.exception("Startup YNAB cache refresh failed")
        refresh_task = asyncio.create_task(_periodic_cache_refresh())

    try:
        yield
    finally:
        if refresh_task:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task

app = FastAPI(title=settings.app_name, lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router)
app.include_router(receipts.router, prefix=settings.api_prefix)
app.include_router(ingestion.router, prefix=settings.api_prefix)
app.include_router(ynab.router, prefix=settings.api_prefix)
app.include_router(stats.router, prefix=settings.api_prefix)
app.include_router(game.router, prefix=settings.api_prefix)
