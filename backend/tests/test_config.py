"""Regression tests for app.core.config.Settings.

These guard two boot-time footguns that previously bit deployments:

1. CORS_ORIGINS as a comma-separated string raised a SettingsError at startup
   because pydantic-settings JSON-decodes complex (list) env vars before
   field validators run. The fix annotates the field with `NoDecode` and lets
   the validator accept both a comma list and a JSON array.

2. A production deploy that left SECRET_KEY at its placeholder value would
   silently sign tokens with a secret that's public in this repo.

No database or network is touched — these instantiate Settings directly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**env: str) -> Settings:
    """Build Settings from an explicit env mapping, ignoring any on-disk .env."""
    # _env_file=None keeps the checked-in .env from leaking into assertions.
    return Settings(_env_file=None, **env)


# --- CORS_ORIGINS parsing (Bug 1 regression) --------------------------------


def test_cors_origins_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert _settings().CORS_ORIGINS == ["http://localhost:3000"]


def test_cors_origins_comma_separated_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the exact form shipped in .env.example and the docker-compose
    # default — it used to raise SettingsError on boot.
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    assert _settings().CORS_ORIGINS == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    # The form used by the dev compose override and the checked-in .env.
    monkeypatch.setenv(
        "CORS_ORIGINS", '["http://localhost:3000","http://127.0.0.1:3000"]'
    )
    assert _settings().CORS_ORIGINS == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_single_bare_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    assert _settings().CORS_ORIGINS == ["http://localhost:3000"]


def test_cors_origins_strips_whitespace_and_blanks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", " http://a.com , , http://b.com ")
    assert _settings().CORS_ORIGINS == ["http://a.com", "http://b.com"]


# --- SECRET_KEY production guard --------------------------------------------


@pytest.mark.parametrize(
    "placeholder",
    [
        "change-me-in-production",
        "change-me-please-use-a-real-random-32-byte-secret",
    ],
)
def test_default_secret_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch, placeholder: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", placeholder)
    with pytest.raises(ValidationError):
        _settings()


def test_real_secret_accepted_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a-genuinely-random-secret-value-123456")
    assert _settings().is_production is True


def test_default_secret_allowed_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SECRET_KEY", "change-me-in-production")
    # Inert outside production — should not raise.
    assert _settings().is_development is True


# --- Upload allow-list -------------------------------------------------------


def test_csv_is_an_allowed_upload_extension() -> None:
    # CSV is a first-class supported platform; the allow-list must include it.
    assert ".csv" in _settings().ALLOWED_UPLOAD_EXTENSIONS
