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
