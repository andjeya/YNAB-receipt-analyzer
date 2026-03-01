from __future__ import annotations

import errno
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import PathsConfig, RuntimeConfig, StabilityConfig
from .state import DeliveryStateStore

DROPBOX_ATTR_SUFFIX = ":com.dropbox.attrs"


@dataclass
class _SeenFile:
    size: int
    mtime_ns: int
    last_change_at: datetime


def _slugify(value: str, *, fallback: str = "file") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def build_outbox_name(
    *,
    source: str,
    original_name: str,
    user_tag: str = "",
    now: datetime | None = None,
    random_suffix: str | None = None,
) -> str:
    dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = dt.strftime("%Y%m%dT%H%M%SZ")

    original_path = Path(original_name)
    ext = original_path.suffix.lower()
    stem = _slugify(original_path.stem, fallback="receipt")
    source_slug = _slugify(source, fallback="source")
    user_slug = _slugify(user_tag, fallback="") if user_tag else ""
    suffix = random_suffix or uuid.uuid4().hex[:8]

    pieces = [timestamp, source_slug]
    if user_slug:
        pieces.append(user_slug)
    pieces.extend([stem, suffix])
    result = "-".join(pieces) + ext
    # POSIX filesystem limit is 255 bytes; truncate stem if the assembled name exceeds it.
    result_bytes = result.encode("utf-8")
    if len(result_bytes) > 255:
        fixed = "-".join(pieces[:-2]) + "-" + suffix + ext
        available = 255 - len(fixed.encode("utf-8")) - 1  # -1 for hyphen before stem
        stem = stem.encode("utf-8")[:max(1, available)].decode("utf-8", errors="ignore")
        pieces[-2] = stem
        result = "-".join(pieces) + ext
    return result


class InboxWatcher:
    def __init__(
        self,
        *,
        paths: PathsConfig,
        stability: StabilityConfig,
        runtime: RuntimeConfig,
        state: DeliveryStateStore,
    ):
        self.paths = paths
        self.stability = stability
        self.runtime = runtime
        self.state = state
        self._seen: dict[Path, _SeenFile] = {}
        self._logger = logging.getLogger(__name__)

    def _is_ignored(self, path: Path) -> bool:
        name = path.name
        lowered = name.lower()
        if self.stability.ignore_hidden and name.startswith("."):
            return True
        if lowered.endswith(DROPBOX_ATTR_SUFFIX):
            return True
        if lowered.endswith("~"):
            return True
        return any(lowered.endswith(suffix) for suffix in self.stability.ignore_suffixes)

    def _is_stable(self, path: Path, *, now: datetime) -> bool:
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._seen.pop(path, None)
            self._logger.debug("file disappeared between scans path=%s", path)
            return False

        age_seconds = (now - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)).total_seconds()
        if age_seconds < self.stability.min_age_seconds:
            self._seen[path] = _SeenFile(
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                last_change_at=now,
            )
            return False

        observed = self._seen.get(path)
        if observed is None:
            last_change_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            observed = _SeenFile(size=stat.st_size, mtime_ns=stat.st_mtime_ns, last_change_at=last_change_at)
            self._seen[path] = observed
        elif observed.size != stat.st_size or observed.mtime_ns != stat.st_mtime_ns:
            observed = _SeenFile(size=stat.st_size, mtime_ns=stat.st_mtime_ns, last_change_at=now)
            self._seen[path] = observed

        stable_for = (now - observed.last_change_at).total_seconds()
        return stable_for >= self.stability.stable_seconds

    def scan_stable_files(self, *, now: datetime | None = None) -> list[tuple[str, Path]]:
        now_utc = now or datetime.now(timezone.utc)
        candidates: list[tuple[str, Path]] = []
        live_paths: set[Path] = set()

        for inbox in self.paths.inboxes:
            if not inbox.path.exists():
                continue
            for path in sorted(inbox.path.iterdir()):
                if not path.is_file():
                    continue
                if self._is_ignored(path):
                    continue
                live_paths.add(path)
                if self._is_stable(path, now=now_utc):
                    candidates.append((inbox.name, path))

        stale = [path for path in self._seen if path not in live_paths]
        for path in stale:
            self._seen.pop(path, None)

        return candidates

    def enqueue_stable_files(self, *, now: datetime | None = None) -> list[Path]:
        now_utc = now or datetime.now(timezone.utc)
        self.paths.outbox.mkdir(parents=True, exist_ok=True)
        enqueued: list[Path] = []

        for source_name, source_path in self.scan_stable_files(now=now_utc):
            target_name = build_outbox_name(
                source=source_name,
                original_name=source_path.name,
                user_tag=self.runtime.user_tag,
                now=now_utc,
            )
            target_path = self._unique_outbox_path(target_name)

            try:
                os.replace(source_path, target_path)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise RuntimeError(
                        f"Atomic move failed across filesystems for '{source_path}'. Keep inbox and outbox on same filesystem."
                    ) from exc
                if exc.errno == errno.ENOENT:
                    continue
                raise

            self.state.ensure_record(target_path.name, now=now_utc)
            self._seen.pop(source_path, None)
            enqueued.append(target_path)

        return enqueued

    def _unique_outbox_path(self, name: str) -> Path:
        candidate = self.paths.outbox / name
        while candidate.exists():
            stem = candidate.stem
            suffix = candidate.suffix
            candidate = self.paths.outbox / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"
        return candidate

    def count_inbox_files(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for inbox in self.paths.inboxes:
            if not inbox.path.exists():
                counts[inbox.name] = 0
                continue
            count = 0
            for path in inbox.path.iterdir():
                if not path.is_file():
                    continue
                if self._is_ignored(path):
                    continue
                count += 1
            counts[inbox.name] = count
        return counts
