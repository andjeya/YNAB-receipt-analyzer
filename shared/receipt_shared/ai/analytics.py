from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal

from .store import UsageLedgerStore
from .types import LedgerEvent
from .windows import bucket_start, ensure_utc

PeriodName = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class UsageAggregate:
    period_start: datetime
    model_id: str
    request_count: int
    tokens: int
    usd: Decimal


@dataclass(frozen=True)
class UsageSummaryStats:
    daily_avg_tokens: float
    daily_max_tokens: int
    weekly_avg_tokens: float
    monthly_avg_tokens: float
    daily_avg_usd: float
    daily_max_usd: float
    weekly_avg_usd: float
    monthly_avg_usd: float


class UsageAnalytics:
    def __init__(self, store: UsageLedgerStore):
        self.store = store

    @staticmethod
    def _date_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
        start_utc = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_utc = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        return start_utc, end_utc

    def _load_events(self, *, start_date: date, end_date: date) -> list[LedgerEvent]:
        start_utc, end_utc = self._date_bounds(start_date, end_date)
        return self.store.fetch_events(start_utc=start_utc, end_utc=end_utc)

    def breakdown(
        self,
        *,
        period: PeriodName,
        start_date: date,
        end_date: date,
    ) -> list[UsageAggregate]:
        events = self._load_events(start_date=start_date, end_date=end_date)
        buckets: dict[tuple[datetime, str], dict[str, Decimal | int]] = {}

        for event in events:
            key = (bucket_start(event.timestamp_utc, period), event.model_id)
            current = buckets.setdefault(
                key,
                {
                    "request_count": 0,
                    "tokens": 0,
                    "usd": Decimal("0"),
                },
            )
            current["request_count"] = int(current["request_count"]) + 1
            current["tokens"] = int(current["tokens"]) + int(event.total_tokens or 0)
            current["usd"] = Decimal(str(current["usd"])) + event.cost_usd

        aggregates: list[UsageAggregate] = []
        for (bucket_ts, model_id), value in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1])):
            aggregates.append(
                UsageAggregate(
                    period_start=ensure_utc(bucket_ts),
                    model_id=model_id,
                    request_count=int(value["request_count"]),
                    tokens=int(value["tokens"]),
                    usd=Decimal(str(value["usd"])).quantize(Decimal("0.00000001")),
                )
            )
        return aggregates

    def daily_breakdown(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[UsageAggregate]:
        return self.breakdown(period="daily", start_date=start_date, end_date=end_date)

    def summary_stats(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, UsageSummaryStats]:
        events = self._load_events(start_date=start_date, end_date=end_date)

        def _build(scope_events: list[LedgerEvent]) -> UsageSummaryStats:
            if not scope_events:
                return UsageSummaryStats(
                    daily_avg_tokens=0.0,
                    daily_max_tokens=0,
                    weekly_avg_tokens=0.0,
                    monthly_avg_tokens=0.0,
                    daily_avg_usd=0.0,
                    daily_max_usd=0.0,
                    weekly_avg_usd=0.0,
                    monthly_avg_usd=0.0,
                )

            token_buckets: dict[tuple[str, datetime], int] = defaultdict(int)
            usd_buckets: dict[tuple[str, datetime], Decimal] = defaultdict(lambda: Decimal("0"))

            for event in scope_events:
                for period in ("daily", "weekly", "monthly"):
                    key = (period, bucket_start(event.timestamp_utc, period))
                    token_buckets[key] += int(event.total_tokens or 0)
                    usd_buckets[key] += event.cost_usd

            def _avg(period: str, source: dict[tuple[str, datetime], Decimal | int]) -> float:
                values = [value for (name, _), value in source.items() if name == period]
                if not values:
                    return 0.0
                if isinstance(values[0], Decimal):
                    total = sum((Decimal(str(v)) for v in values), Decimal("0"))
                    return float(total / Decimal(str(len(values))))
                return float(sum(int(v) for v in values) / len(values))

            def _max_tokens(period: str) -> int:
                values = [int(value) for (name, _), value in token_buckets.items() if name == period]
                return max(values) if values else 0

            def _max_usd(period: str) -> float:
                values = [Decimal(str(value)) for (name, _), value in usd_buckets.items() if name == period]
                if not values:
                    return 0.0
                return float(max(values))

            return UsageSummaryStats(
                daily_avg_tokens=_avg("daily", token_buckets),
                daily_max_tokens=_max_tokens("daily"),
                weekly_avg_tokens=_avg("weekly", token_buckets),
                monthly_avg_tokens=_avg("monthly", token_buckets),
                daily_avg_usd=_avg("daily", usd_buckets),
                daily_max_usd=_max_usd("daily"),
                weekly_avg_usd=_avg("weekly", usd_buckets),
                monthly_avg_usd=_avg("monthly", usd_buckets),
            )

        grouped: dict[str, list[LedgerEvent]] = defaultdict(list)
        for event in events:
            grouped[event.model_id].append(event)

        results: dict[str, UsageSummaryStats] = {}
        results["__overall__"] = _build(events)
        for model_id in sorted(grouped.keys()):
            results[model_id] = _build(grouped[model_id])
        return results
