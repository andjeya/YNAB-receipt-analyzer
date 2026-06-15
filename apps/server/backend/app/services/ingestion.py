from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt
from app.services.retention import hard_delete_receipt
from app.services.storage import build_storage_key, guess_mime_type, move_to_storage, sanitize_extension, storage_path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"}


@dataclass
class ScanResult:
    ingested_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PendingFile:
    last_size: int
    last_size_change_ts: float
    stable_passes: int = 0


class IngestionScanner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pending: dict[str, PendingFile] = {}

    def scan_once(self, db: Session) -> ScanResult:
        result = ScanResult()
        ingest_dir = self.settings.ingest_dir
        ingest_dir.mkdir(parents=True, exist_ok=True)

        visible_files = [path for path in ingest_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS]
        visible_keys = {str(path) for path in visible_files}

        for key in list(self._pending.keys()):
            if key not in visible_keys:
                self._pending.pop(key, None)

        now = time.time()
        for path in sorted(visible_files):
            key = str(path)
            size = path.stat().st_size
            pending = self._pending.get(key)
            if pending is None:
                self._pending[key] = PendingFile(last_size=size, last_size_change_ts=now)
                result.skipped_count += 1
                continue

            if size != pending.last_size:
                pending.last_size = size
                pending.last_size_change_ts = now
                pending.stable_passes = 0
                result.skipped_count += 1
                continue

            stable_age = now - pending.last_size_change_ts
            if stable_age < self.settings.stable_min_age_seconds:
                result.skipped_count += 1
                continue

            pending.stable_passes += 1
            if pending.stable_passes < self.settings.stable_checks_required:
                result.skipped_count += 1
                continue

            try:
                _, was_ingested = ingest_file(path, db, self.settings)
                if was_ingested:
                    result.ingested_count += 1
                else:
                    result.duplicate_count += 1
            except Exception as exc:  # pragma: no cover - safety for background scanner
                message = f"Failed ingest for {path.name}: {exc}"
                logger.exception(message)
                result.error_count += 1
                result.errors.append(message)
            finally:
                self._pending.pop(key, None)

        return result


def compute_file_hash(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_file(source_path: Path, db: Session, settings: Settings) -> tuple[Receipt, bool]:
    file_size_check = source_path.stat().st_size
    if file_size_check > settings.max_ingest_file_size_bytes:
        raise ValueError(
            f"File {source_path.name!r} exceeds maximum ingest size of {settings.max_ingest_file_size_bytes} bytes "
            f"(got {file_size_check} bytes)"
        )

    file_hash = compute_file_hash(source_path)

    existing = db.scalar(select(Receipt).where(Receipt.file_hash == file_hash))
    if existing is not None and existing.deleted_at is None:
        # A live receipt already holds this content — genuine duplicate scan.
        source_path.unlink(missing_ok=True)
        return existing, False
    if existing is not None:
        # The only match is a receipt the user already deleted. The soft-delete
        # is just an Undo buffer; reviving the old row would resurrect its stale
        # state/notes, and the unique file_hash blocks a fresh row. Hard-delete
        # the old one now and ingest this scan as a clean, new receipt.
        hard_delete_receipt(db, settings, existing)
        db.flush()

    receipt_id = str(uuid.uuid4())
    extension = sanitize_extension(source_path.name)
    mime_type = guess_mime_type(source_path.name)
    storage_key = build_storage_key(receipt_id, extension, settings.object_store_receipts_prefix)
    destination = storage_path(settings.object_store_root, storage_key)

    file_size = source_path.stat().st_size
    move_to_storage(source_path, destination)

    receipt = Receipt(
        id=receipt_id,
        storage_key=storage_key,
        original_filename=source_path.name,
        file_hash=file_hash,
        file_ext=extension,
        mime_type=mime_type,
        file_size_bytes=file_size,
        status=ReceiptStatus.INGESTED.value,
    )
    db.add(receipt)
    db.commit()
    db.refresh(receipt)

    from app.jobs.queue import enqueue_extraction_job

    try:
        enqueue_extraction_job(receipt.id)
    except Exception:
        # The row is already committed at INGESTED. Don't fail the whole scan over
        # a transient queue error — the orphaned-ingested recovery sweep will
        # re-enqueue it shortly. See app/services/recovery.py.
        logger.exception("Failed to enqueue extraction for receipt %s; left for recovery sweep", receipt.id)
    return receipt, True
