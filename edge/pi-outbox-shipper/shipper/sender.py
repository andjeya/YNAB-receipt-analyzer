from __future__ import annotations

import logging
import posixpath
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import RetryConfig, SenderConfig
from .state import DeliveryStateStore, compute_backoff_seconds

CommandRunner = Callable[[list[str], bool], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SendSummary:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


class RsyncSender:
    def __init__(
        self,
        *,
        sender_config: SenderConfig,
        retry_config: RetryConfig,
        state: DeliveryStateStore,
        outbox_dir: Path,
        sent_archive_dir: Path,
        post_send_action: str,
        logger: logging.Logger,
        command_runner: CommandRunner | None = None,
    ):
        self.sender_config = sender_config
        self.retry_config = retry_config
        self.state = state
        self.outbox_dir = outbox_dir
        self.sent_archive_dir = sent_archive_dir
        self.post_send_action = post_send_action
        self.logger = logger
        self.command_runner = command_runner or self._run_command

    def send_available(self, *, now: datetime | None = None) -> SendSummary:
        now_utc = now or datetime.now(timezone.utc)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

        sent = 0
        failed = 0
        skipped = 0

        for path in sorted(self.outbox_dir.iterdir()):
            if not path.is_file():
                continue

            record = self.state.ensure_record(path.name, now=now_utc)
            if record.status == "sent":
                self._cleanup_local(path)
                skipped += 1
                continue

            if not self.state.due_for_send(path.name, now=now_utc):
                skipped += 1
                continue

            if self._send_one(path, now=now_utc):
                sent += 1
            else:
                failed += 1

        return SendSummary(sent=sent, failed=failed, skipped=skipped)

    def _send_one(self, local_path: Path, *, now: datetime) -> bool:
        remote_final = self._remote_final_path(local_path.name)

        try:
            if self.sender_config.dry_run:
                self.logger.info("dry-run send filename=%s", local_path.name)
                self.state.mark_sent(local_path.name, now=now)
                self._cleanup_local(local_path)
                return True

            self._ensure_remote_dirs()
            if self._remote_file_exists(remote_final):
                self.logger.info("remote file already exists filename=%s", local_path.name)
                self.state.mark_sent(local_path.name, now=now)
                self._cleanup_local(local_path)
                return True

            remote_temp = self._remote_temp_path(local_path.name)
            self._rsync_to_remote(local_path, remote_temp)

            if self.sender_config.rsync_dry_run:
                self.logger.info("rsync dry-run complete filename=%s", local_path.name)
            else:
                self._promote_remote(remote_temp, remote_final)

            self.state.mark_sent(local_path.name, now=now)
            self._cleanup_local(local_path)
            self.logger.info("sent filename=%s", local_path.name)
            return True
        except Exception as exc:  # noqa: BLE001
            current = self.state.ensure_record(local_path.name, now=now)
            next_attempt = current.attempts + 1
            backoff = compute_backoff_seconds(
                next_attempt,
                self.retry_config.initial_backoff_seconds,
                self.retry_config.max_backoff_seconds,
            )
            self.state.mark_retry(
                local_path.name,
                error_text=str(exc),
                backoff_seconds=backoff,
                now=now,
            )
            self.logger.warning(
                "send failed filename=%s retry_in_seconds=%s error=%s",
                local_path.name,
                backoff,
                exc,
            )
            return False

    def _cleanup_local(self, local_path: Path) -> None:
        if not local_path.exists():
            return

        if self.post_send_action == "delete":
            local_path.unlink()
            return

        self.sent_archive_dir.mkdir(parents=True, exist_ok=True)
        destination = self.sent_archive_dir / local_path.name
        while destination.exists():
            destination = self.sent_archive_dir / f"{destination.stem}-{uuid.uuid4().hex[:6]}{destination.suffix}"
        local_path.replace(destination)

    def _remote_temp_path(self, filename: str) -> str:
        token = uuid.uuid4().hex[:8]
        return posixpath.join(self.sender_config.staging_dir, f"{filename}.part-{token}")

    def _remote_final_path(self, filename: str) -> str:
        return posixpath.join(self.sender_config.incoming_dir, filename)

    def _ensure_remote_dirs(self) -> None:
        cmd = (
            f"mkdir -p {shlex.quote(self.sender_config.incoming_dir)} "
            f"{shlex.quote(self.sender_config.staging_dir)}"
        )
        self._run_ssh(cmd)

    def _remote_file_exists(self, remote_path: str) -> bool:
        result = self._run_ssh(f"test -f {shlex.quote(remote_path)}", check=False)
        return result.returncode == 0

    def _promote_remote(self, remote_temp: str, remote_final: str) -> None:
        cmd = (
            f"if [ -f {shlex.quote(remote_final)} ]; then "
            f"rm -f {shlex.quote(remote_temp)}; "
            "else "
            f"mv {shlex.quote(remote_temp)} {shlex.quote(remote_final)}; "
            "fi"
        )
        self._run_ssh(cmd)

    def _ssh_parts(self) -> list[str]:
        parts = [
            self.sender_config.ssh_binary,
            "-p",
            str(self.sender_config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.sender_config.connect_timeout_seconds}",
        ]
        if self.sender_config.ssh_key:
            parts.extend(["-i", str(self.sender_config.ssh_key)])
        return parts

    def _run_ssh(self, remote_command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        target = f"{self.sender_config.user}@{self.sender_config.host}"
        command = [*self._ssh_parts(), target, remote_command]
        return self.command_runner(command, check)

    def _rsync_to_remote(self, local_path: Path, remote_temp: str) -> None:
        target = f"{self.sender_config.user}@{self.sender_config.host}:{shlex.quote(remote_temp)}"
        ssh_transport = " ".join(shlex.quote(part) for part in self._ssh_parts())

        command = [
            self.sender_config.rsync_binary,
            "--archive",
            "--compress",
            "--partial",
            "--times",
            "--no-owner",
            "--no-group",
            "--chmod=F644",
            "--protect-args",
            "-e",
            ssh_transport,
        ]
        if self.sender_config.rsync_dry_run:
            command.append("--dry-run")
        command.extend([str(local_path), target])
        self.command_runner(command, True)

    def _run_command(self, command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            text = stderr or stdout or "unknown error"
            raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)} :: {text}")
        return result
