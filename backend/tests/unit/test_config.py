"""Settings are read from the environment only — never hard-coded."""

import pytest

from ea.core.config import Settings


def test_cors_origins_are_parsed_from_a_comma_separated_string() -> None:
    settings = Settings(cors_origins="http://localhost:5173, http://127.0.0.1:5173")

    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_reject_a_wildcard() -> None:
    """CORS is an explicit allowlist; `*` alongside credentials is a footgun."""
    with pytest.raises(ValueError, match="wildcard"):
        Settings(cors_origins="*")


def test_default_cors_origin_is_the_vite_dev_server() -> None:
    assert Settings().cors_origins == ["http://localhost:5173"]


def test_neo4j_connection_is_read_from_the_environment() -> None:
    settings = Settings(neo4j_uri="bolt://db:7687", neo4j_user="ea", neo4j_password="s3cret")

    assert settings.neo4j_uri == "bolt://db:7687"
    assert settings.neo4j_password.get_secret_value() == "s3cret"


def test_the_neo4j_password_never_appears_in_a_repr() -> None:
    """Settings end up in log lines and tracebacks; the password must not."""
    settings = Settings(neo4j_password="s3cret")

    assert "s3cret" not in repr(settings)


def test_an_empty_neo4j_password_is_refused_outside_debug() -> None:
    with pytest.raises(ValueError, match="neo4j_password"):
        Settings(debug=False, neo4j_password="")


def test_an_empty_neo4j_password_is_tolerated_in_debug() -> None:
    """A throwaway local container should not need a secret to be useful."""
    assert Settings(debug=True, neo4j_password="").neo4j_password.get_secret_value() == ""


def test_cors_origins_are_parsed_from_a_comma_separated_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.example` ships a bare string — reading it must not crash the app."""
    monkeypatch.setenv("EA_CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")

    assert Settings(debug=True).cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_a_single_origin_in_the_environment_stays_a_one_item_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EA_CORS_ORIGINS", "http://localhost:5173")

    assert Settings(debug=True).cors_origins == ["http://localhost:5173"]
