"""`/mcp` as a resource server of the realm `ea` — the same check as the REST API.

The SDK wants a `TokenVerifier` answering an `AccessToken` or `None`; ours
answers a `Caller`. This is the translation, and nothing more: the roles travel
in `claims`, and `speaking_plainly` turns them back into the caller the services
read. See docs/adr/0031.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from ea.domain.auth import LOCAL_DEVELOPER, Caller
from ea.domain.errors import NotAuthenticatedError
from ea.services.caller import acting_as

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from ea.core.config import Settings
    from ea.domain.ports import AccessTokenVerifier


class KeycloakTokenVerifier:
    """The SDK's `TokenVerifier` over our `AccessTokenVerifier`."""

    def __init__(self, verifier: AccessTokenVerifier) -> None:
        self._verifier = verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            caller = await self._verifier.verify(token)
        except NotAuthenticatedError:
            return None
        return AccessToken(
            token=token,
            client_id=caller.subject,
            scopes=[],
            subject=caller.subject,
            claims={"preferred_username": caller.username, "roles": sorted(caller.roles)},
        )


def caller_of(token: AccessToken) -> Caller:
    claims = token.claims or {}
    subject = token.subject or token.client_id
    return Caller(
        subject=subject,
        username=str(claims.get("preferred_username", subject)),
        roles=frozenset(str(role) for role in claims.get("roles", [])),
    )


def auth_settings(settings: Settings) -> AuthSettings:
    return AuthSettings(
        issuer_url=AnyHttpUrl(settings.auth_issuer),
        resource_server_url=AnyHttpUrl(settings.mcp_resource_url),
        # Our tokens name the audience `ea-api`, checked by the verifier; they
        # carry no RFC 8707 resource equal to this URL.
        validate_token_resource=False,
    )


class AsTheLocalDeveloper:
    """With auth off (debug only), every MCP call is `LOCAL_DEVELOPER` — as on REST.

    Set on the request's context, which the SDK hands to the handler of each
    message that request carried.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with acting_as(LOCAL_DEVELOPER):
            await self._app(scope, receive, send)
