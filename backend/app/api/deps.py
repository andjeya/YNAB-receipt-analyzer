from __future__ import annotations

from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.services.debug_tools import is_debug_tools_enabled


def db_session(db: Session = Depends(get_db)) -> Session:
    return db


def app_settings(settings: Settings = Depends(get_settings)) -> Settings:
    return settings


def require_debug_tools_enabled(settings: Settings = Depends(get_settings)) -> None:
    if not is_debug_tools_enabled(settings):
        # Keep hidden when disabled.
        raise HTTPException(status_code=404, detail="Not found")
