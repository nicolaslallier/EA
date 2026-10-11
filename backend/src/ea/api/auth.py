"""Who is calling the REST API — the dependency every router but `health` carries.

It sets the caller the services read (`services/caller.py`) and nothing else:
what the caller may do is decided there, never here. The value is not reset
after the request: uvicorn serves each request in its own task, and the context
goes with it. See docs/adr/0032.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ea.core.config import Settings
from ea.domain.audit import AuditChannel
from ea.domain.auth import LOCAL_DEVELOPER, Caller
from ea.domain.errors import NotAuthenticatedError
from ea.domain.ports import AccessTokenVerifier
from ea.services.caller import current_caller, current_channel

#: `auto_error=False` so a missing header reaches our own 401 envelope rather
#: than FastAPI's own.
bearer = HTTPBearer(auto_error=False, description="An access token of the Keycloak realm `ea`.")


def verifier_of(app: FastAPI) -> AccessTokenVerifier:
    verifier: AccessTokenVerifier | None = getattr(app.state, "token_verifier", None)
    if verifier is None:  # pragma: no cover - a misassembled app, not a request error
        msg = "no token verifier on the application — is auth_enabled on without one?"
        raise RuntimeError(msg)
    return verifier


async def caller_of(app: FastAPI, token: str | None) -> Caller:
    """Who a bearer token says is calling — the one place a request is authenticated.

    The dependency below uses it, and so does any middleware that must know the caller
    before the body is read (`body_limit.py`): there is a single verification, never a copy.
    """
    settings: Settings = app.state.settings
    if not settings.auth_enabled:
        return LOCAL_DEVELOPER
    if token is None:
        msg = "a bearer token is required"
        raise NotAuthenticatedError(msg)
    return await verifier_of(app).verify(token)


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Caller:
    caller = await caller_of(request.app, credentials.credentials if credentials else None)
    current_caller.set(caller)
    # The door is said by the adapter that received the call, never by the caller.
    current_channel.set(AuditChannel.HTTP)
    return caller


Authenticated = Depends(authenticate)
CurrentCaller = Annotated[Caller, Depends(authenticate)]
