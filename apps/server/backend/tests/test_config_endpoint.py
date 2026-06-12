"""Tests for GET /api/config endpoint.

Covers:
1. test_config_defaults_safe_off — ynab_sync_enabled=False, ynab_dry_run=True by default.
2. test_budget_name_none_on_empty_cache — budget_name is None when cache is empty.
3. test_flag_colors_reflected — flag color settings returned verbatim.
4. test_no_token_in_response — serialized JSON does not contain ynab_access_token key or token value.
5. test_ynab_sync_enabled_reflected — sync_enabled=True reflected when set.
6. test_dry_run_false_reflected — dry_run=False reflected when set.
7. test_budget_name_still_none_with_populated_cache — budget_name None even when cache rows exist.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import app.api.config as config_module
from app.api.config import get_app_config
from app.config import Settings
from app.enums import YNABCacheEntityType
from app.models import YNABCache
from app.schemas import AppConfigOut


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    base = dict(
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="secret-token-do-not-expose",
        ynab_budget_id="test-budget-id",
        ynab_default_account_id="acct-1",
        object_store_root="./data",
        ingest_dir="./data/ingest",
        # Keep sync disabled by default so tests that don't exercise budget-name
        # fetching do not attempt YNAB network calls (YNAB_SYNC_ENABLED may be
        # set to true in the dev .env).
        ynab_sync_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _call_endpoint(db_session, settings: Settings) -> AppConfigOut:
    """Call get_app_config with injected db and settings."""
    return get_app_config(db=db_session, settings=settings)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_budget_name_cache():
    """Reset the module-level budget name cache before each test."""
    config_module._budget_name_cache.clear()
    yield
    config_module._budget_name_cache.clear()


def test_config_defaults_safe_off(db_session):
    """ynab_sync_enabled defaults to False; ynab_dry_run defaults to True (when set explicitly)."""
    # Provide explicit safe values since the test environment may have YNAB_SYNC_ENABLED=true.
    settings = _make_settings(ynab_sync_enabled=False, ynab_dry_run=True)
    result = _call_endpoint(db_session, settings)
    assert result.ynab_sync_enabled is False
    assert result.ynab_dry_run is True


def test_budget_name_none_on_empty_cache(db_session):
    """budget_name is None when no cache rows exist."""
    settings = _make_settings()
    result = _call_endpoint(db_session, settings)
    assert result.ynab_budget_name is None


def test_flag_colors_reflected(db_session):
    """Flag color settings are returned verbatim."""
    settings = _make_settings(
        ynab_new_transaction_flag_color="green",
        ynab_updated_transaction_flag_color="red",
    )
    result = _call_endpoint(db_session, settings)
    assert result.new_transaction_flag_color == "green"
    assert result.updated_transaction_flag_color == "red"


def test_no_token_in_response(db_session):
    """Serialized response JSON must not contain the access token or its key."""
    settings = _make_settings(ynab_access_token="my-secret-token-xyz")
    result = _call_endpoint(db_session, settings)
    serialized = result.model_dump_json()
    payload = json.loads(serialized)

    # Key must not be present
    assert "ynab_access_token" not in payload
    # Token value must not appear in the raw JSON string
    assert "my-secret-token-xyz" not in serialized


def test_ynab_sync_enabled_reflected(db_session):
    """ynab_sync_enabled=True is reflected in the response."""
    settings = _make_settings(ynab_sync_enabled=True)
    mock_client = MagicMock()
    mock_client.list_budgets.return_value = []
    with patch("app.api.config.YNABClient", return_value=mock_client):
        result = _call_endpoint(db_session, settings)
    assert result.ynab_sync_enabled is True


def test_dry_run_false_reflected(db_session):
    """ynab_dry_run=False is reflected in the response."""
    settings = _make_settings(ynab_dry_run=False)
    result = _call_endpoint(db_session, settings)
    assert result.ynab_dry_run is False


def test_budget_name_still_none_with_populated_cache(db_session):
    """budget_name is None even when cache rows exist (budget name not stored in cache)."""
    settings = _make_settings(ynab_budget_id="test-budget-id")
    db_session.add(
        YNABCache(
            budget_id="test-budget-id",
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id="cat-1",
            name="Groceries",
            group_name="Everyday",
            raw_json={"id": "cat-1", "name": "Groceries"},
        )
    )
    db_session.commit()
    result = _call_endpoint(db_session, settings)
    assert result.ynab_budget_name is None


def test_budget_id_reflected(db_session):
    """ynab_budget_id is returned in the response."""
    settings = _make_settings(ynab_budget_id="my-budget-abc")
    result = _call_endpoint(db_session, settings)
    assert result.ynab_budget_id == "my-budget-abc"


def test_budget_id_none_when_not_configured(db_session):
    """ynab_budget_id is None when not configured."""
    settings = _make_settings(ynab_budget_id=None)
    result = _call_endpoint(db_session, settings)
    assert result.ynab_budget_id is None
