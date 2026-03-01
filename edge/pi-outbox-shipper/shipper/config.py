from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_TEMP_SUFFIXES = (
    ".tmp",
    ".part",
    ".partial",
    ".crdownload",
    ".download",
    ".swp",
    ".swx",
)


class ConfigError(ValueError):
    """Raised when shipper config is invalid."""


@dataclass(frozen=True)
class InboxConfig:
    name: str
    path: Path


@dataclass(frozen=True)
class PathsConfig:
    inboxes: tuple[InboxConfig, ...]
    outbox: Path
    sent_archive: Path
    state_db: Path


@dataclass(frozen=True)
class StabilityConfig:
    stable_seconds: int = 10
    min_age_seconds: int = 5
    ignore_hidden: bool = True
    ignore_suffixes: tuple[str, ...] = DEFAULT_TEMP_SUFFIXES


@dataclass(frozen=True)
class RetryConfig:
    initial_backoff_seconds: int = 5
    max_backoff_seconds: int = 300


@dataclass(frozen=True)
class SenderConfig:
    host: str
    user: str
    port: int = 22
    incoming_dir: str = "/volume1/receipts/incoming"
    staging_dir: str = "/volume1/receipts/staging"
    ssh_key: Path | None = None
    connect_timeout_seconds: int = 10
    dry_run: bool = False
    rsync_dry_run: bool = False
    rsync_binary: str = "rsync"
    ssh_binary: str = "ssh"


@dataclass(frozen=True)
class RuntimeConfig:
    poll_interval_seconds: int = 5
    user_tag: str = ""
    post_send_action: str = "archive"
    log_level: str = "INFO"


@dataclass(frozen=True)
class ShipperConfig:
    paths: PathsConfig
    stability: StabilityConfig
    retry: RetryConfig
    sender: SenderConfig
    runtime: RuntimeConfig


_ENV_TO_KEY = {
    "SHIPPER_OUTBOX_DIR": ("paths", "outbox"),
    "SHIPPER_SENT_ARCHIVE_DIR": ("paths", "sent_archive"),
    "SHIPPER_STATE_DB": ("paths", "state_db"),
    "SHIPPER_NAS_HOST": ("sender", "host"),
    "SHIPPER_NAS_USER": ("sender", "user"),
    "SHIPPER_NAS_PORT": ("sender", "port"),
    "SHIPPER_NAS_INCOMING_DIR": ("sender", "incoming_dir"),
    "SHIPPER_NAS_STAGING_DIR": ("sender", "staging_dir"),
    "SHIPPER_SSH_KEY": ("sender", "ssh_key"),
    "SHIPPER_DRY_RUN": ("sender", "dry_run"),
    "SHIPPER_RSYNC_DRY_RUN": ("sender", "rsync_dry_run"),
    "SHIPPER_LOG_LEVEL": ("runtime", "log_level"),
    "SHIPPER_USER_TAG": ("runtime", "user_tag"),
}


def _expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ConfigError("Top-level config must be a mapping")
    return payload


def _set_nested(payload: dict[str, Any], path_keys: tuple[str, ...], value: Any) -> None:
    target = payload
    for key in path_keys[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target[path_keys[-1]] = value


def _apply_env_overrides(payload: dict[str, Any]) -> None:
    for env_key, path_keys in _ENV_TO_KEY.items():
        if env_key not in os.environ:
            continue
        raw = os.environ[env_key]
        if env_key in {"SHIPPER_DRY_RUN", "SHIPPER_RSYNC_DRY_RUN"}:
            value: Any = _parse_bool(raw)
        elif env_key in {"SHIPPER_NAS_PORT"}:
            value = int(raw)
        else:
            value = raw
        _set_nested(payload, path_keys, value)

    inboxes_raw = os.environ.get("SHIPPER_INBOXES")
    if not inboxes_raw:
        return
    parsed: list[dict[str, str]] = []
    for item in inboxes_raw.split(","):
        token = item.strip()
        if not token:
            continue
        if "=" not in token:
            raise ConfigError(
                "SHIPPER_INBOXES must use comma-separated name=path pairs, e.g. scanner=/path,dropbox=/path"
            )
        name, path = token.split("=", 1)
        parsed.append({"name": name.strip(), "path": path.strip()})
    _set_nested(payload, ("paths", "inboxes"), parsed)


def _require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{key}' must be a mapping")
    return value


def _parse_inboxes(paths_cfg: dict[str, Any]) -> tuple[InboxConfig, ...]:
    inboxes_raw = paths_cfg.get("inboxes")
    if not isinstance(inboxes_raw, list) or not inboxes_raw:
        raise ConfigError("paths.inboxes must be a non-empty list")

    inboxes: list[InboxConfig] = []
    seen_names: set[str] = set()
    for row in inboxes_raw:
        if not isinstance(row, dict):
            raise ConfigError("Each inbox entry must be a mapping")
        name = str(row.get("name", "")).strip().lower()
        path_raw = row.get("path")
        if not name:
            raise ConfigError("Each inbox requires a non-empty 'name'")
        if name in seen_names:
            raise ConfigError(f"Duplicate inbox name: {name}")
        if not path_raw:
            raise ConfigError(f"Inbox '{name}' is missing 'path'")
        inboxes.append(InboxConfig(name=name, path=_expand_path(str(path_raw))))
        seen_names.add(name)
    return tuple(inboxes)


def load_config(config_path: str | Path | None = None) -> ShipperConfig:
    path = Path(
        config_path
        or os.environ.get("SHIPPER_CONFIG")
        or "/etc/receipt-shipper/shipper.yaml"
    ).expanduser()

    payload = _read_yaml(path)
    _apply_env_overrides(payload)

    paths_cfg = _require_mapping(payload, "paths")
    sender_cfg = _require_mapping(payload, "sender")
    stability_cfg = _require_mapping(payload, "stability")
    retry_cfg = _require_mapping(payload, "retry")
    runtime_cfg = _require_mapping(payload, "runtime")

    inboxes = _parse_inboxes(paths_cfg)

    outbox = _expand_path(str(paths_cfg.get("outbox") or "~/receipts/outbox"))
    sent_archive = _expand_path(str(paths_cfg.get("sent_archive") or "~/receipts/sent"))
    state_db = _expand_path(str(paths_cfg.get("state_db") or (outbox.parent / "shipper-state.db")))

    post_send_action = str(runtime_cfg.get("post_send_action") or "archive").strip().lower()
    if post_send_action not in {"archive", "delete"}:
        raise ConfigError("runtime.post_send_action must be either 'archive' or 'delete'")

    sender = SenderConfig(
        host=str(sender_cfg.get("host") or "").strip(),
        user=str(sender_cfg.get("user") or "").strip(),
        port=int(sender_cfg.get("port") or 22),
        incoming_dir=str(sender_cfg.get("incoming_dir") or "/volume1/receipts/incoming").strip(),
        staging_dir=str(sender_cfg.get("staging_dir") or "/volume1/receipts/staging").strip(),
        ssh_key=_expand_path(str(sender_cfg["ssh_key"])) if sender_cfg.get("ssh_key") else None,
        connect_timeout_seconds=int(sender_cfg.get("connect_timeout_seconds") or 10),
        dry_run=_parse_bool(sender_cfg.get("dry_run"), default=False),
        rsync_dry_run=_parse_bool(sender_cfg.get("rsync_dry_run"), default=False),
        rsync_binary=str(sender_cfg.get("rsync_binary") or "rsync"),
        ssh_binary=str(sender_cfg.get("ssh_binary") or "ssh"),
    )

    if not sender.dry_run and (not sender.host or not sender.user):
        raise ConfigError("sender.host and sender.user are required unless sender.dry_run=true")

    config = ShipperConfig(
        paths=PathsConfig(
            inboxes=inboxes,
            outbox=outbox,
            sent_archive=sent_archive,
            state_db=state_db,
        ),
        stability=StabilityConfig(
            stable_seconds=int(stability_cfg.get("stable_seconds") or 10),
            min_age_seconds=int(stability_cfg.get("min_age_seconds") or 5),
            ignore_hidden=_parse_bool(stability_cfg.get("ignore_hidden"), default=True),
            ignore_suffixes=tuple(
                str(item).lower()
                for item in (stability_cfg.get("ignore_suffixes") or DEFAULT_TEMP_SUFFIXES)
            ),
        ),
        retry=RetryConfig(
            initial_backoff_seconds=int(retry_cfg.get("initial_backoff_seconds") or 5),
            max_backoff_seconds=int(retry_cfg.get("max_backoff_seconds") or 300),
        ),
        runtime=RuntimeConfig(
            poll_interval_seconds=int(runtime_cfg.get("poll_interval_seconds") or 5),
            user_tag=str(runtime_cfg.get("user_tag") or "").strip(),
            post_send_action=post_send_action,
            log_level=str(runtime_cfg.get("log_level") or "INFO"),
        ),
        sender=sender,
    )

    if config.retry.initial_backoff_seconds < 1:
        raise ConfigError("retry.initial_backoff_seconds must be >= 1")
    if config.retry.max_backoff_seconds < config.retry.initial_backoff_seconds:
        raise ConfigError("retry.max_backoff_seconds must be >= retry.initial_backoff_seconds")
    if config.runtime.poll_interval_seconds < 1:
        raise ConfigError("runtime.poll_interval_seconds must be >= 1")
    if config.stability.stable_seconds < 1 or config.stability.min_age_seconds < 0:
        raise ConfigError("stability values are invalid")

    return config
