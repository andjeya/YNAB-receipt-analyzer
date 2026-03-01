from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from shipper.config import InboxConfig, PathsConfig, RuntimeConfig, StabilityConfig
from shipper.state import DeliveryStateStore
from shipper.watcher import InboxWatcher, build_outbox_name


def _runtime_paths(tmp_path: Path) -> tuple[PathsConfig, DeliveryStateStore]:
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


def test_stable_detection_respects_min_age_and_stability_window(tmp_path: Path) -> None:
    paths, state = _runtime_paths(tmp_path)
    watcher = InboxWatcher(
        paths=paths,
        stability=StabilityConfig(stable_seconds=2, min_age_seconds=2),
        runtime=RuntimeConfig(),
        state=state,
    )

    receipt = paths.inboxes[0].path / "scan.pdf"
    receipt.write_bytes(b"sample")

    now = datetime.now(timezone.utc)
    receipt.touch()
    # First pass should reject file due to min age.
    assert watcher.scan_stable_files(now=now) == []

    # After enough time has passed with no changes, file becomes stable.
    later = now + timedelta(seconds=4)
    assert watcher.scan_stable_files(now=later) == [("scanner", receipt)]

    # If file changes, stability window should reset.
    receipt.write_bytes(b"sample-updated")
    reset_time = later + timedelta(seconds=1)
    assert watcher.scan_stable_files(now=reset_time) == []



def test_build_outbox_name_includes_metadata() -> None:
    now = datetime(2026, 3, 1, 22, 50, 0, tzinfo=timezone.utc)
    name = build_outbox_name(
        source="inbox_scanner",
        original_name="My Receipt 03-01.JPG",
        user_tag="alice",
        now=now,
        random_suffix="abcd1234",
    )

    assert name == "20260301T225000Z-inbox-scanner-alice-my-receipt-03-01-abcd1234.jpg"
