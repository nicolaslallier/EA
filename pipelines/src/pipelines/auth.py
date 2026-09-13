"""The worker's access token for the EA API — Keycloak client credentials.

The pipeline writes to the catalogue, which needs the `ea-editor` role; the
confidential client `ea-pipelines` holds it through its service account. An
`httpx.Auth`, so `EaClient` does not know a token exists. See EA docs/adr/0032.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator

import httpx


class AuthFailed(RuntimeError):  # noqa: N818 -- exact name from the task interface
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

    def close(self) -> None:
        """Close the client Keycloak is asked with — `httpx.Client` never closes its `auth`."""
        self._http.close()

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._current()}"
        response = yield request
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._token = None
            request.headers["Authorization"] = f"Bearer {self._current()}"
            yield request

    def _current(self) -> str:
        token = self._token
        if token is None or self._clock() >= self._expires_at - self._leeway:
            return self._fetch()
        return token

    def _fetch(self) -> str:
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
            msg = (
                f"Keycloak refused client {self._client_id!r} at {self._token_url}: "
                f"{type(error).__name__}"
            )
            raise AuthFailed(msg) from None
        self._token, self._expires_at = token, self._clock() + lifetime
        return token
