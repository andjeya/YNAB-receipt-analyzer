from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

WindowName = Literal["hourly", "daily", "weekly", "monthly"]
WINDOWS: tuple[WindowName, ...] = ("hourly", "daily", "weekly", "monthly")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def window_start(now: datetime, window: WindowName) -> datetime:
    current = ensure_utc(now)
    if window == "hourly":
        return current.replace(minute=0, second=0, microsecond=0)
    if window == "daily":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "weekly":
        day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    if window == "monthly":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported window: {window}")


def bucket_start(ts: datetime, period: Literal["daily", "weekly", "monthly"]) -> datetime:
    if period == "daily":
        return window_start(ts, "daily")
    if period == "weekly":
        return window_start(ts, "weekly")
    if period == "monthly":
        return window_start(ts, "monthly")
    raise ValueError(f"Unsupported period: {period}")
