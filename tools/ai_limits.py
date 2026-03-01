from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "server" / "backend"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "server" / "shared"))

from app.config import get_settings
from receipt_shared.ai import LimitsConfigRepository, ModelRegistryRepository, UsageAnalytics, UsageLedgerStore
from receipt_shared.ai.windows import WINDOWS
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, Switch, TabPane, TabbedContent


def _format_limit_tokens(unlimited: bool, value: int | None) -> str:
    if unlimited or value is None:
        return "unlimited"
    return str(value)


def _format_limit_usd(unlimited: bool, value: Decimal | None) -> str:
    if unlimited or value is None:
        return "unlimited"
    return f"{value:.6f}"


class AILimitsApp(App[None]):
    TITLE = "AI Usage Limits"
    SUB_TITLE = "Registry + limits + usage analytics"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("[", "shift_back", "Range -7d"),
        Binding("]", "shift_forward", "Range +7d"),
    ]

    def __init__(self) -> None:
        super().__init__()
        settings = get_settings()
        self.registry_repo = ModelRegistryRepository(settings.ai_model_registry_path)
        self.limits_repo = LimitsConfigRepository(settings.ai_limits_config_path)
        self.store = UsageLedgerStore(settings.ai_usage_db_url)
        self.analytics = UsageAnalytics(self.store)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield Static("Current limits and usage for hourly/daily/weekly/monthly windows.", id="overview_info")
                yield DataTable(id="overview_table")

            with TabPane("Edit Limits", id="edit"):
                yield Static("Edit one scope/window at a time and save atomically.", id="edit_info")
                with Horizontal():
                    yield Label("Scope (global or model_id):")
                    yield Input(value="global", id="edit_scope_input")
                with Horizontal():
                    yield Label("Window (hourly/daily/weekly/monthly):")
                    yield Input(value="daily", id="edit_window_input")
                with Horizontal():
                    yield Label("Token cap (empty = unlimited):")
                    yield Input(value="", id="edit_tokens_input")
                with Horizontal():
                    yield Label("USD cap (empty = unlimited):")
                    yield Input(value="", id="edit_usd_input")
                with Horizontal():
                    yield Label("Unlimited override:")
                    yield Switch(value=False, id="edit_unlimited_switch")
                    yield Button("Apply", id="apply_limit", variant="primary")
                yield Static("", id="edit_status")
                yield DataTable(id="edit_limits_table")

            with TabPane("Usage Analytics", id="analytics"):
                yield Static("Daily breakdown + avg/max stats. Use [ and ] to shift date range.", id="analytics_info")
                with Horizontal():
                    yield Label("Start (YYYY-MM-DD):")
                    yield Input(id="analytics_start_input")
                    yield Label("End (YYYY-MM-DD):")
                    yield Input(id="analytics_end_input")
                    yield Button("Refresh", id="refresh_analytics", variant="primary")
                    yield Button("-7d", id="shift_back_btn")
                    yield Button("+7d", id="shift_forward_btn")
                yield Static("", id="analytics_status")
                with Vertical():
                    yield Label("Daily breakdown")
                    yield DataTable(id="analytics_breakdown_table")
                with Vertical():
                    yield Label("Summary stats")
                    yield DataTable(id="analytics_summary_table")
        yield Footer()

    def on_mount(self) -> None:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=29)
        self.query_one("#analytics_start_input", Input).value = start.isoformat()
        self.query_one("#analytics_end_input", Input).value = end.isoformat()

        self._init_tables()
        self.refresh_all()

    def _init_tables(self) -> None:
        overview = self.query_one("#overview_table", DataTable)
        overview.add_columns("Scope", "Window", "Limit Tokens", "Limit USD", "Usage Tokens", "Usage USD")

        edit = self.query_one("#edit_limits_table", DataTable)
        edit.add_columns("Scope", "Window", "Unlimited", "Token Cap", "USD Cap")

        breakdown = self.query_one("#analytics_breakdown_table", DataTable)
        breakdown.add_columns("Date", "Model", "Requests", "Tokens", "USD")

        summary = self.query_one("#analytics_summary_table", DataTable)
        summary.add_columns(
            "Scope",
            "Daily Avg Tokens",
            "Daily Max Tokens",
            "Weekly Avg Tokens",
            "Monthly Avg Tokens",
            "Daily Avg USD",
            "Daily Max USD",
            "Weekly Avg USD",
            "Monthly Avg USD",
        )

    def refresh_all(self) -> None:
        self._refresh_overview()
        self._refresh_edit_table()
        self._refresh_analytics()

    def _model_ids_for_display(self) -> list[str]:
        ids = set()
        try:
            ids.update(self.registry_repo.load().available_model_ids())
        except Exception:
            pass
        ids.update(self.limits_repo.load().all_models())
        ids.update(self.store.list_models())
        return sorted(ids)

    def _refresh_overview(self) -> None:
        table = self.query_one("#overview_table", DataTable)
        table.clear()

        limits = self.limits_repo.load()
        now = datetime.now(timezone.utc)

        global_usage = self.store.window_totals(now_utc=now, model_id=None)
        for window in WINDOWS:
            limit = limits.get_global(window)
            usage = global_usage[window]
            table.add_row(
                "global",
                window,
                _format_limit_tokens(limit.unlimited, limit.tokens),
                _format_limit_usd(limit.unlimited, limit.usd),
                str(usage.tokens),
                f"{usage.usd:.6f}",
            )

        for model_id in self._model_ids_for_display():
            model_usage = self.store.window_totals(now_utc=now, model_id=model_id)
            for window in WINDOWS:
                limit = limits.get_model(model_id, window)
                usage = model_usage[window]
                table.add_row(
                    model_id,
                    window,
                    _format_limit_tokens(limit.unlimited, limit.tokens),
                    _format_limit_usd(limit.unlimited, limit.usd),
                    str(usage.tokens),
                    f"{usage.usd:.6f}",
                )

    def _refresh_edit_table(self) -> None:
        table = self.query_one("#edit_limits_table", DataTable)
        table.clear()
        limits = self.limits_repo.load()

        for window in WINDOWS:
            limit = limits.get_global(window)
            table.add_row("global", window, str(limit.unlimited), str(limit.tokens), str(limit.usd))

        for model_id in self._model_ids_for_display():
            for window in WINDOWS:
                limit = limits.get_model(model_id, window)
                table.add_row(model_id, window, str(limit.unlimited), str(limit.tokens), str(limit.usd))

    def _parse_date_range(self) -> tuple[date, date]:
        start_text = self.query_one("#analytics_start_input", Input).value.strip()
        end_text = self.query_one("#analytics_end_input", Input).value.strip()
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        if start > end:
            raise ValueError("start date must be <= end date")
        return start, end

    def _refresh_analytics(self) -> None:
        status = self.query_one("#analytics_status", Static)
        breakdown_table = self.query_one("#analytics_breakdown_table", DataTable)
        summary_table = self.query_one("#analytics_summary_table", DataTable)

        breakdown_table.clear()
        summary_table.clear()

        try:
            start, end = self._parse_date_range()
        except Exception as exc:
            status.update(f"Invalid date range: {exc}")
            return

        daily_rows = self.analytics.daily_breakdown(start_date=start, end_date=end)
        for row in daily_rows:
            breakdown_table.add_row(
                row.period_start.date().isoformat(),
                row.model_id,
                str(row.request_count),
                str(row.tokens),
                f"{row.usd:.6f}",
            )

        stats = self.analytics.summary_stats(start_date=start, end_date=end)
        for scope in sorted(stats.keys()):
            stat = stats[scope]
            label = "overall" if scope == "__overall__" else scope
            summary_table.add_row(
                label,
                f"{stat.daily_avg_tokens:.2f}",
                str(stat.daily_max_tokens),
                f"{stat.weekly_avg_tokens:.2f}",
                f"{stat.monthly_avg_tokens:.2f}",
                f"{stat.daily_avg_usd:.6f}",
                f"{stat.daily_max_usd:.6f}",
                f"{stat.weekly_avg_usd:.6f}",
                f"{stat.monthly_avg_usd:.6f}",
            )

        status.update(
            f"Range {start.isoformat()} to {end.isoformat()} | rows={len(daily_rows)} | scopes={len(stats)}"
        )

    def _shift_date_range(self, days: int) -> None:
        try:
            start, end = self._parse_date_range()
        except Exception:
            return
        start += timedelta(days=days)
        end += timedelta(days=days)
        self.query_one("#analytics_start_input", Input).value = start.isoformat()
        self.query_one("#analytics_end_input", Input).value = end.isoformat()
        self._refresh_analytics()

    @on(Button.Pressed, "#apply_limit")
    def on_apply_limit(self) -> None:
        scope = self.query_one("#edit_scope_input", Input).value.strip() or "global"
        window = self.query_one("#edit_window_input", Input).value.strip().lower()
        tokens_text = self.query_one("#edit_tokens_input", Input).value.strip()
        usd_text = self.query_one("#edit_usd_input", Input).value.strip()
        unlimited = self.query_one("#edit_unlimited_switch", Switch).value
        status = self.query_one("#edit_status", Static)

        if window not in WINDOWS:
            status.update(f"Invalid window '{window}'. Use one of: {', '.join(WINDOWS)}")
            return

        tokens_value = None
        if tokens_text:
            try:
                tokens_value = max(int(tokens_text), 0)
            except Exception:
                status.update("Token cap must be an integer or empty.")
                return

        usd_value = None
        if usd_text:
            try:
                usd_value = max(float(usd_text), 0.0)
            except Exception:
                status.update("USD cap must be a number or empty.")
                return

        payload = self.limits_repo.load().to_json_dict()

        window_payload = {
            "unlimited": bool(unlimited),
            "tokens": tokens_value,
            "usd": usd_value,
        }

        if scope.lower() == "global":
            payload.setdefault("global", {})[window] = window_payload
        else:
            payload.setdefault("models", {})
            model_payload = payload["models"].setdefault(scope, {})
            model_payload[window] = window_payload
            for other_window in WINDOWS:
                model_payload.setdefault(other_window, {"unlimited": True, "tokens": None, "usd": None})

        try:
            parsed = self.limits_repo.parse_payload(payload)
            self.limits_repo.save(parsed)
        except Exception as exc:
            status.update(f"Failed to save limits: {exc}")
            return

        status.update(f"Saved {scope}:{window} (unlimited={unlimited}, tokens={tokens_value}, usd={usd_value})")
        self._refresh_overview()
        self._refresh_edit_table()

    @on(Button.Pressed, "#refresh_analytics")
    def on_refresh_analytics(self) -> None:
        self._refresh_analytics()

    @on(Button.Pressed, "#shift_back_btn")
    def on_shift_back_btn(self) -> None:
        self._shift_date_range(-7)

    @on(Button.Pressed, "#shift_forward_btn")
    def on_shift_forward_btn(self) -> None:
        self._shift_date_range(7)

    def action_shift_back(self) -> None:
        self._shift_date_range(-7)

    def action_shift_forward(self) -> None:
        self._shift_date_range(7)

    def action_refresh(self) -> None:
        self.refresh_all()


def main() -> None:
    app = AILimitsApp()
    app.run()


if __name__ == "__main__":
    main()
