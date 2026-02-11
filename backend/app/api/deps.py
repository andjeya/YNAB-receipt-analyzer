from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db


def db_session(db: Session = Depends(get_db)) -> Session:
    return db


def app_settings(settings: Settings = Depends(get_settings)) -> Settings:
    return settings
