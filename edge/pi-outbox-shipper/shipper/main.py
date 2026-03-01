from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, ShipperConfig, load_config
from .logging import configure_logging, get_logger
from .sender import RsyncSender
from .state import DeliveryStateStore
from .watcher import InboxWatcher


class ShipperRuntime:
    def __init__(self, config: ShipperConfig):
        self.config = config
        self.logger = get_logger()
        self.state = DeliveryStateStore(config.paths.state_db)
        self.watcher = InboxWatcher(
            paths=config.paths,
            stability=config.stability,
            runtime=config.runtime,
            state=self.state,
        )
        self.sender = RsyncSender(
            sender_config=config.sender,
            retry_config=config.retry,
            state=self.state,
            outbox_dir=config.paths.outbox,
            sent_archive_dir=config.paths.sent_archive,
            post_send_action=config.runtime.post_send_action,
            logger=self.logger,
        )

    def cycle(self) -> tuple[int, int, int]:
        enqueued = self.watcher.enqueue_stable_files()
        for path in enqueued:
            self.logger.info("enqueued filename=%s", path.name)

        send_result = self.sender.send_available()
        return len(enqueued), send_result.sent, send_result.failed

    def status(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        inbox_counts = self.watcher.count_inbox_files()

        outbox_count = 0
        oldest_age_seconds = 0.0
        ready_retries = 0
        waiting_retries = 0

        if self.config.paths.outbox.exists():
            for path in self.config.paths.outbox.iterdir():
                if not path.is_file():
                    continue
                outbox_count += 1
                age = now.timestamp() - path.stat().st_mtime
                oldest_age_seconds = max(oldest_age_seconds, age)

                record = self.state.ensure_record(path.name)
                if record.status == "sent":
                    continue
                if self.state.due_for_send(path.name, now=now):
                    ready_retries += 1
                else:
                    waiting_retries += 1

        sent_archive_count = 0
        if self.config.paths.sent_archive.exists():
            sent_archive_count = sum(1 for p in self.config.paths.sent_archive.iterdir() if p.is_file())

        return {
            "inbox_counts": inbox_counts,
            "outbox_count": outbox_count,
            "sent_archive_count": sent_archive_count,
            "oldest_outbox_age_seconds": round(oldest_age_seconds, 2),
            "ready_to_send_count": ready_retries,
            "backoff_waiting_count": waiting_retries,
            "state_db": str(self.config.paths.state_db),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receipt outbox shipper for Raspberry Pi edge ingestion")
    parser.add_argument("--config", dest="config_path", default=None, help="Path to shipper YAML config")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override runtime log level",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Run continuously")
    subparsers.add_parser("once", help="Run one scan+send cycle")

    drain = subparsers.add_parser("drain", help="Run until outbox and inbox are empty")
    drain.add_argument(
        "--max-idle-cycles",
        type=int,
        default=2,
        help="Number of consecutive empty cycles before drain exits",
    )

    status = subparsers.add_parser("status", help="Show queue/status summary")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    return parser


def _load_runtime(config_path: str | None, log_level: str | None) -> ShipperRuntime:
    config = load_config(config_path)
    configure_logging(log_level or config.runtime.log_level)

    config.paths.outbox.mkdir(parents=True, exist_ok=True)
    config.paths.sent_archive.mkdir(parents=True, exist_ok=True)
    for inbox in config.paths.inboxes:
        inbox.path.mkdir(parents=True, exist_ok=True)

    return ShipperRuntime(config)


def _run_forever(runtime: ShipperRuntime) -> int:
    interval = runtime.config.runtime.poll_interval_seconds
    while True:
        enqueued, sent, failed = runtime.cycle()
        runtime.logger.debug("cycle complete enqueued=%s sent=%s failed=%s", enqueued, sent, failed)
        time.sleep(interval)


def _run_once(runtime: ShipperRuntime) -> int:
    enqueued, sent, failed = runtime.cycle()
    runtime.logger.info("once complete enqueued=%s sent=%s failed=%s", enqueued, sent, failed)
    return 0 if failed == 0 else 1


def _run_drain(runtime: ShipperRuntime, max_idle_cycles: int) -> int:
    interval = runtime.config.runtime.poll_interval_seconds
    idle_cycles = 0

    while True:
        enqueued, sent, failed = runtime.cycle()
        status = runtime.status()

        runtime.logger.info(
            "drain cycle enqueued=%s sent=%s failed=%s outbox=%s inbox_total=%s",
            enqueued,
            sent,
            failed,
            status["outbox_count"],
            sum(int(v) for v in status["inbox_counts"].values()),
        )

        if failed > 0:
            idle_cycles = 0
        elif status["outbox_count"] == 0 and sum(int(v) for v in status["inbox_counts"].values()) == 0:
            idle_cycles += 1
        else:
            idle_cycles = 0

        if idle_cycles >= max_idle_cycles:
            return 0

        time.sleep(interval)


def _print_status(runtime: ShipperRuntime, as_json: bool) -> int:
    status = runtime.status()
    if as_json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    print("Receipt Shipper Status")
    print(f"  outbox_count: {status['outbox_count']}")
    print(f"  sent_archive_count: {status['sent_archive_count']}")
    print(f"  oldest_outbox_age_seconds: {status['oldest_outbox_age_seconds']}")
    print(f"  ready_to_send_count: {status['ready_to_send_count']}")
    print(f"  backoff_waiting_count: {status['backoff_waiting_count']}")
    print(f"  state_db: {status['state_db']}")
    print("  inbox_counts:")
    for name, count in status["inbox_counts"].items():
        print(f"    - {name}: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        runtime = _load_runtime(args.config_path, args.log_level)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "run":
        return _run_forever(runtime)
    if args.command == "once":
        return _run_once(runtime)
    if args.command == "drain":
        return _run_drain(runtime, max_idle_cycles=max(1, int(args.max_idle_cycles)))
    if args.command == "status":
        return _print_status(runtime, as_json=bool(args.json))

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
