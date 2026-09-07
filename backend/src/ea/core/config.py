"""Application settings, read from the environment only.

No literal secret, DSN or key lives in this file — see `.env.example` for the
shape of a local environment.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a local `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="EA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "EA API"
    debug: bool = False

    host: str = "127.0.0.1"
    port: int = 8000

    # Explicit allowlist — never `*`, because the API is called with credentials.
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `A,B` from the environment as well as a real list."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _reject_wildcard(cls, value: list[str]) -> list[str]:
        if "*" in value:
            msg = "cors_origins must be an explicit allowlist, not a wildcard"
            raise ValueError(msg)
        return value


def get_settings() -> Settings:
    """Build the settings for this process."""
    return Settings()
