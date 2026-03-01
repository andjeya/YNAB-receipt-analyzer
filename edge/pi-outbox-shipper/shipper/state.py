from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_backoff_seconds(attempt_number: int, initial_seconds: int, max_seconds: int) -> int:
    attempt = max(1, attempt_number)
    return min(initial_seconds * (2 ** (attempt - 1)), max_seconds)


@dataclass(frozen=True)
class DeliveryRecord:
    filename: str
    status: str
    attempts: int
    next_retry_at: datetime | None
    last_error: str | None
    last_attempt_at: datetime | None
    sent_at: datetime | None


class DeliveryStateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    filename TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    next_retry_at TEXT,
                    last_error TEXT,
                    last_attempt_at TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def ensure_record(self, filename: str, *, now: datetime | None = None) -> DeliveryRecord:
        now_utc = now or utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deliveries (filename, status, attempts, created_at)
                VALUES (?, 'pending', 0, ?)
                ON CONFLICT(filename) DO NOTHING
                """,
                (filename, isoformat_utc(now_utc)),
            )
            conn.commit()
        record = self.get_record(filename)
        assert record is not None
        return record

    def get_record(self, filename: str) -> DeliveryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT filename, status, attempts, next_retry_at, last_error, last_attempt_at, sent_at
                FROM deliveries
                WHERE filename = ?
                """,
                (filename,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryRecord(
            filename=row["filename"],
            status=row["status"],
            attempts=int(row["attempts"]),
            next_retry_at=parse_utc(row["next_retry_at"]),
            last_error=row["last_error"],
            last_attempt_at=parse_utc(row["last_attempt_at"]),
            sent_at=parse_utc(row["sent_at"]),
        )

    def mark_retry(
        self,
        filename: str,
        *,
        error_text: str,
        backoff_seconds: int,
        now: datetime | None = None,
    ) -> DeliveryRecord:
        now_utc = now or utc_now()
        current = self.ensure_record(filename, now=now_utc)
        attempts = current.attempts + 1
        next_retry = now_utc + timedelta(seconds=backoff_seconds)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE deliveries
                SET status = 'retry',
                    attempts = ?,
                    next_retry_at = ?,
                    last_error = ?,
                    last_attempt_at = ?,
                    sent_at = NULL
                WHERE filename = ?
                """,
                (
                    attempts,
                    isoformat_utc(next_retry),
                    error_text,
                    isoformat_utc(now_utc),
                    filename,
                ),
            )
            conn.commit()

        record = self.get_record(filename)
        assert record is not None
        return record

    def mark_sent(self, filename: str, *, now: datetime | None = None) -> DeliveryRecord:
        now_utc = now or utc_now()
        self.ensure_record(filename, now=now_utc)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE deliveries
                SET status = 'sent',
                    next_retry_at = NULL,
                    last_error = NULL,
                    last_attempt_at = ?,
                    sent_at = ?
                WHERE filename = ?
                """,
                (isoformat_utc(now_utc), isoformat_utc(now_utc), filename),
            )
            conn.commit()

        record = self.get_record(filename)
        assert record is not None
        return record

    def due_for_send(self, filename: str, *, now: datetime | None = None) -> bool:
        now_utc = now or utc_now()
        record = self.get_record(filename)
        if record is None:
            return True
        if record.status == "sent":
            return False
        if record.next_retry_at is None:
            return True
        return record.next_retry_at <= now_utc

    def all_records(self) -> list[DeliveryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT filename, status, attempts, next_retry_at, last_error, last_attempt_at, sent_at
                FROM deliveries
                ORDER BY filename ASC
                """
            ).fetchall()
        return [
            DeliveryRecord(
                filename=row["filename"],
                status=row["status"],
                attempts=int(row["attempts"]),
                next_retry_at=parse_utc(row["next_retry_at"]),
                last_error=row["last_error"],
                last_attempt_at=parse_utc(row["last_attempt_at"]),
                sent_at=parse_utc(row["sent_at"]),
            )
            for row in rows
        ]
