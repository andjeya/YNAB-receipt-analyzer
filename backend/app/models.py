from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)

    latest_validation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    display_payee_name: Mapped[str | None] = mapped_column(String(255))
    display_total_milliunits: Mapped[int | None] = mapped_column(Integer)
    display_receipt_date: Mapped[date | None] = mapped_column(Date)

    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    extraction_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    extraction_runs: Mapped[list["ExtractionRun"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    validations: Mapped[list["Validation"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    ynab_sync_runs: Mapped[list["YNABSync"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    timing_metrics: Mapped[list["TimingMetric"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)

    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_errors: Mapped[list[str] | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="extraction_runs")


class Validation(Base):
    __tablename__ = "validations"
    __table_args__ = (UniqueConstraint("receipt_id", "version", name="uq_validation_receipt_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    errors: Mapped[list[str] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="validations")


class YNABCache(Base):
    __tablename__ = "ynab_cache"
    __table_args__ = (UniqueConstraint("budget_id", "entity_type", "entity_id", name="uq_ynab_cache_entity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    budget_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class YNABSync(Base):
    __tablename__ = "ynab_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    validation_id: Mapped[int] = mapped_column(Integer, ForeignKey("validations.id", ondelete="SET NULL"), nullable=True, index=True)

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    match_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    matched_transaction_id: Mapped[str | None] = mapped_column(String(64))
    created_transaction_id: Mapped[str | None] = mapped_column(String(64))

    raw_request: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_text: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="ynab_sync_runs")


class TimingMetric(Base):
    __tablename__ = "timing_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_value_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="timing_metrics")
