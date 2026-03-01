from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.deps import require_debug_tools_enabled
from app.config import Settings
from app.services.debug_tools import is_debug_tools_enabled


def test_debug_tools_disabled_by_default_when_flag_missing(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        debug_tools_enabled=False,
        debug_tools_flag_file=tmp_path / "debug_tools_enabled.flag",
    )
    assert is_debug_tools_enabled(settings) is False


def test_debug_tools_enabled_when_flag_exists(tmp_path: Path):
    flag_path = tmp_path / "debug_tools_enabled.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()
    settings = Settings(
        _env_file=None,
        debug_tools_enabled=False,
        debug_tools_flag_file=flag_path,
    )
    assert is_debug_tools_enabled(settings) is True


def test_debug_tools_disabled_when_flag_missing_even_if_env_true(tmp_path):
    settings = Settings(
        _env_file=None,
        debug_tools_enabled=True,
        debug_tools_flag_file=tmp_path / "debug_tools_enabled.flag",
    )
    assert is_debug_tools_enabled(settings) is False


def test_require_debug_tools_enabled_returns_404_when_disabled(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        debug_tools_enabled=False,
        debug_tools_flag_file=tmp_path / "debug_tools_enabled.flag",
    )
    with pytest.raises(HTTPException) as exc_info:
        require_debug_tools_enabled(settings=settings)
    assert exc_info.value.status_code == 404


def test_require_debug_tools_enabled_allows_when_flag_exists(tmp_path: Path):
    flag_path = tmp_path / "debug_tools_enabled.flag"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()
    settings = Settings(
        _env_file=None,
        debug_tools_enabled=False,
        debug_tools_flag_file=flag_path,
    )
    require_debug_tools_enabled(settings=settings)
