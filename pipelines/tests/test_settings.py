"""`Settings` reads `PIPELINES_*` and never prints a secret."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pipelines.settings import Settings

#: The three secrets `Settings` requires — every test that builds one supplies
#: them, exactly as a deployment's `pipelines/.env` would.
REQUIRED_SECRETS = {
    "litellm_api_key": "litellm-key",
    "s3_access_key": "s3-access",
    "s3_secret_key": "s3-secret",
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**REQUIRED_SECRETS, **overrides})  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = _settings()
    assert settings.ea_base_url == "http://host.docker.internal:8000"
    assert settings.litellm_base_url == "http://litellm:4000"
    assert settings.llm_timeout_seconds == 300
    assert settings.ea_timeout_seconds == 30
    assert settings.s3_endpoint == "minio.famillelallier.net"
    assert settings.s3_secure is True
    assert settings.s3_bucket == "ea-catalogue"
    assert settings.s3_ca_cert is None
    assert settings.max_source_chars == 60000


def test_required_secrets_are_required() -> None:
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_env_prefix_is_pipelines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINES_EA_BASE_URL", "http://example.test:8000")
    monkeypatch.setenv("PIPELINES_LITELLM_API_KEY", "from-env")
    monkeypatch.setenv("PIPELINES_S3_ACCESS_KEY", "from-env")
    monkeypatch.setenv("PIPELINES_S3_SECRET_KEY", "from-env")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.ea_base_url == "http://example.test:8000"
    assert settings.litellm_api_key.get_secret_value() == "from-env"


def test_a_plain_prefix_without_pipelines_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EA_BASE_URL", "http://should-not-be-read:8000")
    settings = _settings()
    assert settings.ea_base_url == "http://host.docker.internal:8000"


def test_secrets_are_never_shown_in_repr() -> None:
    settings = _settings()
    rendered = repr(settings)
    for value in REQUIRED_SECRETS.values():
        assert value not in rendered


def test_a_dotenv_in_the_working_directory_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pipelines/.env` feeds docker compose, and holds keys `Settings` has no field for.

    Reading it would refuse to build (`extra="forbid"` on `LITELLM_DATABASE_URL`)
    and would let a developer's file leak into every test run from that directory.
    """
    (tmp_path / ".env").write_text(
        "LITELLM_DATABASE_URL=postgresql://litellm:x@db.invalid:5432/litellm\n"
        "PIPELINES_EA_BASE_URL=http://from-dotenv.invalid:8000\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = _settings()

    assert settings.ea_base_url == "http://host.docker.internal:8000"
