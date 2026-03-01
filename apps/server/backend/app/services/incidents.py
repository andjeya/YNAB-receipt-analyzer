from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GameIncident
from app.utils import utcnow


def record_incident(
    db: Session,
    *,
    incident_type: str,
    severity: str,
    title: str,
    message: str,
    details: dict[str, Any] | None,
    idempotency_key: str,
    created_at: datetime | None = None,
) -> GameIncident:
    existing = db.scalar(select(GameIncident).where(GameIncident.idempotency_key == idempotency_key))
    if existing is not None:
        return existing

    row = GameIncident(
        incident_type=incident_type,
        severity=severity,
        title=title,
        message=message,
        details_json=details,
        idempotency_key=idempotency_key,
        created_at=created_at or utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def list_incidents(db: Session, *, pending_only: bool = False, limit: int = 30) -> list[GameIncident]:
    stmt = select(GameIncident).order_by(GameIncident.created_at.asc(), GameIncident.id.asc())
    if pending_only:
        stmt = stmt.where(GameIncident.acknowledged_at.is_(None))
    return list(db.scalars(stmt.limit(limit)))


def acknowledge_incident(db: Session, incident_id: int) -> GameIncident | None:
    row = db.get(GameIncident, incident_id)
    if row is None:
        return None
    if row.acknowledged_at is None:
        row.acknowledged_at = utcnow()
    return row
