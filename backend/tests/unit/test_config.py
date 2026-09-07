"""Settings are read from the environment only — never hard-coded."""

from pathlib import Path

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
    """`_env_file=None`: a default is only a default with no local `.env` in play."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:5173"]


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


def test_the_committed_env_example_builds_settings() -> None:
    """`.env.example` is the documented onboarding path: it must actually load."""
    example = Path(__file__).parents[2] / ".env.example"

    settings = Settings(_env_file=example)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:5173"]


def test_postgres_is_off_until_something_stores_a_table_there() -> None:
    """Nothing relational exists yet; booting must not require a second database."""
    assert Settings(_env_file=None).postgres_enabled is False  # type: ignore[call-arg]


def test_the_postgres_connection_is_read_from_the_environment() -> None:
    settings = Settings(
        debug=True,
        postgres_host="db.internal",
        postgres_port=5433,
        postgres_user="ea",
        postgres_password="s3cret",
        postgres_database="audit",
    )

    assert (settings.postgres_host, settings.postgres_port) == ("db.internal", 5433)
    assert settings.postgres_database == "audit"
    assert settings.postgres_password.get_secret_value() == "s3cret"


def test_the_postgres_password_never_appears_in_a_repr() -> None:
    assert "s3cret" not in repr(Settings(debug=True, postgres_password="s3cret"))


def test_an_empty_postgres_password_is_refused_once_the_store_is_in_use() -> None:
    with pytest.raises(ValueError, match="postgres_password"):
        Settings(debug=False, neo4j_password="x", postgres_enabled=True, postgres_password="")


def test_an_empty_postgres_password_is_tolerated_in_debug() -> None:
    """The local container in `docker-compose.yml` is not a deployment."""
    settings = Settings(debug=True, postgres_enabled=True, postgres_password="")

    assert settings.postgres_password.get_secret_value() == ""


def test_a_disabled_store_asks_for_no_password_at_all() -> None:
    """A secret is required by *use*, not by the mere presence of a setting."""
    settings = Settings(debug=False, neo4j_password="x", postgres_enabled=False)

    assert settings.postgres_enabled is False


def test_the_api_listens_on_every_interface_by_default() -> None:
    """The stack is reached from other machines — the cluster, a phone, a peer."""
    assert Settings(_env_file=None).host == "0.0.0.0"  # type: ignore[call-arg]


def test_the_mcp_allowlist_does_not_follow_the_bind_address() -> None:
    """Binding every interface must not widen who may call `/mcp`.

    The MCP SDK turns DNS-rebinding protection on by itself only when it is
    served on a loopback host. Deriving the allowlist from `host` would
    therefore switch that protection *off* the moment the API binds 0.0.0.0 —
    on an unauthenticated write path onto the graph. It is its own setting.
    """
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.host == "0.0.0.0"
    assert settings.mcp_allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]


def test_the_mcp_allowlist_is_parsed_from_a_comma_separated_string() -> None:
    settings = Settings(debug=True, mcp_allowed_hosts="192.168.1.40:8000, localhost:*")

    assert settings.mcp_allowed_hosts == ["192.168.1.40:8000", "localhost:*"]


def test_the_mcp_allowlist_rejects_a_bare_wildcard() -> None:
    """`*` here is "any Host header", which is the protection switched off."""
    with pytest.raises(ValueError, match="wildcard"):
        Settings(debug=True, mcp_allowed_hosts="*")
