"""The one door out to third-party systems — see docs/adr/0063.

Every connector (`external_github.py`, `external_jira.py`, ...) calls through an
`EgressClient`, and nothing else in the codebase may open a connection to a provider. It
cannot be made to call anything but its target, because there is nowhere for an address to
come from:

* **The host is fixed at construction**, from the configuration. A request is a *path*
  (plain characters, no query, no `//`) and query parameters that are encoded, never
  concatenated.
* **The name is resolved here, once, and every address is checked** against the policy of
  `domain/egress.py` (loopback, link-local and cloud metadata never; private ranges only
  when the configuration declares their network). The connection then goes to the *validated
  address* with the original name as `Host` and as the TLS server name, so the certificate is
  still checked against the name and a second lookup cannot send it elsewhere (DNS
  rebinding).
* **TLS is always verified**, with the system store or the configured CA — never `verify=False`.
  `trust_env=False`: a proxy variable of the environment is not a way round the checks.
* **No redirect is followed.** A 3xx is reported to the connector, which treats it as
  unavailable: the `Location` of a response is somebody else's text.
* **The answer is bounded before it is read**: a declared length over the ceiling is refused,
  a stream is cut the moment it outgrows it, and the body is read *raw* with
  `Accept-Encoding: identity` — a compression bomb is never inflated.
* **Time is bounded twice**: per phase by `httpx.Timeout`, and for the whole call.
* **Nothing is retried.** A GET may be asked again by a person; a POST is not idempotent and
  an automatic retry could open two tickets.

A failure raises `EgressError`, whose message names the rule that stopped the call and
never the host, the headers or the provider's words.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx

from ea.domain.egress import EgressRefusedError, IPNetwork, check_address

logger = logging.getLogger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]

#: Characters of a request path: no query, no fragment, no whitespace, no control.
_PATH: Final = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,2000}")
#: More addresses than any real name has: a longer list is an attack on the loop below.
_MAX_ADDRESSES: Final = 32


class EgressError(Exception):
    """The call did not happen, or did not finish — and why, in words that name no host."""

    def __init__(self, reason: str, *, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class EgressResponse:
    status: int
    body: bytes


async def system_resolver(host: str, port: int) -> list[str]:  # pragma: no cover - needs DNS
    """Every address the system resolver gives for `host`, without picking one."""
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in found]


def _tls(ca_cert: str | None) -> ssl.SSLContext:
    """The system trust store, plus the configured CA when there is one. Always verifying."""
    return ssl.create_default_context(cafile=ca_cert or None)


class EgressClient:
    """GET and POST to one configured https target, through the checks above."""

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        ports: Iterable[int],
        allowed_networks: Iterable[IPNetwork],
        timeout: float,
        total_timeout: float,
        max_bytes: int,
        ca_cert: str | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            parts = urlsplit(url)
            port = parts.port or 443
        except ValueError:
            msg = "not a usable target address"
            raise ValueError(msg) from None
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or port not in set(ports)
            or parts.path not in {"", "/"}
        ):
            msg = "not a usable target address (https, no credentials, a declared port)"
            raise ValueError(msg)
        self._host = parts.hostname
        self._port = port
        self._networks = tuple(allowed_networks)
        self._resolver = resolver
        self._total = total_timeout
        self._max = max_bytes
        self._headers = {
            **headers,
            "Accept-Encoding": "identity",
            "User-Agent": "ea-catalogue",
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            verify=_tls(ca_cert) if transport is None else True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=2),
            transport=transport,
        )

    async def get(self, path: str, *, params: Mapping[str, str] | None = None) -> EgressResponse:
        return await self._send("GET", path, params=params, body=None)

    async def post(self, path: str, *, json: Any) -> EgressResponse:
        return await self._send("POST", path, params=None, body=json)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _address(self) -> str:
        """One validated address of the target — all of them are checked, none is trusted."""
        try:
            literal = ipaddress.ip_address(self._host)
            found = [str(literal)]
        except ValueError:
            try:
                found = await self._resolver(self._host, self._port)
            except (TimeoutError, OSError):
                raise EgressError("the name could not be resolved") from None
        if not found or len(found) > _MAX_ADDRESSES:
            raise EgressError("the name did not resolve to a usable set of addresses")
        for text in found:
            try:
                check_address(ipaddress.ip_address(text), allowed=self._networks)
            except (EgressRefusedError, ValueError):
                logger.warning(
                    "an outbound address was refused by the egress policy",
                    extra={"action": "egress_refused"},
                )
                raise EgressError("an address of the target is not one a call may reach") from None
        return found[0]

    async def _send(
        self, method: str, path: str, *, params: Mapping[str, str] | None, body: Any
    ) -> EgressResponse:
        if not _PATH.fullmatch(path) or path.startswith("//"):
            raise EgressError("the request path is not a plain path")
        try:
            async with asyncio.timeout(self._total):
                address = await self._address()
                return await self._exchange(method, address, path, params, body)
        except TimeoutError:
            raise EgressError("the call took too long", timed_out=True) from None

    async def _exchange(
        self,
        method: str,
        address: str,
        path: str,
        params: Mapping[str, str] | None,
        body: Any,
    ) -> EgressResponse:
        netloc = f"[{address}]" if ":" in address else address
        host = self._host if self._port == 443 else f"{self._host}:{self._port}"
        request = self._client.build_request(
            method,
            f"https://{netloc}:{self._port}{path}",
            params=dict(params) if params else None,
            json=body,
            headers={**self._headers, "Host": host},
            extensions={"sni_hostname": self._host},
        )
        received = bytearray()
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
            try:
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self._max:
                    raise EgressError("the answer is too large")
                try:
                    async for chunk in response.aiter_raw():
                        received += chunk
                        if len(received) > self._max:
                            raise EgressError("the answer is too large")
                except httpx.StreamConsumed:
                    # Only an in-memory transport hands back a response it has already read.
                    received = bytearray(response.content)
                    if len(received) > self._max:
                        raise EgressError("the answer is too large") from None
            finally:
                await response.aclose()
        except httpx.TimeoutException:
            raise EgressError("the provider did not answer in time", timed_out=True) from None
        except (httpx.HTTPError, ssl.SSLError, OSError):
            raise EgressError("the provider could not be reached") from None
        return EgressResponse(response.status_code, bytes(received))
