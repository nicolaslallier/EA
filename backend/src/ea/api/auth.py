"""Who is calling the REST API — the dependency every router but `health` carries.

It sets the caller the services read (`services/caller.py`) and nothing else:
what the caller may do is decided there, never here. The value is not reset
after the request: uvicorn serves each request in its own task, and the context
goes with it. See docs/adr/0031.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ea.core.config import Settings
from ea.domain.auth import LOCAL_DEVELOPER, Caller
from ea.domain.errors import NotAuthenticatedError
from ea.domain.ports import AccessTokenVerifier
from ea.services.caller import current_caller

#: `auto_error=False` so a missing header reaches our own 401 envelope rather
#: than FastAPI's own.
bearer = HTTPBearer(auto_error=False, description="An access token of the Keycloak realm `ea`.")


def verifier_of(app: FastAPI) -> AccessTokenVerifier:
    verifier: AccessTokenVerifier | None = getattr(app.state, "token_verifier", None)
    if verifier is None:  # pragma: no cover - a misassembled app, not a request error
        msg = "no token verifier on the application — is auth_enabled on without one?"
        raise RuntimeError(msg)
    return verifier


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Caller:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        caller = LOCAL_DEVELOPER
    elif credentials is None:
        msg = "a bearer token is required"
        raise NotAuthenticatedError(msg)
    else:
        caller = await verifier_of(request.app).verify(credentials.credentials)
    current_caller.set(caller)
    return caller


Authenticated = Depends(authenticate)
CurrentCaller = Annotated[Caller, Depends(authenticate)]
