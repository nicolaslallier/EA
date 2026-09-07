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
