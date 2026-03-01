"""Shared pytest fixtures for the backend test suite."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import YNABCacheEntityType
from app.models import Base, YNABCache


@pytest.fixture()
def db_session() -> Session:
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def test_settings() -> Settings:
    """Minimal Settings instance suitable for unit tests."""
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id="test-budget-id",
        ynab_default_account_id="acct-1",
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )


@pytest.fixture()
def db_with_cache(db_session: Session, test_settings: Settings) -> Session:
    """Session pre-populated with a minimal YNAB cache (one category, one account)."""
    db_session.add_all(
        [
            YNABCache(
                budget_id=test_settings.ynab_budget_id,
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-1",
                name="Groceries",
                group_name="Everyday",
                raw_json={"id": "cat-1", "name": "Groceries"},
            ),
            YNABCache(
                budget_id=test_settings.ynab_budget_id,
                entity_type=YNABCacheEntityType.ACCOUNT.value,
                entity_id="acct-1",
                name="Checking",
                group_name=None,
                raw_json={"id": "acct-1", "name": "Checking"},
            ),
        ]
    )
    db_session.commit()
    return db_session
