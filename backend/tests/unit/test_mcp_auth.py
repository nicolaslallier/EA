"""The MCP side of a bearer token: the SDK's verifier and the caller a tool acts as."""

from __future__ import annotations

import pytest

from ea.core.config import Settings
from ea.mcp.auth import KeycloakTokenVerifier, auth_settings, caller_of
from tests.conftest import StaticVerifier, a_reader, an_editor


@pytest.mark.asyncio
async def test_a_known_token_becomes_an_access_token_carrying_the_caller() -> None:
    verifier = KeycloakTokenVerifier(StaticVerifier({"t": an_editor()}))

    token = await verifier.verify_token("t")

    assert token is not None
    assert caller_of(token) == an_editor()


@pytest.mark.asyncio
async def test_an_unknown_token_is_none_so_the_sdk_answers_401() -> None:
    assert await KeycloakTokenVerifier(StaticVerifier({})).verify_token("forged") is None


@pytest.mark.asyncio
async def test_a_reader_stays_a_reader() -> None:
    token = await KeycloakTokenVerifier(StaticVerifier({"t": a_reader()})).verify_token("t")
    assert token is not None and not caller_of(token).can_write


def test_the_resource_is_not_checked_against_the_url_since_the_audience_is_ea_api() -> None:
    """`validate_token_resource` left unset warns, and 3.0 turns it on: our
    tokens carry `aud=ea-api`, checked by the verifier, and no RFC 8707
    resource equal to `/mcp`'s URL — on, it would refuse every one of them."""
    settings = auth_settings(Settings(debug=True))

    assert settings.validate_token_resource is False
    assert str(settings.issuer_url) == Settings(debug=True).auth_issuer
    assert str(settings.resource_server_url) == Settings(debug=True).mcp_resource_url
