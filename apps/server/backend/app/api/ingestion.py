from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.services.ingestion import IngestionScanner

router = APIRouter(prefix="/ingest", tags=["ingestion"])
_SCANNER: IngestionScanner | None = None


def get_scanner(settings: Settings) -> IngestionScanner:
    global _SCANNER
    if _SCANNER is None:
        _SCANNER = IngestionScanner(settings)
    return _SCANNER


@router.post("/scan")
def trigger_scan(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> dict[str, int | list[str]]:
    scanner = get_scanner(settings)
    result = scanner.scan_once(db)
    return {
        "ingested_count": result.ingested_count,
        "duplicate_count": result.duplicate_count,
        "skipped_count": result.skipped_count,
        "error_count": result.error_count,
        "errors": result.errors,
    }
