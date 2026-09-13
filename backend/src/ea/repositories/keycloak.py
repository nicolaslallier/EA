"""Keycloak's signing keys, and the check that turns a bearer token into a caller.

A repository in this package's sense: the adapter behind a port the domain
declared (`AccessTokenVerifier`), with the protocol kept out of everything
above it. The keys are fetched over `httpx` — `PyJWKClient` would block the
event loop — and verified by `pyjwt`. See docs/adr/0031.

**Only RS256.** The algorithm is fixed here and never read from the token: a
verifier that trusts the header accepts `none`, or an HS256 token "signed" with
the public key as the secret.

**An unknown `kid` refetches the key set, at most once per interval.** That is
how a key rotation is picked up without a restart, and the interval is what
stops a stream of forged `kid`s from turning every request into a call to
Keycloak.
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable
from typing import Any, Final

import httpx
import jwt

from ea.domain.auth import Caller
from ea.domain.errors import NotAuthenticatedError

logger = logging.getLogger(__name__)

ALGORITHMS: Final = ["RS256"]
REQUIRED_CLAIMS: Final = ["exp", "iat", "iss", "aud", "sub"]


class AuthServiceError(RuntimeError):
    """Keycloak was unreachable or answered something that is not a key set.

    Not a `DomainError`: nothing about the request was wrong. It is a 500 and a
    log line, like the embedding service's own unavailability.
    """


def http_client_for(ca_cert: str | None, timeout: float) -> httpx.AsyncClient:
    verify: ssl.SSLContext | bool = ssl.create_default_context(cafile=ca_cert) if ca_cert else True
    return httpx.AsyncClient(verify=verify, timeout=timeout)


def _refused(reason: str) -> NotAuthenticatedError:
    # The reason goes to the log; the client is told only that the token failed.
    logger.info("bearer token refused: %s", reason)
    return NotAuthenticatedError("the bearer token is not valid")


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
        refetch_interval_seconds: float = 30.0,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._http = http
        self._clock = clock
        self._interval = refetch_interval_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None

    @property
    def jwks_url(self) -> str:
        return f"{self._issuer}/protocol/openid-connect/certs"

    async def probe(self) -> None:
        await self._fetch()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def verify(self, token: str) -> Caller:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise _refused(f"unreadable header: {error}") from error
        if header.get("alg") not in ALGORITHMS:
            raise _refused(f"algorithm {header.get('alg')!r}")
        key = await self._key(str(header.get("kid")))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as error:
            raise _refused(str(error)) from error
        return _caller_of(claims)

    async def _key(self, kid: str) -> Any:
        if kid not in self._keys and self._may_refetch():
            await self._fetch()
        if kid not in self._keys:
            raise _refused(f"unknown key id {kid!r}")
        return self._keys[kid]

    def _may_refetch(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at >= self._interval

    async def _fetch(self) -> None:
        self._fetched_at = self._clock()
        try:
            response = await self._http.get(self.jwks_url)
            response.raise_for_status()
            entries = response.json()["keys"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            msg = f"cannot read the signing keys at {self.jwks_url}: {error}"
            raise AuthServiceError(msg) from error
        keys: dict[str, Any] = {}
        for entry in entries:
            if entry.get("use", "sig") != "sig" or entry.get("kty") != "RSA" or "kid" not in entry:
                continue
            keys[entry["kid"]] = jwt.PyJWK.from_dict(entry, algorithm="RS256").key
        self._keys = keys
        logger.info("signing keys read", extra={"action": "signing_keys_read", "count": len(keys)})


def _caller_of(claims: dict[str, Any]) -> Caller:
    access = claims.get("realm_access")
    roles = access.get("roles", []) if isinstance(access, dict) else []
    subject = str(claims["sub"])
    username = claims.get("preferred_username")
    return Caller(
        subject=subject,
        username=username if isinstance(username, str) else subject,
        roles=frozenset(role for role in roles if isinstance(role, str)),
    )
