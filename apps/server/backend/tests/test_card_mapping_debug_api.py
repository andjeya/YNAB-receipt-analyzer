"""Tests for the debug-only card mapping API.

Covers:
- 404 when debug disabled (all verbs).
- Enabled: GET lists with account_name (stale → null).
- PUT create then edit.
- PUT unknown account → 422, non-digit card → 422.
- DELETE 204 then missing → 404.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.card_mappings import delete_mapping, list_mappings, upsert_mapping
from app.config import Settings
from app.enums import YNABCacheEntityType
from app.models import YNABCache
from app.schemas import CardMappingUpsertRequest


BUDGET_ID = "test-budget"


def _make_settings(tmp_path: Path, debug_enabled: bool = False, **overrides: Any) -> Settings:
    flag_path = tmp_path / "debug_tools_enabled.flag"
    if debug_enabled:
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.touch()
    base = dict(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id=BUDGET_ID,
        ynab_default_account_id="acct-1",
        debug_tools_enabled=False,
        debug_tools_flag_file=flag_path,
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )
    base.update(overrides)
    return Settings(**base)


def _add_account(db: Session, account_id: str, name: str = "Test Account") -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.ACCOUNT.value,
            entity_id=account_id,
            name=name,
            group_name=None,
            raw_json={"id": account_id, "name": name},
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Debug disabled — all endpoints return 404
# ---------------------------------------------------------------------------

class TestDebugDisabled:
    def _disabled_settings(self, tmp_path: Path) -> Settings:
        return _make_settings(tmp_path, debug_enabled=False)

    def test_list_returns_404_when_disabled(self, db_session: Session, tmp_path: Path):
        settings = self._disabled_settings(tmp_path)
        from app.api.deps import require_debug_tools_enabled
        with pytest.raises(HTTPException) as exc_info:
            require_debug_tools_enabled(settings=settings)
        assert exc_info.value.status_code == 404

    def test_put_returns_404_when_disabled(self, db_session: Session, tmp_path: Path):
        settings = self._disabled_settings(tmp_path)
        from app.api.deps import require_debug_tools_enabled
        with pytest.raises(HTTPException) as exc_info:
            require_debug_tools_enabled(settings=settings)
        assert exc_info.value.status_code == 404

    def test_delete_returns_404_when_disabled(self, db_session: Session, tmp_path: Path):
        settings = self._disabled_settings(tmp_path)
        from app.api.deps import require_debug_tools_enabled
        with pytest.raises(HTTPException) as exc_info:
            require_debug_tools_enabled(settings=settings)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Debug enabled — happy paths
# ---------------------------------------------------------------------------

class TestDebugEnabled:
    def _settings(self, tmp_path: Path) -> Settings:
        return _make_settings(tmp_path, debug_enabled=True)

    def test_list_empty(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        result = list_mappings(_=None, db=db_session, settings=settings)
        assert result.items == []

    def test_list_with_account_name(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        _add_account(db_session, "acct-1", "Checking")

        # Create a mapping via upsert
        body = CardMappingUpsertRequest(card_last_four="5830", account_id="acct-1")
        upsert_mapping(body=body, _=None, db=db_session, settings=settings)

        result = list_mappings(_=None, db=db_session, settings=settings)
        assert len(result.items) == 1
        assert result.items[0].card_last_four == "5830"
        assert result.items[0].account_id == "acct-1"
        assert result.items[0].account_name == "Checking"

    def test_list_stale_account_has_null_name(self, db_session: Session, tmp_path: Path):
        """Mapping pointing to stale (removed from cache) account → account_name=None."""
        settings = self._settings(tmp_path)
        # Add account to cache, create mapping, then remove from cache.
        _add_account(db_session, "acct-temp", "Temp Account")
        body = CardMappingUpsertRequest(card_last_four="1111", account_id="acct-temp")
        upsert_mapping(body=body, _=None, db=db_session, settings=settings)

        # Remove from cache (simulate stale).
        cache_row = db_session.scalar(
            __import__("sqlalchemy", fromlist=["select"]).select(YNABCache).where(
                YNABCache.entity_id == "acct-temp"
            )
        )
        if cache_row:
            db_session.delete(cache_row)
            db_session.commit()

        result = list_mappings(_=None, db=db_session, settings=settings)
        assert len(result.items) == 1
        assert result.items[0].account_name is None

    def test_put_create_then_edit(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        _add_account(db_session, "acct-1", "Checking")
        _add_account(db_session, "acct-2", "Savings")

        # Create
        body1 = CardMappingUpsertRequest(card_last_four="5830", account_id="acct-1")
        result1 = upsert_mapping(body=body1, _=None, db=db_session, settings=settings)
        assert result1.card_last_four == "5830"
        assert result1.account_id == "acct-1"
        assert result1.account_name == "Checking"
        mapping_id = result1.id

        # Edit (same card, different account)
        body2 = CardMappingUpsertRequest(card_last_four="5830", account_id="acct-2")
        result2 = upsert_mapping(body=body2, _=None, db=db_session, settings=settings)
        assert result2.account_id == "acct-2"
        assert result2.account_name == "Savings"
        # Single row, same id
        assert result2.id == mapping_id

    def test_put_unknown_account_returns_422(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        # Account not in cache.
        body = CardMappingUpsertRequest(card_last_four="5830", account_id="acct-nonexistent")
        with pytest.raises(HTTPException) as exc_info:
            upsert_mapping(body=body, _=None, db=db_session, settings=settings)
        assert exc_info.value.status_code == 422

    def test_put_non_digit_card_returns_422(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        _add_account(db_session, "acct-1")
        body = CardMappingUpsertRequest(card_last_four="cash", account_id="acct-1")
        with pytest.raises(HTTPException) as exc_info:
            upsert_mapping(body=body, _=None, db=db_session, settings=settings)
        assert exc_info.value.status_code == 422

    def test_put_too_few_digits_returns_422(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        _add_account(db_session, "acct-1")
        body = CardMappingUpsertRequest(card_last_four="123", account_id="acct-1")
        with pytest.raises(HTTPException) as exc_info:
            upsert_mapping(body=body, _=None, db=db_session, settings=settings)
        assert exc_info.value.status_code == 422

    def test_delete_204_then_missing_404(self, db_session: Session, tmp_path: Path):
        settings = self._settings(tmp_path)
        _add_account(db_session, "acct-1")

        # Create mapping
        body = CardMappingUpsertRequest(card_last_four="9999", account_id="acct-1")
        created = upsert_mapping(body=body, _=None, db=db_session, settings=settings)
        mapping_id = created.id

        # Delete → 204
        response = delete_mapping(mapping_id=mapping_id, _=None, db=db_session)
        assert response.status_code == 204

        # Delete again → 404
        with pytest.raises(HTTPException) as exc_info:
            delete_mapping(mapping_id=mapping_id, _=None, db=db_session)
        assert exc_info.value.status_code == 404
