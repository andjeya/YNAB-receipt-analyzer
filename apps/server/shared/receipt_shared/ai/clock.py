from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime:
        ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)
