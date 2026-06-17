"""Settings safe-default regression tests (plan T1-09).

The single most important financial-safety invariant at the config layer is that
a freshly-deployed instance with no configuration is SAFE: YNAB sync is disabled
and dry-run is on. `test_config_endpoint.test_config_defaults_safe_off` passes
explicit values to dodge the dev `.env`, so it does not actually assert the
*default*. These tests lock the field defaults directly (env-independent) and
verify a clean-env construction stays safe, so a stray default flip can't ship.

(T1-10 — token never leaks in /api/config — is already covered by
test_config_endpoint.test_no_token_in_response.)
"""

from __future__ import annotations

import pytest

from app.config import Settings


def test_sync_disabled_by_default() -> None:
    assert Settings.model_fields["ynab_sync_enabled"].default is False


def test_dry_run_enabled_by_default() -> None:
    assert Settings.model_fields["ynab_dry_run"].default is True


def test_constructed_settings_safe_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env file and the relevant env vars cleared, sync is off / dry-run on."""
    monkeypatch.delenv("YNAB_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("YNAB_DRY_RUN", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ynab_sync_enabled is False
    assert settings.ynab_dry_run is True
