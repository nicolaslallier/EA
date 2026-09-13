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
        return httpx.Response(
            self.status, json={"access_token": f"tok-{self.issued}", "expires_in": 300}
        )


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
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://ea.test", auth=credentials
    )


def test_asks_keycloak_with_the_client_credentials_grant() -> None:
    keycloak = Keycloak()
    seen: list[str] = []
    api(
        auth(keycloak), lambda r: (seen.append(r.headers["Authorization"]), httpx.Response(200))[1]
    ).get("/metamodel")

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
