"""The lifespan builds and probes the real token verifier when auth is on.

Mirrors how `tests/unit/test_reindex.py` covers `build_embedder`: the real
function runs and is wired into the app, but the HTTP call it makes never
leaves the process — the transport underneath it is a `MockTransport`.
"""

from __future__ import annotations

import httpx
import pytest

from ea.core.config import Settings
from ea.main import build_verifier, create_app
from ea.repositories.keycloak import JwtVerifier
from ea.services.architecture import ArchitectureService


def test_build_verifier_builds_a_jwt_verifier_from_settings() -> None:
    settings = Settings(
        debug=True, auth_issuer="https://keycloak.test/realms/ea", auth_audience="ea-api"
    )

    verifier = build_verifier(settings)

    assert isinstance(verifier, JwtVerifier)
    assert verifier.jwks_url == "https://keycloak.test/realms/ea/protocol/openid-connect/certs"


@pytest.mark.asyncio
async def test_the_lifespan_builds_and_probes_the_verifier_when_auth_is_on(
    monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
) -> None:
    """`auth_enabled` defaults to on, so a plain `create_app` must reach it."""
    fetches = 0

    def jwks(request: httpx.Request) -> httpx.Response:
        nonlocal fetches
        fetches += 1
        return httpx.Response(200, json={"keys": []})

    def fake_build_verifier(settings: Settings) -> JwtVerifier:
        return JwtVerifier(
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            http=httpx.AsyncClient(transport=httpx.MockTransport(jwks)),
        )

    monkeypatch.setattr("ea.main.build_verifier", fake_build_verifier)

    app = create_app(
        Settings(debug=True, postgres_enabled=False, embeddings_enabled=False, mcp_enabled=False),
        architecture_service=service,
    )

    async with app.router.lifespan_context(app):
        assert app.state.token_verifier is not None

    assert fetches == 1


@pytest.mark.asyncio
async def test_an_injected_verifier_is_never_rebuilt(
    monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
) -> None:
    """`create_app(verifier=...)` is the seam every other test uses — it must
    stop the lifespan from reaching Keycloak at all."""

    def fail(_settings: Settings) -> JwtVerifier:
        raise AssertionError("build_verifier must not run when a verifier was injected")

    monkeypatch.setattr("ea.main.build_verifier", fail)

    class Stub:
        async def verify(self, token: str) -> object:  # pragma: no cover - unused here
            raise NotImplementedError

    app = create_app(
        Settings(debug=True, postgres_enabled=False, embeddings_enabled=False, mcp_enabled=False),
        architecture_service=service,
        verifier=Stub(),  # type: ignore[arg-type]
    )

    async with app.router.lifespan_context(app):
        assert app.state.token_verifier is not None
