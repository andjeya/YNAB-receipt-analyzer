from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def ensure_schema_current() -> None:
    """Apply Alembic migrations to `head` for long-running processes.

    This keeps scanner/worker/api robust when local DB files lag behind the
    currently deployed code schema.
    """

    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(alembic_ini))
    command.upgrade(cfg, "head")
    logger.info("Database schema ensured at Alembic head")
