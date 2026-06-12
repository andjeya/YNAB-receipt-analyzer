"""Tests for budget name fetching in GET /api/config.

Covers:
1. success → name returned from YNAB API
2. failure (exception) → null returned, no 500
3. cached on second call → client called only once
4. sync disabled → null without calling YNAB
5. no token → null without calling YNAB
6. no budget_id → null without calling YNAB
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

import app.api.config as config_module
from app.api.config import _fetch_budget_name, get_app_config
from app.config import Settings


def _make_settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id="budget-abc",
        ynab_default_account_id="acct-1",
        object_store_root="./data",
        ingest_dir="./data/ingest",
        ynab_sync_enabled=True,
        ynab_dry_run=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def clear_budget_name_cache():
    """Reset the module-level cache before each test to keep tests independent."""
    config_module._budget_name_cache.clear()
    yield
    config_module._budget_name_cache.clear()


class TestFetchBudgetName:
    def test_success_returns_name(self):
        """When YNAB returns the budget, its name is returned."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.return_value = [
            {"id": "other-budget", "name": "Other"},
            {"id": "budget-abc", "name": "My Budget"},
        ]
        with patch("app.api.config.YNABClient", return_value=mock_client):
            result = _fetch_budget_name(settings)
        assert result == "My Budget"

    def test_failure_returns_none(self):
        """When the YNAB client raises an exception, None is returned (no 500)."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.side_effect = RuntimeError("YNAB unreachable")
        with patch("app.api.config.YNABClient", return_value=mock_client):
            result = _fetch_budget_name(settings)
        assert result is None

    def test_cached_on_second_call(self):
        """YNAB client is called only once; second call returns cached name."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.return_value = [
            {"id": "budget-abc", "name": "Cached Budget"},
        ]
        with patch("app.api.config.YNABClient", return_value=mock_client) as mock_cls:
            result1 = _fetch_budget_name(settings)
            result2 = _fetch_budget_name(settings)

        assert result1 == "Cached Budget"
        assert result2 == "Cached Budget"
        # YNABClient constructor called only once → list_budgets called once
        assert mock_cls.call_count == 1
        assert mock_client.list_budgets.call_count == 1

    def test_sync_disabled_returns_none_no_call(self):
        """When sync is disabled, None is returned without calling YNAB."""
        settings = _make_settings(ynab_sync_enabled=False)
        with patch("app.api.config.YNABClient") as mock_cls:
            result = _fetch_budget_name(settings)
        assert result is None
        mock_cls.assert_not_called()

    def test_no_token_returns_none(self):
        """When no access token, None is returned without calling YNAB."""
        settings = _make_settings(ynab_access_token=None)
        with patch("app.api.config.YNABClient") as mock_cls:
            result = _fetch_budget_name(settings)
        assert result is None
        mock_cls.assert_not_called()

    def test_no_budget_id_returns_none(self):
        """When no budget_id configured, None is returned without calling YNAB."""
        settings = _make_settings(ynab_budget_id=None)
        with patch("app.api.config.YNABClient") as mock_cls:
            result = _fetch_budget_name(settings)
        assert result is None
        mock_cls.assert_not_called()

    def test_budget_not_in_list_returns_none(self):
        """When the configured budget_id is not in the returned list, None is returned."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.return_value = [
            {"id": "other-budget", "name": "Other Budget"},
        ]
        with patch("app.api.config.YNABClient", return_value=mock_client):
            result = _fetch_budget_name(settings)
        assert result is None


class TestGetAppConfigBudgetName:
    def test_config_endpoint_returns_budget_name(self, db_session):
        """get_app_config returns the budget name when available."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.return_value = [
            {"id": "budget-abc", "name": "Family Budget"},
        ]
        with patch("app.api.config.YNABClient", return_value=mock_client):
            result = get_app_config(db=db_session, settings=settings)
        assert result.ynab_budget_name == "Family Budget"

    def test_config_endpoint_returns_null_on_ynab_error(self, db_session):
        """get_app_config returns null for budget_name when YNAB is unreachable."""
        settings = _make_settings()
        mock_client = MagicMock()
        mock_client.list_budgets.side_effect = Exception("timeout")
        with patch("app.api.config.YNABClient", return_value=mock_client):
            result = get_app_config(db=db_session, settings=settings)
        assert result.ynab_budget_name is None

    def test_config_endpoint_null_when_sync_disabled(self, db_session):
        """get_app_config returns null for budget_name when sync is disabled."""
        settings = _make_settings(ynab_sync_enabled=False)
        with patch("app.api.config.YNABClient") as mock_cls:
            result = get_app_config(db=db_session, settings=settings)
        assert result.ynab_budget_name is None
        mock_cls.assert_not_called()
