# Keycloak Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every call to EA (SPA, REST, `/mcp`, pipeline) is authenticated by the Keycloak realm `ea`, and every write additionally needs the realm role `ea-editor`, enforced in `services/`.

**Architecture:** EA is an OAuth2 resource server. One `JwtVerifier` (JWKS over `httpx`, signature by `pyjwt`) turns a bearer token into a domain `Caller`; the REST dependency and the MCP adapter both put that caller in a `ContextVar`; every public service method starts with `require_caller()` or `require_editor()`, which fail closed. The SPA logs in with authorization code + PKCE (`oidc-client-ts`, tokens in memory); the pipeline uses client credentials.

**Tech Stack:** FastAPI, `pyjwt[crypto]` (already locked via `mcp`), `httpx`, `mcp` 2.2 (`AuthSettings`, `TokenVerifier`), Vue 3 + `oidc-client-ts` 3.5, Keycloak 26.7.

**Spec:** `docs/superpowers/specs/2026-09-13-keycloak-authentication-design.md` — read it before your task.

## Global Constraints

- Realm `ea`; issuer `https://keycloak.famillelallier.net/realms/ea`; audience `ea-api`; role `ea-editor`; clients `ea-spa` (public, PKCE S256), `ea-mcp` (public, PKCE S256), `ea-pipelines` (confidential, service account).
- Only `RS256` tokens are accepted. `iss`, `aud`, `exp`, `iat`, `sub` are required.
- Fail closed: a service method reached with no caller raises `NotAuthenticatedError`; a write by a caller without `ea-editor` raises `NotAuthorisedError`.
- `EA_AUTH_ENABLED` defaults to `true`; `false` is refused by `Settings` unless `EA_DEBUG` is true.
- Tokens in the SPA live in memory (`InMemoryWebStorage`), never `localStorage`.
- No secret in any committed file; `.env.example` values for secrets stay blank.
- Follow `CLAUDE.md`: TDD, `ruff`, `mypy --strict`, ESLint type-aware, no hand-written TS mirror of a Pydantic model, error envelope `{error, detail}`.
- **Agents do not commit** in the EA worktree (several agents share it; the coordinator commits per task). Run only the test files of your task, plus the full suite of the side you touched at the end (`uv run pytest tests/unit tests/e2e -q` / `npm test -- --run` / pipelines `uv run pytest -q`), and report failures you did not cause rather than fixing other tasks' files.
- Do not touch files outside your task's **Files** list without saying so in your report.

## Waves (what can run in parallel)

| Wave | Tasks | Why this order |
|---|---|---|
| 1 | 1 (auth contract), 5 (SPA login plumbing), 7 (pipeline client credentials), 8 (Infra realm) | No shared files; 5/7/8 depend on nothing in EA's backend |
| 2 | 2 (JWT verifier), 3 (service guards) | Both consume Task 1 only |
| 3 | 4 (REST adapter + `/me` + OpenAPI regen) | Consumes 2 and 3; owns `main.py`, `tests/conftest.py` |
| 4 | 6 (MCP adapter), 9 (SPA identity + write controls) | 6 edits `main.py` after 4; 9 needs the regenerated client |
| 5 | 10 (deploy, ADR 0031, CLAUDE.md, env examples) | Documents the names every other task fixed |

---

### Task 1: Auth contract — `Caller`, errors, caller context, settings

**Files:**
- Create: `backend/src/ea/domain/auth.py`
- Modify: `backend/src/ea/domain/errors.py` (append two errors)
- Create: `backend/src/ea/services/caller.py`
- Modify: `backend/src/ea/core/config.py` (auth block + validator)
- Test: `backend/tests/unit/test_auth_contract.py` (create), `backend/tests/unit/test_config.py` (append)

**Interfaces:**
- Produces:
  - `ea.domain.auth.EDITOR_ROLE: Final = "ea-editor"`
  - `ea.domain.auth.Caller` — frozen dataclass `(subject: str, username: str, roles: frozenset[str] = frozenset())`, property `can_write -> bool`
  - `ea.domain.auth.LOCAL_DEVELOPER: Caller` (subject/username `"local-developer"`, roles `{EDITOR_ROLE}`)
  - `ea.domain.auth.SYSTEM: Caller` (subject/username `"ea-system"`, roles `{EDITOR_ROLE}`)
  - `ea.domain.errors.NotAuthenticatedError(DomainError)`, `ea.domain.errors.NotAuthorisedError(DomainError)`
  - `ea.services.caller.current_caller: ContextVar[Caller | None]`, `require_caller() -> Caller`, `require_editor() -> Caller`, `acting_as(caller) -> ContextManager[Caller]`
  - `Settings.auth_enabled: bool = True`, `auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"`, `auth_audience: str = "ea-api"`, `auth_ca_cert: str | None = None`, `auth_timeout_seconds: float = 5.0`, `mcp_resource_url: str = "http://127.0.0.1:8000/mcp"`

- [ ] **Step 1: Write the failing tests** — `backend/tests/unit/test_auth_contract.py`

```python
"""Who is calling, and what the services refuse when nobody is."""

from __future__ import annotations

import pytest

from ea.domain.auth import EDITOR_ROLE, LOCAL_DEVELOPER, SYSTEM, Caller
from ea.domain.errors import DomainError, NotAuthenticatedError, NotAuthorisedError
from ea.services.caller import acting_as, current_caller, require_caller, require_editor


def test_a_caller_with_the_editor_role_can_write() -> None:
    assert Caller("s", "alice", frozenset({EDITOR_ROLE})).can_write
    assert not Caller("s", "bob", frozenset({"offline_access"})).can_write


def test_the_built_in_callers_are_editors() -> None:
    assert LOCAL_DEVELOPER.can_write and SYSTEM.can_write


def test_both_refusals_are_domain_errors_so_both_adapters_translate_them() -> None:
    assert issubclass(NotAuthenticatedError, DomainError)
    assert issubclass(NotAuthorisedError, DomainError)


def test_nobody_calling_is_refused() -> None:
    token = current_caller.set(None)
    try:
        with pytest.raises(NotAuthenticatedError):
            require_caller()
        with pytest.raises(NotAuthenticatedError):
            require_editor()
    finally:
        current_caller.reset(token)


def test_a_reader_may_read_but_not_write() -> None:
    reader = Caller("s", "bob")
    with acting_as(reader):
        assert require_caller() is reader
        with pytest.raises(NotAuthorisedError, match=EDITOR_ROLE):
            require_editor()


def test_acting_as_puts_the_previous_caller_back() -> None:
    before = current_caller.get()
    with acting_as(SYSTEM):
        assert current_caller.get() is SYSTEM
    assert current_caller.get() is before
```

Append to `backend/tests/unit/test_config.py` (match the file's existing style for building `Settings`):

```python
def test_authentication_is_on_by_default() -> None:
    assert Settings(debug=True).auth_enabled is True


def test_authentication_cannot_be_turned_off_outside_debug() -> None:
    with pytest.raises(ValueError, match="auth_enabled"):
        Settings(debug=False, auth_enabled=False)


def test_debug_may_turn_authentication_off() -> None:
    assert Settings(debug=True, auth_enabled=False).auth_enabled is False
```

- [ ] **Step 2: Run to see them fail**

Run: `cd backend && uv run pytest tests/unit/test_auth_contract.py tests/unit/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: ea.domain.auth`, and the settings tests fail on the missing field.

- [ ] **Step 3: Implement**

`backend/src/ea/domain/auth.py`:

```python
"""Who is calling — the one fact about a request the services decide on.

Pure: no JWT, no Keycloak, no FastAPI. A token is turned into a `Caller` by
`repositories/keycloak.py`; what a caller may do is decided in `services/`.
See docs/adr/0031.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

#: The realm role that allows changing the catalogue. Reading needs none.
EDITOR_ROLE: Final = "ea-editor"


@dataclass(frozen=True, slots=True)
class Caller:
    """An authenticated principal: its stable id, a name to log, its realm roles."""

    subject: str
    username: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def can_write(self) -> bool:
        return EDITOR_ROLE in self.roles


#: Who calls when `EA_AUTH_ENABLED` is off — which `Settings` allows in debug only.
LOCAL_DEVELOPER: Final = Caller("local-developer", "local-developer", frozenset({EDITOR_ROLE}))

#: Who calls from an operator's script with no request behind it (`ea.reindex`).
SYSTEM: Final = Caller("ea-system", "ea-system", frozenset({EDITOR_ROLE}))
```

Append to `backend/src/ea/domain/errors.py`:

```python
class NotAuthenticatedError(DomainError):
    """No caller, or a token that does not prove one. Mapped to 401 / a ToolError."""


class NotAuthorisedError(DomainError):
    """A known caller asking for something its roles do not allow. Mapped to 403."""
```

`backend/src/ea/services/caller.py`:

```python
"""The caller of the use case being run, and the two checks every use case makes.

A `ContextVar`, like the request id of docs/adr/0021: the adapter that knows who
is calling (the REST dependency, the MCP decorator, `ea.reindex`) sets it, and
the service reads it. Nobody set it means nobody is calling — refused, so a
forgotten wire-up is a 401 in a test rather than an open door. See docs/adr/0031.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ea.domain.auth import EDITOR_ROLE, Caller
from ea.domain.errors import NotAuthenticatedError, NotAuthorisedError

current_caller: ContextVar[Caller | None] = ContextVar("ea_current_caller", default=None)


def require_caller() -> Caller:
    caller = current_caller.get()
    if caller is None:
        msg = "authentication is required"
        raise NotAuthenticatedError(msg)
    return caller


def require_editor() -> Caller:
    caller = require_caller()
    if not caller.can_write:
        msg = f"changing the catalogue needs the {EDITOR_ROLE} role"
        raise NotAuthorisedError(msg)
    return caller


@contextmanager
def acting_as(caller: Caller) -> Iterator[Caller]:
    token = current_caller.set(caller)
    try:
        yield caller
    finally:
        current_caller.reset(token)
```

In `backend/src/ea/core/config.py`, add a block after the MCP settings (keep the file's comment style — explain *why*):

```python
    # --- Authentication, by the Keycloak realm `ea` — see docs/adr/0031 ------
    # On by default, like the two stores: an API that anyone on the LAN can
    # write to is the state this replaces. Off is accepted in debug only.
    auth_enabled: bool = True
    auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"
    auth_audience: str = "ea-api"
    #: The Infra CA. From the Mac, keycloak.famillelallier.net resolves to
    #: 127.0.0.1 behind a certificate the system trust store does not know.
    auth_ca_cert: str | None = None
    auth_timeout_seconds: float = 5.0
    #: The resource identifier `/mcp` publishes (RFC 9728).
    mcp_resource_url: str = "http://127.0.0.1:8000/mcp"
```

and a validator beside the password ones:

```python
    @model_validator(mode="after")
    def _authentication_off_only_in_debug(self) -> "Settings":
        """Turning auth off hands every write to whoever reaches the port."""
        if not self.auth_enabled and not self.debug:
            msg = "auth_enabled may only be false when debug is on"
            raise ValueError(msg)
        return self
```

- [ ] **Step 4: Run to see them pass**

Run: `cd backend && uv run pytest tests/unit/test_auth_contract.py tests/unit/test_config.py -q && uv run mypy src && uv run ruff check src tests`
Expected: PASS, mypy and ruff clean.

- [ ] **Step 5: Report** the files changed (the coordinator commits: `feat(auth): appelant, erreurs et réglages de l'authentification`).

---

### Task 2: `JwtVerifier` — a bearer token becomes a `Caller`

**Files:**
- Create: `backend/src/ea/repositories/keycloak.py`
- Modify: `backend/src/ea/domain/ports.py` (append `AccessTokenVerifier` protocol)
- Modify: `backend/pyproject.toml` (add `"pyjwt[crypto]>=2.10"` to `dependencies`, with a one-line comment), `backend/uv.lock` (`uv lock`)
- Test: `backend/tests/unit/test_keycloak_verifier.py`

**Interfaces:**
- Consumes: `Caller`, `NotAuthenticatedError` (Task 1).
- Produces:
  - `ea.domain.ports.AccessTokenVerifier` — `Protocol` with `async def verify(self, token: str) -> Caller`
  - `ea.repositories.keycloak.AuthServiceError(RuntimeError)` — Keycloak unreachable or answered nonsense
  - `ea.repositories.keycloak.JwtVerifier(*, issuer: str, audience: str, http: httpx.AsyncClient, clock: Callable[[], float] = time.monotonic, refetch_interval_seconds: float = 30.0)` with `jwks_url: str` property (`f"{issuer}/protocol/openid-connect/certs"`), `async probe() -> None`, `async verify(token: str) -> Caller`, `async aclose() -> None`
  - `ea.repositories.keycloak.http_client_for(ca_cert: str | None, timeout: float) -> httpx.AsyncClient` (verify = `ssl.create_default_context(cafile=ca_cert)` when given, else `True`)

- [ ] **Step 1: Write the failing tests** — `backend/tests/unit/test_keycloak_verifier.py`

```python
"""Turning a Keycloak access token into a caller — and every way to refuse one.

The signing key is generated here and the JWKS is served by `MockTransport`, so
no test reaches Keycloak (tests/conftest.py refuses the socket anyway).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ea.domain.errors import NotAuthenticatedError
from ea.repositories.keycloak import AuthServiceError, JwtVerifier

ISSUER = "https://keycloak.test/realms/ea"
AUDIENCE = "ea-api"


def a_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = a_key()


def jwk(private: rsa.RSAPrivateKey, kid: str, use: str = "sig") -> dict[str, Any]:
    entry: dict[str, Any] = RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    return {**entry, "kid": kid, "use": use, "alg": "RS256"}


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": ["ea-api", "account"],
        "sub": "7f3c",
        "preferred_username": "alice",
        "realm_access": {"roles": ["ea-editor", "offline_access"]},
        "iat": now,
        "exp": now + 300,
        **overrides,
    }


def token(private: rsa.RSAPrivateKey = KEY, kid: str = "k1", **overrides: Any) -> str:
    return jwt.encode(claims(**overrides), private, algorithm="RS256", headers={"kid": kid})


class Keycloak:
    """A JWKS endpoint whose key set a test can rotate, counting the fetches."""

    def __init__(self, *keys: dict[str, Any], status: int = 200) -> None:
        self.keys = list(keys)
        self.status = status
        self.fetches = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url == f"{ISSUER}/protocol/openid-connect/certs"
        self.fetches += 1
        return httpx.Response(self.status, json={"keys": self.keys})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def verifier(keycloak: Keycloak, clock: Clock | None = None) -> JwtVerifier:
    return JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        http=httpx.AsyncClient(transport=httpx.MockTransport(keycloak)),
        clock=clock or Clock(),
    )


@pytest.mark.asyncio
class TestAValidToken:
    async def test_names_the_caller_and_its_realm_roles(self) -> None:
        caller = await verifier(Keycloak(jwk(KEY, "k1"))).verify(token())

        assert caller.subject == "7f3c"
        assert caller.username == "alice"
        assert caller.roles == frozenset({"ea-editor", "offline_access"})
        assert caller.can_write

    async def test_without_realm_roles_is_a_reader(self) -> None:
        caller = await verifier(Keycloak(jwk(KEY, "k1"))).verify(token(realm_access=None))

        assert not caller.can_write

    async def test_keys_are_fetched_once_and_reused(self) -> None:
        keycloak = Keycloak(jwk(KEY, "k1"))
        check = verifier(keycloak)

        await check.verify(token())
        await check.verify(token())

        assert keycloak.fetches == 1

    async def test_an_encryption_key_in_the_set_is_ignored(self) -> None:
        other = a_key()
        keycloak = Keycloak(jwk(other, "enc1", use="enc"), jwk(KEY, "k1"))

        assert (await verifier(keycloak).verify(token())).username == "alice"


@pytest.mark.asyncio
class TestRefused:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"exp": int(time.time()) - 60},
            {"aud": "account"},
            {"iss": "https://keycloak.test/realms/jarvis"},
            {"sub": None},
        ],
        ids=["expired", "another-audience", "another-realm", "no-subject"],
    )
    async def test_a_token_that_does_not_prove_a_caller(self, overrides: dict[str, Any]) -> None:
        bad = {k: v for k, v in claims(**overrides).items() if v is not None}
        forged = jwt.encode(bad, KEY, algorithm="RS256", headers={"kid": "k1"})

        with pytest.raises(NotAuthenticatedError):
            await verifier(Keycloak(jwk(KEY, "k1"))).verify(forged)

    async def test_a_symmetric_signature_even_with_the_public_key_as_secret(self) -> None:
        forged = jwt.encode(claims(), "not-the-realm-key", algorithm="HS256", headers={"kid": "k1"})

        with pytest.raises(NotAuthenticatedError):
            await verifier(Keycloak(jwk(KEY, "k1"))).verify(forged)

    async def test_an_unsigned_token(self) -> None:
        forged = jwt.encode(claims(), None, algorithm="none", headers={"kid": "k1"})

        with pytest.raises(NotAuthenticatedError):
            await verifier(Keycloak(jwk(KEY, "k1"))).verify(forged)

    async def test_a_token_signed_by_another_key(self) -> None:
        with pytest.raises(NotAuthenticatedError):
            await verifier(Keycloak(jwk(KEY, "k1"))).verify(token(a_key(), kid="k1"))

    async def test_something_that_is_not_a_jwt(self) -> None:
        with pytest.raises(NotAuthenticatedError):
            await verifier(Keycloak(jwk(KEY, "k1"))).verify("not-a-token")


@pytest.mark.asyncio
class TestKeyRotation:
    async def test_an_unknown_key_id_refetches_the_set_once(self) -> None:
        rotated = a_key()
        keycloak = Keycloak(jwk(KEY, "k1"))
        clock = Clock()
        check = verifier(keycloak, clock)
        await check.verify(token())

        keycloak.keys.append(jwk(rotated, "k2"))
        clock.now += 31

        assert (await check.verify(token(rotated, kid="k2"))).username == "alice"
        assert keycloak.fetches == 2

    async def test_unknown_key_ids_cannot_make_it_hammer_keycloak(self) -> None:
        keycloak = Keycloak(jwk(KEY, "k1"))
        check = verifier(keycloak)
        await check.verify(token())

        for kid in ("x1", "x2", "x3"):
            with pytest.raises(NotAuthenticatedError):
                await check.verify(token(a_key(), kid=kid))

        assert keycloak.fetches == 1


@pytest.mark.asyncio
class TestProbe:
    async def test_reads_the_keys_at_boot(self) -> None:
        keycloak = Keycloak(jwk(KEY, "k1"))

        await verifier(keycloak).probe()

        assert keycloak.fetches == 1

    async def test_an_unreachable_or_failing_keycloak_stops_the_boot(self) -> None:
        with pytest.raises(AuthServiceError):
            await verifier(Keycloak(status=503)).probe()
```

Note on the throttle test: the first fetch happens at `clock.now == 1000`; the `x1..x3` lookups happen at the same instant, inside the 30 s interval, so no refetch. The rotation test advances the clock past the interval.

- [ ] **Step 2: Run to see them fail**

Run: `cd backend && uv run pytest tests/unit/test_keycloak_verifier.py -q`
Expected: FAIL — `ModuleNotFoundError: ea.repositories.keycloak`.

- [ ] **Step 3: Implement** `backend/src/ea/repositories/keycloak.py`

```python
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
        logger.info("signing keys read", extra={"status": len(keys)})


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
```

Append to `backend/src/ea/domain/ports.py` (import `Caller` from `ea.domain.auth`, keep the module's `Protocol` style):

```python
class AccessTokenVerifier(Protocol):
    """Whatever proves who a bearer token belongs to — Keycloak's JWKS in production."""

    async def verify(self, token: str) -> Caller:
        """The caller the token proves, or `NotAuthenticatedError`."""
        ...
```

Add `"pyjwt[crypto]>=2.10",` to `backend/pyproject.toml` `dependencies` (alphabetical, comment: `# Verifies Keycloak access tokens — see docs/adr/0031. Already locked via mcp.`), then `cd backend && uv lock` (it must not upgrade anything else; check `git diff --stat uv.lock`).

- [ ] **Step 4: Run to see them pass**

Run: `cd backend && uv run pytest tests/unit/test_keycloak_verifier.py -q && uv run mypy src && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. If `jwt.PyJWK.from_dict` signature differs in pyjwt 2.13, use `jwt.PyJWK(entry, algorithm="RS256").key`. If `logger.info(..., extra={"status": ...})` is flagged by the logging tests, use `extra={"action": "signing_keys_read"}`.

- [ ] **Step 5: Report** (commit: `feat(auth): vérifier les jetons d'accès Keycloak`).

---

### Task 3: Every service method checks its caller

**Files:**
- Modify: `backend/src/ea/services/architecture.py`, `backend/src/ea/services/documents.py`, `backend/src/ea/services/ipam.py`
- Modify: `backend/src/ea/reindex.py`
- Modify: `backend/tests/conftest.py` (autouse editor fixture + `an_editor()` / `a_reader()` helpers)
- Test: `backend/tests/unit/test_service_guards.py` (create); `backend/tests/unit/test_reindex.py` (append)

**Interfaces:**
- Consumes: `require_caller`, `require_editor`, `acting_as`, `current_caller` (Task 1); `Caller`, `EDITOR_ROLE`, `SYSTEM`.
- Produces (in `tests/conftest.py`): `an_editor(**overrides) -> Caller`, `a_reader(**overrides) -> Caller`, autouse fixture `_an_editor_is_calling` (yields the editor), fixture `nobody_calling` (sets `current_caller` to `None` for the test).

Classification (exact — the test enforces it):

| Service | Writes → `require_editor()` | Reads → `require_caller()` |
|---|---|---|
| `ArchitectureService` | `create_element`, `update_element`, `delete_element`, `connect`, `disconnect` | `get_element`, `list_elements`, `count_elements`, `get_relationship`, `list_relationships`, `relations_of`, `neighbourhood`, `impact_of` |
| `DocumentService` | `attach`, `attach_text`, `revise`, `revise_text`, `discard`, `reindex_all` | `get`, `list_for_element`, `search` |
| `IpamService` | `declare_network`, `assign_address`, `allocate_next`, `release_address` | `list_networks`, `read_network`, `locate`, `list_addresses` |

- [ ] **Step 1: Write the failing tests** — `backend/tests/unit/test_service_guards.py`

```python
"""Every use case asks who is calling before it does anything.

Checked on the source rather than by calling thirty methods with invented
arguments: the first statement of each public coroutine must be the check, and
a new method without one fails here. Two behavioural tests prove the check does
what it says.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import NotAuthenticatedError, NotAuthorisedError
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.documents import DocumentService
from ea.services.ipam import IpamService
from tests.conftest import a_reader

WRITES = {
    ArchitectureService: {"create_element", "update_element", "delete_element", "connect", "disconnect"},
    DocumentService: {"attach", "attach_text", "revise", "revise_text", "discard", "reindex_all"},
    IpamService: {"declare_network", "assign_address", "allocate_next", "release_address"},
}


def first_call(method: object) -> str | None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))  # type: ignore[arg-type]
    body = tree.body[0].body  # type: ignore[attr-defined]
    statements = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not statements:
        return None
    first = statements[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
        func = first.value.func
        return func.id if isinstance(func, ast.Name) else None
    return None


def public_coroutines(service: type) -> list[str]:
    return [
        name
        for name, member in vars(service).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    ]


@pytest.mark.parametrize("service", list(WRITES), ids=lambda s: s.__name__)
def test_every_public_use_case_starts_by_checking_its_caller(service: type) -> None:
    for name in public_coroutines(service):
        expected = "require_editor" if name in WRITES[service] else "require_caller"
        assert first_call(getattr(service, name)) == expected, f"{service.__name__}.{name}"


@pytest.mark.parametrize("service", list(WRITES), ids=lambda s: s.__name__)
def test_the_write_list_names_only_methods_that_exist(service: type) -> None:
    assert WRITES[service] <= set(public_coroutines(service))


@pytest.mark.asyncio
async def test_nobody_reads_nothing(service: ArchitectureService, nobody_calling: None) -> None:
    with pytest.raises(NotAuthenticatedError):
        await service.list_elements(__import__("ea.domain.ports", fromlist=["ElementFilter"]).ElementFilter())


@pytest.mark.asyncio
async def test_a_reader_cannot_create(service: ArchitectureService) -> None:
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
```

(Replace the `__import__` with a normal `from ea.domain.ports import ElementFilter` import; check `ElementFilter()` constructs with defaults, else pass the defaults the other tests use.)

Append to `backend/tests/unit/test_reindex.py` a test that the reindex runs as `SYSTEM` — drive `reindex()` the way the existing tests do and assert that the `DocumentService.reindex_all` it reaches sees `current_caller.get() is SYSTEM` (e.g. monkeypatch `DocumentService.reindex_all` with a coroutine that records `current_caller.get()` and returns `0`), while the test itself sets `nobody_calling`.

- [ ] **Step 2: Add the fixtures** to `backend/tests/conftest.py` (near `FIXED_NOW`; keep its docstring style):

```python
from ea.domain.auth import EDITOR_ROLE, Caller
from ea.services.caller import acting_as, current_caller


def an_editor(**overrides: object) -> Caller:
    fields: dict[str, object] = {"subject": "editor-1", "username": "editor", "roles": frozenset({EDITOR_ROLE})}
    return Caller(**{**fields, **overrides})  # type: ignore[arg-type]


def a_reader(**overrides: object) -> Caller:
    return an_editor(**{"subject": "reader-1", "username": "reader", "roles": frozenset(), **overrides})


@pytest.fixture(autouse=True)
def _an_editor_is_calling() -> Iterator[Caller]:
    """The services refuse a call nobody makes (docs/adr/0031).

    A suite about what a use case *does* is not about who may run it, so an
    editor calls unless a test says otherwise — `nobody_calling`, `acting_as`.
    """
    with acting_as(an_editor()) as caller:
        yield caller


@pytest.fixture
def nobody_calling() -> Iterator[None]:
    token = current_caller.set(None)
    try:
        yield
    finally:
        current_caller.reset(token)
```

- [ ] **Step 3: Run to see the guard tests fail**

Run: `cd backend && uv run pytest tests/unit/test_service_guards.py -q`
Expected: FAIL — first call of `create_element` is `validate_ipam_properties`, not `require_editor`; `test_nobody_reads_nothing` does not raise. **Also confirm** the autouse `ContextVar` reaches async tests: `uv run pytest tests/unit/test_architecture_service.py -q` must stay green once guards exist (Step 5). If pytest-asyncio runs the test in a context that does not see the fixture's value, change `_an_editor_is_calling` to set the var with `current_caller.set(...)` inside an `async` autouse fixture instead, and say so in the report.

- [ ] **Step 4: Implement** — in each method from the table, the first statement after the docstring becomes `require_editor()` or `require_caller()` (import from `ea.services.caller`). Example:

```python
    async def create_element(self, *, element_type: ElementType, name: str, ...) -> Element:
        require_editor()
        properties = validate_ipam_properties(element_type, properties)
        ...
```

In `backend/src/ea/reindex.py`, wrap the call: `with acting_as(SYSTEM): return await service.reindex_all()` (imports `SYSTEM` from `ea.domain.auth`, `acting_as` from `ea.services.caller`), with a one-line comment: an operator's script has no request, and runs as `SYSTEM`.

- [ ] **Step 5: Run to see everything pass**

Run: `cd backend && uv run pytest tests/unit -q && uv run mypy src && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. (`tests/e2e` is Task 4's; do not fix it here, but report whether it is green — with auth still unwired in the adapters it should be, since the autouse editor is visible to `ASGITransport` requests. If it is not, report which tests fail.)

- [ ] **Step 6: Report** (commit: `feat(auth): chaque cas d'usage vérifie son appelant`).

---

### Task 4: REST adapter — bearer dependency, 401/403, `GET /me`, OpenAPI

**Files:**
- Create: `backend/src/ea/api/auth.py`, `backend/src/ea/api/me.py`
- Modify: `backend/src/ea/api/errors.py`, `backend/src/ea/api/schemas.py` (append `MeRead`), `backend/src/ea/main.py` (`build_verifier`, lifespan, `create_app(verifier=...)`, router includes)
- Modify: `backend/tests/conftest.py` (append `StaticVerifier`)
- Modify: every test that calls `create_app(Settings(...))` without testing auth — add `auth_enabled=False` (grep `create_app(` under `backend/tests`)
- Test: `backend/tests/e2e/test_auth_api.py` (create)
- Regenerate: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (`make openapi` from the repo root)

**Interfaces:**
- Consumes: `JwtVerifier`, `http_client_for`, `AuthServiceError` (Task 2); `AccessTokenVerifier`; `current_caller`, `LOCAL_DEVELOPER`, both errors (Task 1).
- Produces:
  - `ea.api.auth.authenticate(request, credentials) -> Caller` (async FastAPI dependency; sets `current_caller`), `ea.api.auth.Authenticated = Depends(authenticate)`, `ea.api.auth.CurrentCaller = Annotated[Caller, Depends(authenticate)]`, `ea.api.auth.verifier_of(app) -> AccessTokenVerifier`
  - `ea.api.schemas.MeRead(BaseModel)`: `username: str`, `can_write: bool`
  - `GET /me -> MeRead` (tag `auth`)
  - `ea.main.build_verifier(settings) -> JwtVerifier`
  - `ea.main.create_app(..., verifier: AccessTokenVerifier | None = None)`; `app.state.token_verifier`
  - `tests.conftest.StaticVerifier(tokens: dict[str, Caller])` — `verify` returns the mapped caller or raises `NotAuthenticatedError`
  - OpenAPI: security scheme `HTTPBearer` on every route but `/health`; response `401`/`403` use `ErrorResponse`

- [ ] **Step 1: Write the failing tests** — `backend/tests/e2e/test_auth_api.py`

```python
"""The REST API refuses whoever cannot show a token, and writes for editors only."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi.routing import APIRoute

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_editor

PUBLIC = {"/health"}
EDITOR, READER = "editor-token", "reader-token"


def an_app(service: ArchitectureService):  # type: ignore[no-untyped-def]
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    return create_app(Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier)


@pytest_asyncio.fixture
async def client(service: ArchitectureService, nobody_calling: None) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=an_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_every_route_but_health_wants_a_token(
    service: ArchitectureService, client: httpx.AsyncClient
) -> None:
    for route in an_app(service).routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC:
            continue
        path = re.sub(r"\{[^}]+\}", str(uuid4()), route.path)
        for method in route.methods:
            response = await client.request(method, path)
            assert response.status_code == 401, f"{method} {route.path} -> {response.status_code}"
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json()["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_health_stays_public(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_an_invalid_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/elements", headers=bearer("forged"))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_reader_reads(client: httpx.AsyncClient) -> None:
    assert (await client.get("/elements", headers=bearer(READER))).status_code == 200


@pytest.mark.asyncio
async def test_a_reader_cannot_write(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": "Billing"}, headers=bearer(READER)
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.asyncio
async def test_an_editor_writes(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": "Billing"}, headers=bearer(EDITOR)
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_me_says_who_and_whether_they_may_write(client: httpx.AsyncClient) -> None:
    assert (await client.get("/me", headers=bearer(READER))).json() == {"username": "reader", "can_write": False}
    assert (await client.get("/me", headers=bearer(EDITOR))).json() == {"username": "editor", "can_write": True}


@pytest.mark.asyncio
async def test_with_auth_off_the_local_developer_calls(service: ArchitectureService, nobody_calling: None) -> None:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/me")).json() == {"username": "local-developer", "can_write": True}
```

Add to `backend/tests/conftest.py`:

```python
class StaticVerifier:
    """Tokens known in advance — the `AccessTokenVerifier` of every API test."""

    def __init__(self, tokens: dict[str, Caller]) -> None:
        self.tokens = tokens

    async def verify(self, token: str) -> Caller:
        try:
            return self.tokens[token]
        except KeyError:
            raise NotAuthenticatedError("the bearer token is not valid") from None
```

Also add a unit test in `backend/tests/unit/test_openapi.py` (or wherever the schema is asserted) that `/elements` declares `security: [{"HTTPBearer": []}]` and `/health` declares none.

- [ ] **Step 2: Run to see them fail**

Run: `cd backend && uv run pytest tests/e2e/test_auth_api.py -q`
Expected: FAIL — `create_app()` has no `verifier` argument.

- [ ] **Step 3: Implement**

`backend/src/ea/api/auth.py`:

```python
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
#: than FastAPI's 403.
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
```

`backend/src/ea/api/me.py`:

```python
"""Who the SPA is talking to, and whether it should offer to write.

The SPA asks rather than reading the token: the API decides, and this is the
same decision the services make. See docs/adr/0031.
"""

from fastapi import APIRouter

from ea.api.auth import CurrentCaller
from ea.api.schemas import ErrorResponse, MeRead

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeRead, responses={401: {"model": ErrorResponse}})
async def read_me(caller: CurrentCaller) -> MeRead:
    return MeRead(username=caller.username, can_write=caller.can_write)
```

`backend/src/ea/api/schemas.py` — append:

```python
class MeRead(BaseModel):
    """The caller, as far as the SPA needs to know it."""

    username: str
    can_write: bool
```

`backend/src/ea/api/errors.py` — add to `_STATUS`:

```python
    # --- Authentication (docs/adr/0031) ---
    NotAuthenticatedError: (status.HTTP_401_UNAUTHORIZED, "unauthenticated"),
    NotAuthorisedError: (status.HTTP_403_FORBIDDEN, "forbidden"),
```

and in `_handle_domain_error`, add the challenge header for a 401:

```python
    headers = {"WWW-Authenticate": "Bearer"} if http_status == status.HTTP_401_UNAUTHORIZED else None
    return JSONResponse(status_code=http_status, content={"error": code, "detail": str(error)}, headers=headers)
```

`backend/src/ea/main.py`:

1. `build_verifier(settings) -> JwtVerifier` next to `build_embedder`:
   ```python
   def build_verifier(settings: Settings) -> JwtVerifier:
       """Keycloak's key set for the realm `ea`, reached through the Infra CA when one is given."""
       return JwtVerifier(
           issuer=settings.auth_issuer,
           audience=settings.auth_audience,
           http=http_client_for(settings.auth_ca_cert, settings.auth_timeout_seconds),
       )
   ```
2. `_lifespan`: before the stores, if `settings.auth_enabled and getattr(app.state, "token_verifier", None) is None`: build, `stack.push_async_callback(verifier.aclose)`, `await verifier.probe()`, `app.state.token_verifier = verifier`, `logger.info("tokens verified against %s", settings.auth_issuer)`. If `not settings.auth_enabled`: `logger.warning("authentication is off: every caller is the local developer, with the editor role")`. Add `"auth": settings.auth_enabled` to the starting log's `extra`.
3. `create_app(..., verifier: AccessTokenVerifier | None = None)`: `if verifier is not None: app.state.token_verifier = verifier` (document it in the docstring like the other doubles).
4. Routers: `app.include_router(health_router)`; every other router — `metamodel_router`, `architecture_router`, `documents_router`, `ipam_router`, and the new `me_router` — with `dependencies=[Authenticated]`.

- [ ] **Step 4: Keep the existing suites meaning what they meant**

Add `auth_enabled=False` to the `Settings(...)` of every existing `create_app` call in `backend/tests` (e.g. `tests/e2e/test_architecture_api.py`'s `create_app(Settings(debug=True), ...)` → `Settings(debug=True, auth_enabled=False)`, and the `an_app` helper in `tests/e2e/test_mcp_endpoint.py`). `ea/openapi.py` keeps `Settings(debug=True)`: it never enters the lifespan, and the dependency is attached whatever the setting, so the document is the same.

- [ ] **Step 5: Run everything**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q --cov=ea && uv run mypy src migrations && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, coverage ≥ 90 %.

- [ ] **Step 6: Regenerate the contract**

Run (repo root): `make openapi && make openapi-check`
Expected: `backend/openapi.json` gains `securitySchemes.HTTPBearer`, `/me`, `MeRead`; `frontend/src/api/schema.d.ts` follows; the check is green. Then `cd frontend && npm run typecheck`.

- [ ] **Step 7: Report** (commit: `feat(auth): l'API REST exige un jeton, /me dit qui écrit`).

---

### Task 5: SPA — log in with Keycloak, send the token

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (`npm install oidc-client-ts@^3.5.0`)
- Create: `frontend/src/lib/auth.ts`, `frontend/src/router/AuthCallback.vue`
- Modify: `frontend/src/lib/api.ts` (bearer + 401 middleware), `frontend/src/router/index.ts` (callback route + guard), `frontend/src/vite-env.d.ts`, `frontend/.env.example`, `frontend/tests/setup.ts` (mock `lib/auth`)
- Check (modify only if it sets a CSP): `frontend/nginx.conf` — `connect-src` must allow `https://keycloak.famillelallier.net`
- Test: `frontend/tests/auth.spec.ts` (create), `frontend/tests/api.spec.ts` (append), `frontend/tests/router.spec.ts` (append)

**Interfaces:**
- Produces:
  - `lib/auth.ts`: `AUTH_AUTHORITY: string`, `AUTH_CLIENT_ID: string`, `CALLBACK_PATH = '/auth/callback'`, `createUserManager(origin: string): UserManager`, `accessToken(): Promise<string | null>`, `signIn(returnTo: string): Promise<void>`, `completeSignIn(): Promise<string>` (resolves to the in-app path to return to), `signOut(): Promise<void>`, `safeReturnPath(value: unknown): string`
  - `router/index.ts`: `createAppRouter(history?: RouterHistory, gate?: Gate)` where `type Gate = { accessToken: () => Promise<string | null>; signIn: (returnTo: string) => Promise<void> }`; route `{ path: CALLBACK_PATH, name: 'auth-callback', meta: { public: true } }`

- [ ] **Step 1: Install** `cd frontend && npm install oidc-client-ts@^3.5.0` (a runtime dependency; it is recorded in ADR 0031 by Task 10).

- [ ] **Step 2: Write the failing tests**

`frontend/tests/auth.spec.ts` — this file must **un-mock** `lib/auth` (`vi.unmock('../src/lib/auth')` at the top) since `setup.ts` mocks it for everyone else:

```ts
import { InMemoryWebStorage } from 'oidc-client-ts'
import { describe, expect, it, vi } from 'vitest'

vi.unmock('../src/lib/auth')

const { CALLBACK_PATH, createUserManager, safeReturnPath } = await import('../src/lib/auth')

describe('the login client', () => {
  it('returns to this origin after Keycloak', () => {
    const manager = createUserManager('http://192.168.1.50:5173')
    expect(manager.settings.redirect_uri).toBe(`http://192.168.1.50:5173${CALLBACK_PATH}`)
    expect(manager.settings.response_type).toBe('code')
    expect(manager.settings.client_id).toBe('ea-spa')
  })

  it('keeps the tokens in memory, never in localStorage', () => {
    const manager = createUserManager('http://localhost:5173')
    // `userStore` is a WebStorageStateStore; its backing store must be the in-memory one.
    const store = (manager.settings.userStore as unknown as { _store: unknown })._store
    expect(store).toBeInstanceOf(InMemoryWebStorage)
  })
})

describe('where a login returns', () => {
  it.each([
    ['/elements?element=42', '/elements?element=42'],
    ['https://evil.example/', '/'],
    ['//evil.example/', '/'],
    [undefined, '/'],
    [42, '/'],
  ])('%s → %s', (value, expected) => {
    expect(safeReturnPath(value)).toBe(expected)
  })
})
```

(If `_store` is not the field name in oidc-client-ts 3.5, assert through the public behaviour instead: `await manager.settings.userStore.set('k', 'v')` then `expect(localStorage.getItem('k')).toBeNull()`.)

Append to `frontend/tests/api.spec.ts`:

```ts
import * as auth from '../src/lib/auth'

describe('who the API is told is calling', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the access token as a bearer', async () => {
    vi.mocked(auth.accessToken).mockResolvedValue('tok-1')
    const fetch = vi.fn(() => Promise.resolve(new Response('{"status":"ok"}', { status: 200 })))
    vi.stubGlobal('fetch', fetch)

    await api.GET('/health')

    const request = fetch.mock.calls[0][0] as Request
    expect(request.headers.get('Authorization')).toBe('Bearer tok-1')
  })

  it('starts a login, returning here, when the API says 401', async () => {
    vi.mocked(auth.accessToken).mockResolvedValue('expired')
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"error":"unauthenticated"}', { status: 401 }))))

    await api.GET('/elements')

    expect(auth.signIn).toHaveBeenCalledWith(expect.stringMatching(/^\//))
  })
})
```

Append to `frontend/tests/router.spec.ts`:

```ts
describe('the login gate', () => {
  it('sends a visitor without a token to Keycloak, remembering where they were going', async () => {
    const signIn = vi.fn(() => Promise.resolve())
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/elements?element=7')

    expect(signIn).toHaveBeenCalledWith('/elements?element=7')
  })

  it('lets the login callback through without a token', async () => {
    const signIn = vi.fn(() => Promise.resolve())
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/auth/callback?code=x&state=y')

    expect(app.currentRoute.value.name).toBe('auth-callback')
    expect(signIn).not.toHaveBeenCalled()
  })
})
```

`frontend/tests/setup.ts` — append a module mock every other spec shares (a logged-in editor's token, no navigation):

```ts
import { vi } from 'vitest'

// No spec may start a real redirect to Keycloak: jsdom cannot navigate, and
// the login flow is `auth.spec.ts`'s alone, which un-mocks this module.
vi.mock('../src/lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/auth')>()
  return {
    ...actual,
    accessToken: vi.fn(() => Promise.resolve('test-token')),
    signIn: vi.fn(() => Promise.resolve()),
    signOut: vi.fn(() => Promise.resolve()),
    completeSignIn: vi.fn(() => Promise.resolve('/')),
  }
})
```

- [ ] **Step 3: Run to see them fail**

Run: `cd frontend && npm test -- --run tests/auth.spec.ts tests/api.spec.ts tests/router.spec.ts`
Expected: FAIL — `src/lib/auth` does not exist.

- [ ] **Step 4: Implement**

`frontend/src/lib/auth.ts`:

```ts
// The login, by the Keycloak realm `ea` — authorization code + PKCE, the SPA
// being a public client. See docs/adr/0031.
//
// Tokens live in memory only (CLAUDE.md: never localStorage). A reload forgets
// them, the router's gate sends the page back to Keycloak, and Keycloak's own
// session cookie answers at once — a redirect, not a login form. The PKCE
// verifier has to survive that round trip, so the *state* goes to
// sessionStorage: it is single-use and holds no token.
import { InMemoryWebStorage, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

export const AUTH_AUTHORITY =
  import.meta.env.VITE_AUTH_AUTHORITY ?? 'https://keycloak.famillelallier.net/realms/ea'
export const AUTH_CLIENT_ID = import.meta.env.VITE_AUTH_CLIENT_ID ?? 'ea-spa'
export const CALLBACK_PATH = '/auth/callback'

export function createUserManager(origin: string): UserManager {
  return new UserManager({
    authority: AUTH_AUTHORITY,
    client_id: AUTH_CLIENT_ID,
    redirect_uri: `${origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: origin,
    response_type: 'code',
    scope: 'openid profile',
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
    stateStore: new WebStorageStateStore({ store: globalThis.sessionStorage }),
    automaticSilentRenew: true,
  })
}

const manager = createUserManager(globalThis.location?.origin ?? 'http://localhost:5173')

export async function accessToken(): Promise<string | null> {
  const user = await manager.getUser()
  return user && !user.expired ? user.access_token : null
}

/** Only an in-app path: a `returnTo` naming another origin would be an open redirect. */
export function safeReturnPath(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}

export function signIn(returnTo: string): Promise<void> {
  return manager.signinRedirect({ state: { returnTo: safeReturnPath(returnTo) } })
}

export async function completeSignIn(): Promise<string> {
  const user: User = await manager.signinRedirectCallback()
  return safeReturnPath((user.state as { returnTo?: unknown } | undefined)?.returnTo)
}

export function signOut(): Promise<void> {
  return manager.signoutRedirect()
}
```

`frontend/src/lib/api.ts` — import `{ accessToken, signIn }` from `./auth` and register a second middleware **before** the logging one:

```ts
// Who is calling (docs/adr/0031): the token on every request, and a 401 —
// a token Keycloak no longer honours — restarts the login, coming back to
// exactly this page, query included.
api.use({
  async onRequest({ request }) {
    const token = await accessToken()
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`)
    }
    return request
  },
  onResponse({ response }) {
    if (response.status === 401) {
      const here = globalThis.location ? `${location.pathname}${location.search}` : '/'
      void signIn(here)
    }
  },
})
```

`frontend/src/router/AuthCallback.vue`:

```vue
<script setup lang="ts">
// Where Keycloak sends the browser back. It finishes the login and replaces
// itself with the page the user was going to, so Back never lands here again.
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { completeSignIn } from '../lib/auth'

const router = useRouter()
const failed = ref(false)

onMounted(async () => {
  try {
    await router.replace(await completeSignIn())
  } catch {
    failed.value = true
  }
})
</script>

<template>
  <p v-if="failed" role="alert">
    La connexion a échoué. <a href="/">Réessayer</a>
  </p>
  <p v-else>Connexion…</p>
</template>
```

`frontend/src/router/index.ts` — add the callback route (before the not-found catch-all) and the gate:

```ts
import { accessToken, CALLBACK_PATH, signIn } from '../lib/auth'

export type Gate = {
  accessToken: () => Promise<string | null>
  signIn: (returnTo: string) => Promise<void>
}

// in `routes`, before the catch-all:
  { path: CALLBACK_PATH, name: 'auth-callback', component: () => import('./AuthCallback.vue'), meta: { public: true } },

export function createAppRouter(
  history: RouterHistory = createWebHistory(),
  gate: Gate = { accessToken, signIn },
): Router {
  const router = createRouter({ history, routes })
  // Nobody reaches a section without a token. The API decides what they may
  // do; this only decides that they log in first (docs/adr/0031).
  router.beforeEach(async (to) => {
    if (to.meta.public || (await gate.accessToken())) {
      return true
    }
    await gate.signIn(to.fullPath)
    return false
  })
  return router
}
```

`frontend/src/vite-env.d.ts` — add `readonly VITE_AUTH_AUTHORITY?: string` and `readonly VITE_AUTH_CLIENT_ID?: string` with one-line doc comments.

`frontend/.env.example` — append a French block, like the others:

```
# --- Connexion par Keycloak (docs/adr/0031) --------------------------------
# Le realm `ea` et le client public `ea-spa`. Laissés vides : les valeurs par
# défaut, celles du Keycloak de l'Infra. L'origine qui sert le SPA doit figurer
# dans les « Valid redirect URIs » du client, avec /auth/callback.
# VITE_AUTH_AUTHORITY=https://keycloak.famillelallier.net/realms/ea
# VITE_AUTH_CLIENT_ID=ea-spa
```

- [ ] **Step 5: Run everything on the frontend**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS. Existing specs keep working because `setup.ts` mocks the login with a token.

- [ ] **Step 6: Report** (commit: `feat(auth): le SPA se connecte par Keycloak et envoie son jeton`).

---

### Task 6: MCP adapter — `/mcp` wants the same token

**Files:**
- Create: `backend/src/ea/mcp/auth.py`
- Modify: `backend/src/ea/mcp/server.py` (`build_mcp_server(..., token_verifier=None, auth=None)`), `backend/src/ea/mcp/errors.py` (`speaking_plainly` acts as the token's caller), `backend/src/ea/main.py` (`_mount_mcp`), `.mcp.json` (only if Step 1 shows it is needed)
- Test: `backend/tests/unit/test_mcp_auth.py` (create), `backend/tests/e2e/test_mcp_endpoint.py` (append)

**Interfaces:**
- Consumes: `AccessTokenVerifier` (Task 2), `verifier_of(app)` (Task 4), `current_caller`, `Caller`, both errors (Task 1).
- Produces:
  - `ea.mcp.auth.KeycloakTokenVerifier(verifier: AccessTokenVerifier)` implementing `mcp.server.auth.provider.TokenVerifier` (`async verify_token(token) -> AccessToken | None`); the `AccessToken` carries `subject=caller.subject`, `client_id=caller.subject`, `scopes=[]`, `claims={"preferred_username": caller.username, "roles": sorted(caller.roles)}`
  - `ea.mcp.auth.caller_of(token: AccessToken) -> Caller`
  - `ea.mcp.auth.auth_settings(settings: Settings) -> AuthSettings` (`issuer_url=settings.auth_issuer`, `resource_server_url=settings.mcp_resource_url`, token-resource validation **off**: our tokens' audience is `ea-api`, not the URL)

- [ ] **Step 1: Settle unknown 1 before coding** (report the answer)

- Read `mcp/server/auth/settings.py` fully (`validate_token_resource` default and name).
- Find how the SDK makes the authenticated user visible to a tool **handler** on streamable HTTP: `grep -rn "auth_context_var\|get_access_token\|request_state" .venv/lib/python3.12/site-packages/mcp/server | head -40`. Tool calls may run in the session's task, not the request's; if `get_access_token()` is `None` inside a tool, find what the SDK offers instead (e.g. the request context's `request.user` / `ctx.request_context`) and use it in `speaking_plainly`.
- Check how Claude Code authenticates to an OAuth-protected MCP server with a pre-registered public client: run `claude mcp add --help` and look for `--client-id` / `--callback-port`; if `.mcp.json` supports `"oauth": {"clientId": "ea-mcp", "callbackPort": ...}`, set it. Report exactly what exists. Keycloak's anonymous dynamic registration is off by default, so a pre-registered `ea-mcp` is the assumption.

- [ ] **Step 2: Write the failing tests**

`backend/tests/unit/test_mcp_auth.py`:

```python
"""The MCP side of a bearer token: the SDK's verifier and the caller a tool acts as."""

from __future__ import annotations

import pytest

from ea.mcp.auth import KeycloakTokenVerifier, caller_of
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
```

Append to `backend/tests/e2e/test_mcp_endpoint.py` (reuse its `an_app` / session helpers; build the app with `auth_enabled=True` and `verifier=StaticVerifier({...})`, and pass headers to `streamable_http_client` the way the SDK client accepts them — check its signature):

1. **No token → HTTP 401** on `POST /mcp`, with a `WWW-Authenticate` header containing `resource_metadata=`.
2. **Metadata is served**: `GET /.well-known/oauth-protected-resource/mcp` → 200, `authorization_servers == ["https://keycloak.famillelallier.net/realms/ea"]` (compare as strings; pydantic may add a trailing slash — assert with `.rstrip("/")`).
3. **Reader token**: `list_elements` succeeds; `create_element` returns `is_error=True` whose text mentions `ea-editor`.
4. **Editor token**: `create_element` succeeds.
5. **The loopback guard still refuses a remote peer** on `/mcp` (the existing test must still pass with auth on).

Existing tests of this file build with `auth_enabled=False` (Task 4 did it) and must stay green.

- [ ] **Step 3: Run to see them fail**

Run: `cd backend && uv run pytest tests/unit/test_mcp_auth.py tests/e2e/test_mcp_endpoint.py -q`
Expected: FAIL — `ea.mcp.auth` does not exist.

- [ ] **Step 4: Implement**

`backend/src/ea/mcp/auth.py`:

```python
"""`/mcp` as a resource server of the realm `ea` — the same check as the REST API.

The SDK wants a `TokenVerifier` answering an `AccessToken` or `None`; ours
answers a `Caller`. This is the translation, and nothing more: the roles travel
in `claims`, and `speaking_plainly` turns them back into the caller the services
read. See docs/adr/0031.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from ea.core.config import Settings
from ea.domain.auth import Caller
from ea.domain.errors import NotAuthenticatedError
from ea.domain.ports import AccessTokenVerifier


class KeycloakTokenVerifier:
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
```

(Use the exact field name Step 1 found for token-resource validation.)

`backend/src/ea/mcp/errors.py` — in `plainly`, before `answer = await tool(...)`:

```python
        token = get_access_token()  # from mcp.server.auth.middleware.auth_context
        if token is not None:
            # The transport authenticated this call: the services decide on the
            # token's caller. Without one (auth off, or a test calling the
            # server directly) whoever the process already acts as stays.
            current_caller.set(caller_of(token))
```

and extend the module docstring's tracing paragraph: the INFO line gains `extra["user"]`? — **no**: `user` is not in the list of safe `extra` keys in CLAUDE.md; log the username under `"caller"` only if `tests/unit/test_logging.py` accepts that key, else leave logging unchanged. If Step 1 showed `get_access_token()` is `None` inside a tool, use the mechanism it found instead.

`backend/src/ea/mcp/server.py` — `build_mcp_server(get_service, get_documents, get_ipam, *, version="0.1.0", token_verifier: TokenVerifier | None = None, auth: AuthSettings | None = None)` → `MCPServer(..., token_verifier=token_verifier, auth=auth)`.

`backend/src/ea/main.py` `_mount_mcp`:
- When `settings.auth_enabled`: pass `token_verifier=KeycloakTokenVerifier(<lazy verifier>)` and `auth=auth_settings(settings)`. The verifier is built in the lifespan, after `_mount_mcp` runs, so give `KeycloakTokenVerifier` a verifier that looks it up per call: `LazyVerifier(lambda: verifier_of(app))` — a three-line class in `main.py` with `async def verify(self, token): return await self._get().verify(token)`.
- The transport now has `user_middleware` (authentication + auth-context). Replace the `RuntimeError` guard: wrap each spliced route's `app` in that middleware stack, innermost first, so the route still carries it once spliced — e.g. `for m in reversed(transport.user_middleware): route_app = m.cls(route_app, *m.args, **m.kwargs)` — then `LoopbackClientsOnly` outermost when remote clients are not allowed. Keep the check that refuses a route type it cannot wrap. Update the docstring: the middleware is no longer "only auth adds any today" but "auth adds two, carried onto each route".
- Splice the protected-resource metadata route too (it is in `transport.routes`, so the existing loop already takes it; assert it in the e2e test).

- [ ] **Step 5: Run everything**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q --cov=ea && uv run mypy src migrations && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, coverage ≥ 90 %. `make openapi-check` from the root stays green (`/mcp` is not in the schema).

- [ ] **Step 6: Report** — including the Step 1 findings (commit: `feat(auth): /mcp exige le même jeton que l'API`).

---

### Task 7: Pipelines — the worker logs in as `ea-pipelines`

**Files:**
- Create: `pipelines/src/pipelines/auth.py`
- Modify: `pipelines/src/pipelines/settings.py`, `pipelines/src/pipelines/ea.py` (`ea_client`), `pipelines/docker-compose.yml` (worker env + `extra_hosts`), `pipelines/.env.example`
- Modify: `pipelines/tests/test_ea.py`, `pipelines/tests/test_settings.py`, `pipelines/tests/test_stack.py` (new required secret, new env names)
- Test: `pipelines/tests/test_auth.py` (create)

**Interfaces:**
- Produces:
  - `pipelines.auth.AuthFailed(RuntimeError)`
  - `pipelines.auth.ClientCredentials(httpx.Auth)(*, token_url: str, client_id: str, client_secret: str, http: httpx.Client, clock: Callable[[], float] = time.monotonic, leeway_seconds: float = 30.0)`
  - `Settings.auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"`, `Settings.ea_client_id: str = "ea-pipelines"`, `Settings.ea_client_secret: SecretStr` (required, like the other secrets)

- [ ] **Step 1: Write the failing tests** — `pipelines/tests/test_auth.py`

```python
"""The worker's own token: fetched once, reused until it expires, renewed on a 401."""

from __future__ import annotations

import httpx
import pytest

from pipelines.auth import AuthFailed, ClientCredentials

TOKEN_URL = "https://keycloak.test/realms/ea/protocol/openid-connect/token"


class Keycloak:
    def __init__(self, status: int = 200) -> None:
        self.issued = 0
        self.status = status
        self.forms: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TOKEN_URL
        self.forms.append(request.content)
        self.issued += 1
        return httpx.Response(self.status, json={"access_token": f"tok-{self.issued}", "expires_in": 300})


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def auth(keycloak: Keycloak, clock: Clock | None = None) -> ClientCredentials:
    return ClientCredentials(
        token_url=TOKEN_URL,
        client_id="ea-pipelines",
        client_secret="s3cret",
        http=httpx.Client(transport=httpx.MockTransport(keycloak)),
        clock=clock or Clock(),
    )


def api(credentials: ClientCredentials, handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ea.test", auth=credentials)


def test_asks_keycloak_with_the_client_credentials_grant() -> None:
    keycloak = Keycloak()
    seen: list[str] = []
    api(auth(keycloak), lambda r: (seen.append(r.headers["Authorization"]), httpx.Response(200))[1]).get("/metamodel")

    assert seen == ["Bearer tok-1"]
    form = keycloak.forms[0].decode()
    assert "grant_type=client_credentials" in form and "client_id=ea-pipelines" in form


def test_reuses_the_token_until_it_nearly_expires() -> None:
    keycloak, clock = Keycloak(), Clock()
    client = api(auth(keycloak, clock), lambda r: httpx.Response(200))

    client.get("/a")
    clock.now = 200
    client.get("/b")
    assert keycloak.issued == 1

    clock.now = 280  # within the 30 s leeway of expires_in=300
    client.get("/c")
    assert keycloak.issued == 2


def test_a_401_renews_the_token_and_retries_once() -> None:
    keycloak = Keycloak()
    answers = iter([httpx.Response(401), httpx.Response(200)])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return next(answers)

    assert api(auth(keycloak), handler).get("/metamodel").status_code == 200
    assert seen == ["Bearer tok-1", "Bearer tok-2"]


def test_a_refusal_from_keycloak_is_said_plainly_without_the_secret() -> None:
    with pytest.raises(AuthFailed) as failure:
        api(auth(Keycloak(status=401)), lambda r: httpx.Response(200)).get("/metamodel")

    assert "s3cret" not in str(failure.value)
```

Update `REQUIRED_SECRETS` in `pipelines/tests/test_ea.py` (and wherever else it is defined) with `"ea_client_secret": "ea-secret"`; add to `test_settings.py` that `ea_client_secret` is required; update `test_stack.py`'s expected worker variables with `PIPELINES_EA_CLIENT_SECRET` (and `PIPELINES_AUTH_ISSUER` if passed).

- [ ] **Step 2: Run to see them fail**

Run: `cd pipelines && uv run pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: pipelines.auth`.

- [ ] **Step 3: Implement** `pipelines/src/pipelines/auth.py`

```python
"""The worker's access token for the EA API — Keycloak client credentials.

The pipeline writes to the catalogue, which needs the `ea-editor` role; the
confidential client `ea-pipelines` holds it through its service account. An
`httpx.Auth`, so `EaClient` does not know a token exists. See EA docs/adr/0031.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator

import httpx


class AuthFailed(RuntimeError):
    """Keycloak refused the client, or answered something that is not a token."""


class ClientCredentials(httpx.Auth):
    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        http: httpx.Client,
        clock: Callable[[], float] = time.monotonic,
        leeway_seconds: float = 30.0,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._secret = client_secret
        self._http = http
        self._clock = clock
        self._leeway = leeway_seconds
        self._token: str | None = None
        self._expires_at = 0.0

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._current()}"
        response = yield request
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._token = None
            request.headers["Authorization"] = f"Bearer {self._current()}"
            yield request

    def _current(self) -> str:
        if self._token is None or self._clock() >= self._expires_at - self._leeway:
            self._fetch()
        assert self._token is not None
        return self._token

    def _fetch(self) -> None:
        try:
            response = self._http.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._secret,
                },
            )
            response.raise_for_status()
            body = response.json()
            token, lifetime = str(body["access_token"]), float(body["expires_in"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            # Never the form: it holds the secret.
            msg = f"Keycloak refused client {self._client_id!r} at {self._token_url}: {type(error).__name__}"
            raise AuthFailed(msg) from None
        self._token, self._expires_at = token, self._clock() + lifetime
```

`pipelines/src/pipelines/settings.py` — add (comment style of the file):

```python
    #: The realm `ea` of the Infra Keycloak. The worker logs in as the
    #: confidential client `ea-pipelines`, whose service account holds
    #: `ea-editor` (EA docs/adr/0031). The token endpoint derives from this.
    auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"
    ea_client_id: str = "ea-pipelines"
    ea_client_secret: SecretStr
```

`pipelines/src/pipelines/ea.py` `ea_client`:

```python
def ea_client(settings: Settings) -> EaClient:
    """One pooled client for the EA API, carrying its base URL, timeout and token."""
    credentials = ClientCredentials(
        token_url=f"{settings.auth_issuer.rstrip('/')}/protocol/openid-connect/token",
        client_id=settings.ea_client_id,
        client_secret=settings.ea_client_secret.get_secret_value(),
        # Keycloak is behind the Infra NGINX, signed by the Infra CA — the same
        # certificate MinIO is reached with.
        http=httpx.Client(verify=settings.s3_ca_cert or True, timeout=settings.ea_timeout_seconds),
    )
    return EaClient(
        httpx.Client(
            base_url=settings.ea_base_url, timeout=settings.ea_timeout_seconds, auth=credentials
        )
    )
```

(Check the existing test of `ea_client` still passes; if it inspects the client, extend it to assert `client.http.auth` is a `ClientCredentials`.)

`pipelines/docker-compose.yml` worker: add `PIPELINES_EA_CLIENT_SECRET: ${PIPELINES_EA_CLIENT_SECRET:?PIPELINES_EA_CLIENT_SECRET est obligatoire (client ea-pipelines du realm ea)}` to `environment`, and to `extra_hosts` `- "keycloak.famillelallier.net:host-gateway"` with a French comment: the worker is not on `infra-net`; this name must reach the Infra NGINX published on the Mac, whose certificate `PIPELINES_S3_CA_CERT` already trusts.

`pipelines/.env.example` — under the `PIPELINES_` block:

```
# Keycloak, realm `ea` : le client confidentiel `ea-pipelines` (compte de
# service portant `ea-editor`). Le secret se lit dans la console Keycloak,
# onglet « Credentials » du client — jamais committé.
# PIPELINES_AUTH_ISSUER=https://keycloak.famillelallier.net/realms/ea
PIPELINES_EA_CLIENT_ID=ea-pipelines
PIPELINES_EA_CLIENT_SECRET=
```

- [ ] **Step 4: Run everything in pipelines**

Run: `cd pipelines && uv run pytest -q --cov=pipelines && uv run mypy src && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, coverage ≥ 90 %.

- [ ] **Step 5: Report** (commit: `feat(pipelines): le worker s'authentifie comme ea-pipelines`).

---

### Task 8: Infra — the realm `ea` (repository `~/OpenCode/Infra`)

**Files (in `~/OpenCode/Infra`, never in the EA worktree):**
- Create: `keycloak/realm-import/ea-realm.json`
- Modify: `README.md` (a short *EA login* subsection next to *Jarvis login*), `CLAUDE.md` (a short subsection next to *Jarvis: Keycloak login gate*)

**Isolation:** work in a new git worktree of the Infra repo so its main checkout is untouched: `git -C ~/OpenCode/Infra worktree add ~/OpenCode/Infra/.claude/worktrees/ea-realm -b feat/ea-realm`. Commit there. **Do not push.** Check first whether `.claude/worktrees` is git-ignored in Infra; if not, use `~/OpenCode/Infra-ea-realm` instead.

- [ ] **Step 1: Look for an existing check of the realm files** (`grep -rn "realm-import" ~/OpenCode/Infra/scripts ~/OpenCode/Infra/Makefile ~/OpenCode/Infra/tests 2>/dev/null`). If there is one, run it before and after.

- [ ] **Step 2: Write** `keycloak/realm-import/ea-realm.json`

```json
{
  "realm": "ea",
  "enabled": true,
  "sslRequired": "external",
  "registrationAllowed": false,
  "resetPasswordAllowed": false,
  "roles": {
    "realm": [
      { "name": "ea-editor", "description": "May change the EA catalogue. Reading needs no role." }
    ]
  },
  "clients": [
    {
      "clientId": "ea-spa",
      "name": "EA — SPA",
      "protocol": "openid-connect",
      "publicClient": true,
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": false,
      "implicitFlowEnabled": false,
      "serviceAccountsEnabled": false,
      "attributes": { "pkce.code.challenge.method": "S256", "post.logout.redirect.uris": "+" },
      "redirectUris": [
        "https://ea.infra.famillelallier.net/*",
        "http://localhost:5173/*",
        "http://127.0.0.1:5173/*"
      ],
      "webOrigins": ["+"],
      "protocolMappers": [
        {
          "name": "ea-api-audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": { "included.custom.audience": "ea-api", "id.token.claim": "false", "access.token.claim": "true" }
        }
      ]
    },
    {
      "clientId": "ea-mcp",
      "name": "EA — agents MCP",
      "protocol": "openid-connect",
      "publicClient": true,
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": false,
      "implicitFlowEnabled": false,
      "serviceAccountsEnabled": false,
      "attributes": { "pkce.code.challenge.method": "S256" },
      "redirectUris": ["http://localhost:*", "http://127.0.0.1:*"],
      "webOrigins": [],
      "protocolMappers": [
        {
          "name": "ea-api-audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": { "included.custom.audience": "ea-api", "id.token.claim": "false", "access.token.claim": "true" }
        }
      ]
    },
    {
      "clientId": "ea-pipelines",
      "name": "EA — pipeline worker",
      "protocol": "openid-connect",
      "publicClient": false,
      "standardFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "implicitFlowEnabled": false,
      "serviceAccountsEnabled": true,
      "protocolMappers": [
        {
          "name": "ea-api-audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": { "included.custom.audience": "ea-api", "id.token.claim": "false", "access.token.claim": "true" }
        }
      ]
    }
  ],
  "users": [
    {
      "username": "service-account-ea-pipelines",
      "enabled": true,
      "serviceAccountClientId": "ea-pipelines",
      "realmRoles": ["ea-editor"]
    }
  ]
}
```

The `users` array holds **only** the service-account user (no password, no human) — it is how an import grants a service account a role. Explain that in the CLAUDE.md subsection, since `jarvis-realm.json` deliberately has no `users`. Validate: `python3 -m json.tool keycloak/realm-import/ea-realm.json >/dev/null`.

- [ ] **Step 3: Document** (short, in the voice of the existing *Jarvis* sections):
  - After the first `make up` with this file: create the humans in the admin console (realm `ea` → Users), give editors the realm role `ea-editor`; copy the `ea-pipelines` secret (Clients → ea-pipelines → Credentials) into EA's `pipelines/.env` as `PIPELINES_EA_CLIENT_SECRET`.
  - `ea-spa`'s redirect URIs: add each LAN origin that serves the Vite dev server (`http://192.168.x.y:5173/*`) in the console — an exact list, as EA's `EA_CORS_ORIGINS`.
  - No oauth2-proxy and no `auth_request` for EA: the EA API and `/mcp` verify the token themselves (EA `docs/adr/0031`); `nginx/conf.d/ea.conf` is unchanged.
  - `--import-realm` is a no-op for an existing realm: changing this file later means editing the live realm in the console too.

- [ ] **Step 4: Commit in the Infra worktree** — `git add keycloak/realm-import/ea-realm.json README.md CLAUDE.md && git commit -m "feat(keycloak): realm ea pour l'application EA"` with the attribution trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Report the worktree path, branch and commit.

---

### Task 9: SPA — who is logged in, and write controls for editors only

**Files:**
- Create: `frontend/src/lib/me.ts`, `frontend/src/components/UserBadge.vue`
- Modify: `frontend/src/App.vue` (header), `frontend/src/features/elements/ElementCatalogue.vue`, `frontend/src/features/documents/DocumentPanel.vue`, `frontend/src/features/relationships/RelationshipPanel.vue`, `frontend/src/features/ipam/IpamSection.vue`, `frontend/tests/setup.ts` (mock `lib/me`)
- Test: `frontend/tests/me.spec.ts`, `frontend/tests/UserBadge.spec.ts` (create); one "reader sees no write control" test appended to each of `ElementCatalogue.spec.ts`, `DocumentPanel.spec.ts`, `RelationshipPanel.spec.ts`, `IpamSection.spec.ts`

**Interfaces:**
- Consumes: `GET /me` → `components['schemas']['MeRead']` from `src/api/schema` (Task 4); `signOut` (Task 5).
- Produces: `lib/me.ts` — `useMe(): { me: Readonly<Ref<MeRead | null>>; canWrite: ComputedRef<boolean>; error: Readonly<Ref<string | null>>; load: () => Promise<void> }`, a module-level singleton (one `GET /me` per page load), and `resetMe()` for tests.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/me.spec.ts` (un-mocks `lib/me`):

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.unmock('../src/lib/me')
const { resetMe, useMe } = await import('../src/lib/me')

function answering(body: unknown, status = 200) {
  return vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })))
}

describe('who is logged in', () => {
  afterEach(() => {
    resetMe()
    vi.unstubAllGlobals()
  })

  it('asks the API once, however many screens ask', async () => {
    const fetch = answering({ username: 'alice', can_write: true })
    vi.stubGlobal('fetch', fetch)

    await Promise.all([useMe().load(), useMe().load()])

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(useMe().me.value?.username).toBe('alice')
    expect(useMe().canWrite.value).toBe(true)
  })

  it('offers no write before the API has said so', () => {
    expect(useMe().canWrite.value).toBe(false)
  })

  it('keeps a failure in its state rather than throwing', async () => {
    vi.stubGlobal('fetch', answering({ error: 'internal_error', detail: 'x' }, 500))

    await useMe().load()

    expect(useMe().error.value).not.toBeNull()
    expect(useMe().canWrite.value).toBe(false)
  })
})
```

`frontend/tests/UserBadge.spec.ts`: renders the username from a mocked `useMe`, and clicking *Déconnexion* calls `signOut`.

In each of the four component specs, add a test that mocks `useMe` with `canWrite = false` and asserts the write controls are absent:
- `ElementCatalogue`: no *Nouvel élément*, no *Modifier …* / delete buttons; *Filtrer* and the name link remain.
- `DocumentPanel`: no upload/replace form, no remove button; the document list and *Fermer* remain.
- `RelationshipPanel`: no *Associer* form, no delete-link button; the list remains.
- `IpamSection`: no *Déclarer* form, no allocate/assign form, no release button; *Chercher* remains.

`frontend/tests/setup.ts` — append a mock of `../src/lib/me` returning a loaded editor (`canWrite` a `computed(() => true)`, `me` a `ref({ username: 'editor', can_write: true })`, `load` resolving), so existing specs keep seeing every control. A spec that needs a reader overrides with `vi.mocked(...)` or its own `vi.mock` factory.

- [ ] **Step 2: Run to see them fail**

Run: `cd frontend && npm test -- --run tests/me.spec.ts tests/UserBadge.spec.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`frontend/src/lib/me.ts`:

```ts
// Who is logged in, and whether the SPA should offer to change anything.
//
// The API decides (docs/adr/0031): this only asks `GET /me` — once per page
// load, shared by every screen — and hides what the answer says the user may
// not do. Until the answer arrives nothing is offered, so a reader never sees
// a button flash before it disappears.
import { computed, readonly, ref } from 'vue'

import type { components } from '../api/schema'
import { api, messageOf, unwrap } from './api'

type MeRead = components['schemas']['MeRead']

const me = ref<MeRead | null>(null)
const error = ref<string | null>(null)
let pending: Promise<void> | null = null

export function useMe() {
  return {
    me: readonly(me),
    error: readonly(error),
    canWrite: computed(() => me.value?.can_write === true),
    load(): Promise<void> {
      pending ??= api
        .GET('/me')
        .then((result) => {
          me.value = unwrap(result)
          error.value = null
        })
        .catch((failure: unknown) => {
          error.value = messageOf(failure)
          pending = null
        })
      return pending
    },
  }
}

/** Forget the answer — for tests, and nothing else. */
export function resetMe(): void {
  me.value = null
  error.value = null
  pending = null
}
```

`frontend/src/components/UserBadge.vue`: calls `useMe().load()` in `onMounted` (`void`-ed per the ESLint rules — use `onMounted(() => { void me.load() })`), shows `me.value.username` and a `<button type="button" class="secondary" @click="onSignOut">Déconnexion</button>` that calls `signOut()` from `../lib/auth`. Scoped styles consistent with `BackendStatus.vue`.

`frontend/src/App.vue`: render `<UserBadge />` beside `<BackendStatus />` in the header.

In the four components: `const { canWrite } = useMe()` and `v-if="canWrite"` on each write control listed in Step 1 (wrap a group in one `<template v-if="canWrite">` where the controls sit together). Do not remove any read control.

- [ ] **Step 4: Run everything on the frontend**

Run: `cd frontend && npm test -- --run && npm run lint && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Report** (commit: `feat(auth): le SPA montre qui est connecté et n'offre l'écriture qu'aux éditeurs`).

---

### Task 10: Deployment, ADR 0031, CLAUDE.md, backend `.env.example`

**Files:**
- Create: `docs/adr/0031-authentification-par-keycloak.md` (from `docs/adr/TEMPLATE.md`, French)
- Modify: `deploy/ea.stack.yml`, `deploy/ea.env.example`, `frontend/Dockerfile` (two `ARG`/`ENV` for `VITE_AUTH_*`), `backend/.env.example`, `CLAUDE.md`, `docs/adr/0023-mcp-reserve-a-la-boucle-locale.md` (one line at the top: *Amendé par 0031* — do not rewrite history), `scripts/portainer-stack.sh` only if it lists required variables explicitly
- Test: `backend/tests/unit/test_deploy_stack.py` (append)

**Interfaces:**
- Consumes: every name fixed by Tasks 1–9 (read the code, not only this plan: Task 6's report may have changed the MCP wiring or `.mcp.json`).

- [ ] **Step 1: Write the failing test** — append to `backend/tests/unit/test_deploy_stack.py` (reuse its YAML-loading helpers):

```python
def test_the_deployed_api_verifies_tokens_with_the_infra_ca_mounted_read_only() -> None:
    api = stack()["services"]["api"]  # use the file's existing loader
    assert "EA_AUTH_ENABLED" not in api["environment"] or api["environment"]["EA_AUTH_ENABLED"] == "true"
    assert api["environment"]["EA_AUTH_CA_CERT"] == "/etc/ssl/certs/infra-ca.pem"
    assert any(v.endswith(":/etc/ssl/certs/infra-ca.pem:ro") for v in api["volumes"])


def test_the_spa_image_is_built_for_the_realm() -> None:
    args = stack()["services"]["web"]["build"]["args"]
    assert set(args) >= {"VITE_AUTH_AUTHORITY", "VITE_AUTH_CLIENT_ID"}
```

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q` → FAIL.

- [ ] **Step 2: Deployment**
  - `deploy/ea.stack.yml` `api.environment`: `EA_AUTH_ISSUER: ${EA_AUTH_ISSUER:-https://keycloak.famillelallier.net/realms/ea}`, `EA_AUTH_CA_CERT: /etc/ssl/certs/infra-ca.pem`; `api.volumes`: `- ${INFRA_CA_CERT:?INFRA_CA_CERT est obligatoire, le chemin du CA de l'Infra sur le Mac}:/etc/ssl/certs/infra-ca.pem:ro`. French comments: inside `infra-net`, `keycloak.famillelallier.net` is an alias of `nginx`, which serves the Infra certificate; `EA_MCP_ENABLED` stays `false` (serving `/mcp` behind NGINX is a later ADR, even with a token).
  - `web.build`: turn `build: ../frontend` into `build: { context: ../frontend, args: { VITE_AUTH_AUTHORITY: ${EA_AUTH_ISSUER:-https://keycloak.famillelallier.net/realms/ea}, VITE_AUTH_CLIENT_ID: ea-spa } }`.
  - `frontend/Dockerfile`: `ARG VITE_AUTH_AUTHORITY=https://keycloak.famillelallier.net/realms/ea` / `ARG VITE_AUTH_CLIENT_ID=ea-spa` with matching `ENV`, beside `VITE_API_BASE_URL`.
  - `deploy/ea.env.example`: `INFRA_CA_CERT=` under the mandatory block (comment: absolute path, e.g. `/Users/…/OpenCode/Infra/certs/infra-ca.crt`); `# EA_AUTH_ISSUER=…` under the optional block.
  - Make sure `make app-up`'s refusal of empty `:?` variables covers `INFRA_CA_CERT` (read `scripts/portainer-stack.sh`).

- [ ] **Step 3: `backend/.env.example`** — a French block after the MCP section:

```
# --- Authentification, realm Keycloak `ea` (docs/adr/0031) ----------------
# À true par défaut : toute requête porte un jeton, toute écriture exige le
# rôle `ea-editor`. false n'est accepté qu'avec EA_DEBUG=true, et chaque appel
# est alors celui du « local-developer », éditeur.
EA_AUTH_ENABLED=true
EA_AUTH_ISSUER=https://keycloak.famillelallier.net/realms/ea
EA_AUTH_AUDIENCE=ea-api
# Le CA de l'Infra : depuis ce Mac, keycloak.famillelallier.net résout sur
# 127.0.0.1 derrière un certificat que le trousseau système ne connaît pas.
EA_AUTH_CA_CERT=
# L'identifiant de ressource que /mcp publie (RFC 9728).
EA_MCP_RESOURCE_URL=http://127.0.0.1:8000/mcp
```

- [ ] **Step 4: ADR 0031** — `titre: Authentification par Keycloak`, `date: 2026-09-13`, `statut: Proposition` (nothing is deployed until the realm is imported and the stack redeployed), `affects:` the files of Tasks 1–9. Content from the spec: context (anyone reaching the port writes; Keycloak already runs; Jarvis's oauth2-proxy gap), decision (resource server; realm/clients/role table; fail-closed caller in `services/`; `/me`; `oidc-client-ts` as a new runtime dependency with in-memory tokens and the reload redirect; `pyjwt[crypto]` made a direct dependency; `/mcp` token + kept loopback guard; pipeline client credentials), alternatives table (oauth2-proxy gate, BFF session cookie, `keycloak-js`, hand-written PKCE), consequences (Keycloak down = API does not boot, cached keys keep serving; every new service method needs its check — `test_service_guards.py` enforces it; no Playwright login test; LAN origins to add per host in `ea-spa`; supersedes the CLAUDE.md line "OAuth2 password/bearer … argon2"; amends 0023), references (0014, 0023, 0027, 0028, the Infra realm file, the spec and this plan).

- [ ] **Step 5: CLAUDE.md** (same commit as the code, per its own rule):
  - *Project status*: remove "auth" from **Not yet scaffolded**; add one sentence on Keycloak auth pointing at `docs/adr/0031`.
  - *Locked stack decisions*: a row `Authentication | Keycloak realm `ea`, EA as resource server (`pyjwt` + JWKS), SPA `oidc-client-ts` PKCE | … — see docs/adr/0031`.
  - *Repository layout*: `domain/auth.py`, `services/caller.py`, `repositories/keycloak.py`, `api/auth.py`, `mcp/auth.py`, `pipelines/src/pipelines/auth.py`, `frontend/src/lib/auth.ts`, `lib/me.ts`.
  - *Security rules*: replace the "Auth: OAuth2 password/bearer … argon2" bullet with the Keycloak rule — tokens verified RS256 against the realm JWKS, `aud=ea-api`; authorisation in `services/` via `require_caller`/`require_editor`, fail closed; SPA tokens in memory.
  - *The MCP adapter* and the paragraph on `/mcp` binding: `/mcp` now also requires a bearer token; the loopback guard stays; still off when deployed.
  - *TDD*: the autouse `_an_editor_is_calling` fixture, `nobody_calling`, `StaticVerifier`; API tests build `Settings(auth_enabled=False)` unless they test auth.
  - A short new section *Who is calling is decided once, in `services/`* (5–10 lines) on the caller `ContextVar` and the AST guard test.

- [ ] **Step 6: Run**

Run (root): `make check`
Expected: green. Report anything red with its output.

- [ ] **Step 7: Report** (commit: `docs(auth): ADR 0031, déploiement et CLAUDE.md pour Keycloak`).

---

## Self-review

- **Spec coverage:** realm & clients → 8; settings, `Caller`, fail-closed context → 1; JWKS verifier, RS256, rotation throttle, boot probe → 2; guards in every service + reindex → 3; REST dependency, 401/403, `/me`, route list test, OpenAPI → 4; SPA PKCE, memory tokens, bearer, 401 → login, callback, guard → 5; `/mcp` token, PRM, loopback kept → 6; pipeline client credentials + reachability (unknown 4 via `host-gateway`) → 7; username, sign-out, hidden write controls → 9; stack CA mount, build args, ADR, CLAUDE.md, env examples → 10. Unknowns 2 and 3 were settled while planning (`AccessToken.claims` exists; a `ContextVar` set in an async dependency reaches the endpoint and the services); unknown 1 is Task 6 Step 1.
- **Names:** `NotAuthenticatedError` / `NotAuthorisedError` (repo's `*Error` convention; the spec's shorter names are the same errors), `AccessTokenVerifier` (distinct from the SDK's `TokenVerifier`), `StaticVerifier`, `an_editor` / `a_reader`, `acting_as`, `MeRead`, `useMe`, `ClientCredentials` — used consistently across tasks.
