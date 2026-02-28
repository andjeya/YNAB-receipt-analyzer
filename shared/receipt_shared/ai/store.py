from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    and_,
    create_engine,
    distinct,
    func,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from .limits import LimitsConfig, WindowLimit
from .types import LedgerEvent, LimitViolation, ReservationResult, TokenUsage, UsageWindowTotals
from .windows import WINDOWS, WindowName, window_start

COUNTABLE_STATUSES: tuple[str, ...] = ("pending", "success")
SUCCESS_STATUSES: tuple[str, ...] = ("success",)


class UsageLedgerStore:
    def __init__(self, database_url: str):
        engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if database_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}

        self._engine: Engine = create_engine(database_url, **engine_kwargs)
        self._is_sqlite = self._engine.dialect.name == "sqlite"

        metadata = MetaData()
        self._ledger = Table(
            "ai_usage_ledger",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("timestamp_utc", DateTime(timezone=True), nullable=False, index=True),
            Column("provider", String(64), nullable=False, index=True),
            Column("model_id", String(128), nullable=False, index=True),
            Column("request_id", String(128), nullable=False, index=True),
            Column("correlation_id", String(128), nullable=True, index=True),
            Column("route", String(128), nullable=True, index=True),
            Column("input_tokens", Integer, nullable=True),
            Column("output_tokens", Integer, nullable=True),
            Column("cached_input_tokens", Integer, nullable=True),
            Column("total_tokens", Integer, nullable=True),
            Column("extra_usage_json", Text, nullable=True),
            Column("cost_usd", Numeric(20, 8), nullable=False, default=Decimal("0")),
            Column("status", String(32), nullable=False, index=True),
            Column("pricing_version", String(64), nullable=True),
            Column("metadata_json", Text, nullable=True),
            Column("error_text", Text, nullable=True),
            Column("created_at", DateTime(timezone=True), nullable=False, index=True),
        )
        metadata.create_all(self._engine, checkfirst=True)

    @staticmethod
    def _as_utc(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    @staticmethod
    def _json_dumps(payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _json_loads(payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            loaded = json.loads(payload)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _run_locked(self, operation: Callable[[Connection], Any]) -> Any:
        if self._is_sqlite:
            with self._engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    result = operation(conn)
                    conn.exec_driver_sql("COMMIT")
                    return result
                except Exception:
                    conn.exec_driver_sql("ROLLBACK")
                    raise

        with self._engine.begin() as conn:
            return operation(conn)

    def _insert_event(
        self,
        conn: Connection,
        *,
        timestamp_utc: datetime,
        provider: str,
        model_id: str,
        request_id: str,
        correlation_id: str | None,
        route: str | None,
        usage: TokenUsage,
        cost_usd: Decimal,
        status: str,
        pricing_version: str | None,
        metadata: dict[str, Any] | None,
        error_text: str | None = None,
    ) -> int:
        usage_full = usage.with_total_if_missing()
        result = conn.execute(
            self._ledger.insert().values(
                timestamp_utc=self._as_utc(timestamp_utc),
                provider=provider,
                model_id=model_id,
                request_id=request_id,
                correlation_id=correlation_id,
                route=route,
                input_tokens=usage_full.input_tokens,
                output_tokens=usage_full.output_tokens,
                cached_input_tokens=usage_full.cached_input_tokens,
                total_tokens=usage_full.total_tokens,
                extra_usage_json=self._json_dumps(usage_full.extra_dimensions),
                cost_usd=Decimal(str(cost_usd)),
                status=status,
                pricing_version=pricing_version,
                metadata_json=self._json_dumps(metadata),
                error_text=error_text,
                created_at=self._as_utc(datetime.now(timezone.utc)),
            )
        )
        pk = result.inserted_primary_key
        if not pk:
            raise RuntimeError("Failed to insert ai_usage_ledger event")
        return int(pk[0])

    def _sum_usage(
        self,
        conn: Connection,
        *,
        window_start_utc: datetime,
        statuses: Sequence[str],
        model_id: str | None = None,
    ) -> UsageWindowTotals:
        stmt = select(
            func.coalesce(func.sum(self._ledger.c.total_tokens), 0),
            func.coalesce(func.sum(self._ledger.c.cost_usd), 0),
        ).where(
            and_(
                self._ledger.c.timestamp_utc >= self._as_utc(window_start_utc),
                self._ledger.c.status.in_(tuple(statuses)),
            )
        )
        if model_id is not None:
            stmt = stmt.where(self._ledger.c.model_id == model_id)

        row = conn.execute(stmt).one()
        tokens = int(row[0] or 0)
        usd = Decimal(str(row[1] or 0)).quantize(Decimal("0.00000001"))
        return UsageWindowTotals(tokens=tokens, usd=usd)

    def _check_window_limit(
        self,
        *,
        limit: WindowLimit,
        scope: str,
        model_id: str | None,
        window: WindowName,
        current: UsageWindowTotals,
        incoming_usage: TokenUsage,
        incoming_cost_usd: Decimal,
    ) -> list[LimitViolation]:
        if limit.unlimited:
            return []

        violations: list[LimitViolation] = []
        incoming_tokens = incoming_usage.limit_tokens

        if limit.tokens is not None:
            projected_tokens = current.tokens + incoming_tokens
            if projected_tokens > limit.tokens:
                violations.append(
                    LimitViolation(
                        scope="model" if scope == "model" else "global",
                        model_id=model_id,
                        window=window,
                        dimension="tokens",
                        limit=limit.tokens,
                        current=current.tokens,
                        projected=projected_tokens,
                    )
                )

        if limit.usd is not None:
            projected_usd = (current.usd + incoming_cost_usd).quantize(Decimal("0.00000001"))
            if projected_usd > limit.usd:
                violations.append(
                    LimitViolation(
                        scope="model" if scope == "model" else "global",
                        model_id=model_id,
                        window=window,
                        dimension="usd",
                        limit=limit.usd,
                        current=current.usd,
                        projected=projected_usd,
                    )
                )
        return violations

    def reserve(
        self,
        *,
        timestamp_utc: datetime,
        provider: str,
        model_id: str,
        request_id: str,
        correlation_id: str | None,
        route: str | None,
        estimated_usage: TokenUsage,
        estimated_cost_usd: Decimal,
        limits: LimitsConfig,
        pricing_version: str | None,
        metadata: dict[str, Any] | None,
    ) -> ReservationResult:
        ts_utc = self._as_utc(timestamp_utc)

        def _op(conn: Connection) -> ReservationResult:
            violations: list[LimitViolation] = []
            for window in WINDOWS:
                start = window_start(ts_utc, window)

                global_current = self._sum_usage(
                    conn,
                    window_start_utc=start,
                    statuses=COUNTABLE_STATUSES,
                    model_id=None,
                )
                model_current = self._sum_usage(
                    conn,
                    window_start_utc=start,
                    statuses=COUNTABLE_STATUSES,
                    model_id=model_id,
                )

                violations.extend(
                    self._check_window_limit(
                        limit=limits.get_global(window),
                        scope="global",
                        model_id=None,
                        window=window,
                        current=global_current,
                        incoming_usage=estimated_usage,
                        incoming_cost_usd=estimated_cost_usd,
                    )
                )
                violations.extend(
                    self._check_window_limit(
                        limit=limits.get_model(model_id, window),
                        scope="model",
                        model_id=model_id,
                        window=window,
                        current=model_current,
                        incoming_usage=estimated_usage,
                        incoming_cost_usd=estimated_cost_usd,
                    )
                )

            if violations:
                event_id = self._insert_event(
                    conn,
                    timestamp_utc=ts_utc,
                    provider=provider,
                    model_id=model_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                    route=route,
                    usage=TokenUsage(total_tokens=0),
                    cost_usd=Decimal("0"),
                    status="rejected_by_limit",
                    pricing_version=pricing_version,
                    metadata={
                        "violations": [
                            {
                                "scope": v.scope,
                                "window": v.window,
                                "dimension": v.dimension,
                                "limit": str(v.limit),
                                "current": str(v.current),
                                "projected": str(v.projected),
                            }
                            for v in violations
                        ],
                        "requested_estimated_tokens": estimated_usage.limit_tokens,
                        "requested_estimated_cost_usd": str(estimated_cost_usd),
                        **(metadata or {}),
                    },
                )
                return ReservationResult(allowed=False, event_id=event_id, violations=violations)

            event_id = self._insert_event(
                conn,
                timestamp_utc=ts_utc,
                provider=provider,
                model_id=model_id,
                request_id=request_id,
                correlation_id=correlation_id,
                route=route,
                usage=estimated_usage,
                cost_usd=estimated_cost_usd,
                status="pending",
                pricing_version=pricing_version,
                metadata=metadata,
            )
            return ReservationResult(allowed=True, event_id=event_id, violations=[])

        return self._run_locked(_op)

    def finalize(
        self,
        *,
        event_id: int,
        status: str,
        usage: TokenUsage,
        cost_usd: Decimal,
        error_text: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        usage_full = usage.with_total_if_missing()

        def _op(conn: Connection) -> None:
            existing = conn.execute(
                select(self._ledger.c.metadata_json).where(self._ledger.c.id == event_id)
            ).first()
            if existing is None:
                raise RuntimeError(f"Usage ledger event not found: {event_id}")

            existing_metadata = self._json_loads(existing[0])
            if metadata_updates:
                existing_metadata.update(metadata_updates)

            conn.execute(
                update(self._ledger)
                .where(self._ledger.c.id == event_id)
                .values(
                    status=status,
                    input_tokens=usage_full.input_tokens,
                    output_tokens=usage_full.output_tokens,
                    cached_input_tokens=usage_full.cached_input_tokens,
                    total_tokens=usage_full.total_tokens,
                    extra_usage_json=self._json_dumps(usage_full.extra_dimensions),
                    cost_usd=Decimal(str(cost_usd)),
                    error_text=error_text,
                    metadata_json=self._json_dumps(existing_metadata),
                )
            )

        self._run_locked(_op)

    def window_totals(
        self,
        *,
        now_utc: datetime,
        statuses: Sequence[str] = SUCCESS_STATUSES,
        model_id: str | None = None,
    ) -> dict[WindowName, UsageWindowTotals]:
        results: dict[WindowName, UsageWindowTotals] = {}
        with self._engine.connect() as conn:
            for window in WINDOWS:
                start = window_start(now_utc, window)
                results[window] = self._sum_usage(
                    conn,
                    window_start_utc=start,
                    statuses=statuses,
                    model_id=model_id,
                )
        return results

    def list_models(self, *, statuses: Sequence[str] = SUCCESS_STATUSES) -> list[str]:
        stmt = (
            select(distinct(self._ledger.c.model_id))
            .where(self._ledger.c.status.in_(tuple(statuses)))
            .order_by(self._ledger.c.model_id.asc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [str(row[0]) for row in rows if row[0] is not None]

    def fetch_events(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
        statuses: Sequence[str] = SUCCESS_STATUSES,
        model_id: str | None = None,
    ) -> list[LedgerEvent]:
        stmt = select(
            self._ledger.c.id,
            self._ledger.c.timestamp_utc,
            self._ledger.c.provider,
            self._ledger.c.model_id,
            self._ledger.c.request_id,
            self._ledger.c.correlation_id,
            self._ledger.c.route,
            self._ledger.c.input_tokens,
            self._ledger.c.output_tokens,
            self._ledger.c.cached_input_tokens,
            self._ledger.c.total_tokens,
            self._ledger.c.cost_usd,
            self._ledger.c.status,
            self._ledger.c.pricing_version,
            self._ledger.c.metadata_json,
            self._ledger.c.error_text,
        ).where(
            and_(
                self._ledger.c.timestamp_utc >= self._as_utc(start_utc),
                self._ledger.c.timestamp_utc < self._as_utc(end_utc),
                self._ledger.c.status.in_(tuple(statuses)),
            )
        )
        if model_id is not None:
            stmt = stmt.where(self._ledger.c.model_id == model_id)
        stmt = stmt.order_by(self._ledger.c.timestamp_utc.asc(), self._ledger.c.id.asc())

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()

        events: list[LedgerEvent] = []
        for row in rows:
            events.append(
                LedgerEvent(
                    id=int(row.id),
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    provider=str(row.provider),
                    model_id=str(row.model_id),
                    request_id=str(row.request_id),
                    correlation_id=str(row.correlation_id) if row.correlation_id is not None else None,
                    route=str(row.route) if row.route is not None else None,
                    input_tokens=int(row.input_tokens) if row.input_tokens is not None else None,
                    output_tokens=int(row.output_tokens) if row.output_tokens is not None else None,
                    cached_input_tokens=int(row.cached_input_tokens) if row.cached_input_tokens is not None else None,
                    total_tokens=int(row.total_tokens) if row.total_tokens is not None else None,
                    cost_usd=Decimal(str(row.cost_usd or 0)).quantize(Decimal("0.00000001")),
                    status=str(row.status),
                    pricing_version=str(row.pricing_version) if row.pricing_version is not None else None,
                    metadata=self._json_loads(row.metadata_json),
                    error_text=str(row.error_text) if row.error_text is not None else None,
                )
            )
        return events
