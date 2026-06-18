from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, true
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
    latest_twin_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    display_payee_name: Mapped[str | None] = mapped_column(String(255))
    display_total_milliunits: Mapped[int | None] = mapped_column(Integer)
    display_receipt_date: Mapped[date | None] = mapped_column(Date)
    semantic_payee_key: Mapped[str | None] = mapped_column(String(255), index=True)
    semantic_total_cents: Mapped[int | None] = mapped_column(Integer, index=True)
    semantic_transaction_date: Mapped[date | None] = mapped_column(Date, index=True)
    semantic_transaction_time: Mapped[str | None] = mapped_column(String(5), index=True)
    semantic_signature: Mapped[str | None] = mapped_column(String(64), index=True)
    duplicate_of_receipt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("receipts.id", ondelete="SET NULL"),
        index=True,
    )
    duplicate_override_signature: Mapped[str | None] = mapped_column(String(64))

    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    extraction_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    # Soft delete: set when the user discards a non-synced receipt. Filtered out
    # of all listings/queries; a background sweep hard-deletes (file + rows) after
    # the purge window so the user can Undo in the meantime.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    extraction_runs: Mapped[list["ExtractionRun"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    twins: Mapped[list["ReceiptTwin"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    validations: Mapped[list["Validation"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    ynab_sync_runs: Mapped[list["YNABSync"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    timing_metrics: Mapped[list["TimingMetric"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    game_receipt_state: Mapped["GameReceiptStateModel | None"] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        uselist=False,
    )
    game_events: Mapped[list["GameEvent"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    corrections: Mapped[list["ReceiptCorrection"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")
    candidate_sets: Mapped[list["ReceiptCandidateSet"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


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
    attempt_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="unified", index=True)
    is_primary_result: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    parent_run_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("extraction_runs.id", ondelete="SET NULL"),
        index=True,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="extraction_runs")
    parent_run: Mapped["ExtractionRun | None"] = relationship(
        remote_side="ExtractionRun.id",
        back_populates="fallback_runs",
        foreign_keys=[parent_run_id],
    )
    fallback_runs: Mapped[list["ExtractionRun"]] = relationship(
        back_populates="parent_run",
        foreign_keys=[parent_run_id],
    )


class ReceiptTwin(Base):
    __tablename__ = "receipt_twins"
    __table_args__ = (UniqueConstraint("receipt_id", "version", name="uq_receipt_twin_receipt_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confirmed_sections: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {"date_time": False, "total": False},
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="twins")


class Validation(Base):
    __tablename__ = "validations"
    __table_args__ = (UniqueConstraint("receipt_id", "version", name="uq_validation_receipt_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    allocation_workspace: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    errors: Mapped[list[str] | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="validations")


class ReceiptCandidateSet(Base):
    """Up to three complete, sum-to-total category/split arrangements offered to
    the user when a receipt's categorization is uncertain.

    Stored as a versioned sibling of Validation/ReceiptTwin so the money path
    (ValidationPayload) is never widened. Candidates carry ONLY category/splits +
    their allocation workspace — never date/total/account/payee. Choosing one
    merges its category/splits onto the CURRENT validation and re-validates; the
    money write still happens through the normal /sync path. `twin_version` is the
    enforced staleness guard (choosing 409s if the twin moved underneath, since the
    total/locks it sized splits against may have changed). `base_validation_version`
    records which draft the arrangements were derived from; it is NOT enforced —
    promotion merges onto the *current* validation and re-validates, so it stays safe
    even against a newer draft (and harmless edits like an account change don't block
    the cards).
    """

    __tablename__ = "receipt_candidate_sets"
    __table_args__ = (UniqueConstraint("receipt_id", "version", name="uq_receipt_candidate_set_receipt_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # "model_topk" | "tier0" | "type_to_organize" — drives the game-economy rule
    # (accepting an AI guess earns no manual-correction water; a type_to_organize
    # edit can).
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    twin_version: Mapped[int | None] = mapped_column(Integer)
    base_validation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    chosen_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="candidate_sets")


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
    # False when a "successful" (matched_updated) sync to YNAB had its split/category
    # structure IGNORED by YNAB (bank-imported lock) — the receipt is then left at
    # NEEDS_REVIEW for manual fixing. Such rows do NOT represent state YNAB holds, so
    # "Restore synced" must not treat them as a restore source.
    structure_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

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


class GameReceiptStateModel(Base):
    __tablename__ = "game_receipt_states"

    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    age_hours_at_validation: Mapped[float] = mapped_column(Float, nullable=False)
    streak_group_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    shredded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="game_receipt_state")


class GameStreak(Base):
    __tablename__ = "game_streaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_green_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    break_reason: Mapped[str | None] = mapped_column(String(32))
    active_streak_group_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameToken(Base):
    __tablename__ = "game_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    earned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameEvent(Base):
    __tablename__ = "game_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    receipt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("receipts.id", ondelete="SET NULL"),
        index=True,
    )
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    receipt: Mapped[Receipt | None] = relationship(back_populates="game_events")


class GameCorrectnessState(Base):
    __tablename__ = "game_correctness_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    water_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    water_earned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    water_spent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_extinguished_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    burn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_burned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameDebugSeed(Base):
    __tablename__ = "game_debug_seed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Correctness baseline.
    water_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    water_earned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    water_spent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fire_extinguished_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    burn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Momentum / pass token baseline.
    token_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_earned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_spent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Streak is now derived from GameReceiptStateModel history; these fields
    # are kept for legacy reads but no longer written during rebuild.
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_streak_group_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    break_reason: Mapped[str | None] = mapped_column(String(32))

    # Demo seeding: current-week flames to inject for UI demoing.
    current_week_flames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # NOTE: behavioral configuration (shred window, timeliness thresholds) lives in
    # GameSettings, NOT here — this row is for testing-only seeding. The legacy
    # shred_window_weeks/green_hours_threshold/brown_hours_threshold columns may
    # still exist on older databases but are no longer mapped or read.

    # Replay floors so future activity builds from this baseline.
    correctness_event_floor_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_floor_unix_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameSettings(Base):
    """Admin-configurable game parameters (singleton row id=1).

    Distinct from GameDebugSeed: these are real, persistent configuration values
    an administrator sets during setup — not testing-only seed data. They apply
    regardless of whether the debug seed is enabled. A blank install falls back to
    the config (env) defaults until a row is written.
    """

    __tablename__ = "game_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Display name Snappy greets (single-user app). None → generic greeting.
    user_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # A receipt is green when it reaches YNAB within `green_hours_threshold` hours
    # of its purchase date, brown when it takes longer than `brown_hours_threshold`,
    # yellow in between.
    green_hours_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=24.0)
    brown_hours_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=72.0)

    # Trailing weeks (including the current one) a validated receipt stays eligible
    # for shredding. 1 = current week only.
    shred_window_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameWeekFire(Base):
    """Per-week fire tracking for Game v3 week-scoped fire mechanics."""

    __tablename__ = "game_week_fires"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True, index=True)
    flames_active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    burnt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_flame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class GameIncident(Base):
    __tablename__ = "game_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ReceiptCorrection(Base):
    __tablename__ = "receipt_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(String(36), ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    ynab_transaction_id: Mapped[str | None] = mapped_column(String(64))

    synced_category_id: Mapped[str | None] = mapped_column(String(64))
    corrected_category_id: Mapped[str | None] = mapped_column(String(64))
    synced_splits_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    corrected_splits_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resynced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resync_penalty_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    receipt: Mapped[Receipt] = relationship(back_populates="corrections")


class CardAccountMapping(Base):
    __tablename__ = "card_account_mappings"
    __table_args__ = (UniqueConstraint("budget_id", "card_last_four", name="uq_card_account_mapping_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    budget_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    card_last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class PayeeCategoryMemory(Base):
    __tablename__ = "payee_category_memory"
    __table_args__ = (UniqueConstraint("budget_id", "payee_key", name="uq_payee_category_memory_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    budget_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payee_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class YNABReconciliationRun(Base):
    __tablename__ = "ynab_reconciliation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    scanned_receipts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_mistakes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_penalties: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
