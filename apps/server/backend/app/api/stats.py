from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.schemas import StatsSummary
from app.services.ynab import average_metric, compute_status_counts

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/summary", response_model=StatsSummary)
def get_summary(db: Session = Depends(db_session)) -> StatsSummary:
    return StatsSummary(
        status_counts=compute_status_counts(db),
        avg_extraction_duration_ms=average_metric(db, "extraction_duration_ms"),
        avg_validation_duration_ms=average_metric(db, "validation_duration_ms"),
        avg_receipt_age_at_validation_ms=average_metric(db, "receipt_age_at_validation_ms"),
    )
