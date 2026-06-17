"""Edge shipper delivery-integrity edge cases (plan T1-12..T1-15).

Protects the "one physical receipt -> at most one delivered file" guarantee at
the Pi edge:

  T1-12  Atomic move across filesystems (EXDEV) fails loudly; the inbox file is
         left in place and nothing is recorded as delivered.
  T1-13  Outbox name collision is resolved with a unique suffix; no overwrite.
  T1-14  When the remote final file already exists, the sender does NOT rsync
         over it (idempotent re-delivery).
  T1-15  rsync_dry_run=true puts --dry-run on the rsync command and skips the
         remote promote, so a dry run can never become a real transfer.

All remote interaction is faked via a recording command runner — no SSH/rsync.
"""

from __future__ import annotations

import errno
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shipper.config import (
    InboxConfig,
    PathsConfig,
    RetryConfig,
    RuntimeConfig,
    SenderConfig,
    StabilityConfig,
)
from shipper.sender import RsyncSender
from shipper.state import DeliveryStateStore
from shipper.watcher import InboxWatcher

NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _paths(tmp_path: Path) -> tuple[PathsConfig, DeliveryStateStore]:
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    sent = tmp_path / "sent"
    state_db = tmp_path / "state" / "shipper.db"
    inbox.mkdir(parents=True)
    outbox.mkdir(parents=True)
    sent.mkdir(parents=True)
    paths = PathsConfig(
        inboxes=(InboxConfig(name="scanner", path=inbox),),
        outbox=outbox,
        sent_archive=sent,
        state_db=state_db,
    )
    return paths, DeliveryStateStore(state_db)


def _watcher(paths: PathsConfig, state: DeliveryStateStore) -> InboxWatcher:
    # min_age/stable both 0 → a freshly-written file is immediately stable.
    return InboxWatcher(
        paths=paths,
        stability=StabilityConfig(stable_seconds=0, min_age_seconds=0),
        runtime=RuntimeConfig(),
        state=state,
    )


# ---------------------------------------------------------------------------
# T1-12: EXDEV atomic move failure
# ---------------------------------------------------------------------------


def test_exdev_move_failure_leaves_inbox_file_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, state = _paths(tmp_path)
    watcher = _watcher(paths, state)

    source = paths.inboxes[0].path / "scan.pdf"
    source.write_bytes(b"receipt-bytes")

    def _raise_exdev(src: str, dst: str) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("shipper.watcher.os.replace", _raise_exdev)

    # Use real wall-clock so the freshly-written file reads as stable (min_age/stable=0).
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match="cross-device|same filesystem|Atomic move"):
        watcher.enqueue_stable_files(now=now)

    # The inbox file is untouched; nothing leaked into the outbox.
    assert source.exists()
    assert list(paths.outbox.iterdir()) == []


# ---------------------------------------------------------------------------
# T1-13: outbox name collision → unique suffix
# ---------------------------------------------------------------------------


def test_outbox_name_collision_gets_unique_suffix(tmp_path: Path) -> None:
    paths, state = _paths(tmp_path)
    watcher = _watcher(paths, state)

    existing = paths.outbox / "20260616T120000Z-scanner-receipt-abcd1234.jpg"
    existing.write_bytes(b"already here")

    candidate = watcher._unique_outbox_path(existing.name)

    assert candidate != existing
    assert not candidate.exists()
    assert candidate.suffix == ".jpg"  # extension preserved
    assert candidate.name.startswith("20260616T120000Z-scanner-receipt-abcd1234-")


# ---------------------------------------------------------------------------
# Sender helpers
# ---------------------------------------------------------------------------


class _RecordingRunner:
    """Fake command runner that records commands and returns a chosen returncode.

    `remote_file_exists` controls the result of the remote `test -f` probe.
    """

    def __init__(self, *, remote_file_exists: bool) -> None:
        self.commands: list[list[str]] = []
        self._remote_file_exists = remote_file_exists

    def __call__(self, command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        is_existence_probe = any("test -f" in part for part in command)
        returncode = 0 if (not is_existence_probe or self._remote_file_exists) else 1
        return subprocess.CompletedProcess(args=command, returncode=returncode, stdout="", stderr="")

    def rsync_commands(self) -> list[list[str]]:
        return [c for c in self.commands if c and c[0] == "rsync"]


def _sender(
    tmp_path: Path,
    state: DeliveryStateStore,
    runner: _RecordingRunner,
    *,
    rsync_dry_run: bool = False,
) -> RsyncSender:
    return RsyncSender(
        sender_config=SenderConfig(host="nas", user="pi", rsync_dry_run=rsync_dry_run),
        retry_config=RetryConfig(),
        state=state,
        outbox_dir=tmp_path / "outbox",
        sent_archive_dir=tmp_path / "sent",
        post_send_action="archive",
        logger=logging.getLogger("test-shipper"),
        command_runner=runner,
    )


# ---------------------------------------------------------------------------
# T1-14: remote final file already exists → no rsync overwrite
# ---------------------------------------------------------------------------


def test_remote_file_exists_skips_rsync_and_marks_sent(tmp_path: Path) -> None:
    paths, state = _paths(tmp_path)
    outbox_file = paths.outbox / "deliverme.jpg"
    outbox_file.write_bytes(b"receipt")

    runner = _RecordingRunner(remote_file_exists=True)
    sender = _sender(tmp_path, state, runner)

    summary = sender.send_available(now=NOW)

    assert summary.sent == 1
    # No rsync transfer happened — the remote already has this file.
    assert runner.rsync_commands() == []
    assert state.ensure_record("deliverme.jpg", now=NOW).status == "sent"


# ---------------------------------------------------------------------------
# T1-15: rsync_dry_run puts --dry-run on the rsync command, skips promote
# ---------------------------------------------------------------------------


def test_rsync_dry_run_adds_flag_and_skips_promote(tmp_path: Path) -> None:
    paths, state = _paths(tmp_path)
    outbox_file = paths.outbox / "dryrun.jpg"
    outbox_file.write_bytes(b"receipt")

    runner = _RecordingRunner(remote_file_exists=False)
    sender = _sender(tmp_path, state, runner, rsync_dry_run=True)

    sender.send_available(now=NOW)

    rsync_cmds = runner.rsync_commands()
    assert len(rsync_cmds) == 1
    assert "--dry-run" in rsync_cmds[0]

    # Promote (an ssh `mv` of the staged temp to final) must NOT run during a dry run.
    assert not any(any("mv " in part for part in cmd) for cmd in runner.commands)
