"""The one door out: a client that cannot be made to call anything else (docs/adr/0073).

No test here opens a socket: the transport is `httpx.MockTransport` and the resolver is a
function, so what is checked is the *decision* — which address is connected to, what is
sent, what is read — not the network.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx
import pytest

from ea.domain.egress import networks_from
from ea.repositories.external_http import (
    EgressClient,
    EgressError,
    EgressResponse,
)

PUBLIC = "93.184.216.34"
API = "https://api.provider.test"
TOKEN = "tok_this_must_never_be_printed"

Handler = Callable[[httpx.Request], httpx.Response]


def resolving(*addresses: str) -> Callable[[str, int], object]:
    calls: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> list[str]:
        calls.append((host, port))
        return list(addresses)

    resolver.calls = calls  # type: ignore[attr-defined]
    return resolver


def client(
    handler: Handler,
    *,
    url: str = API,
    addresses: tuple[str, ...] = (PUBLIC,),
    networks: tuple[str, ...] = (),
    ports: tuple[int, ...] = (443,),
    max_bytes: int = 1000,
    total: float = 5.0,
) -> EgressClient:
    return EgressClient(
        url,
        headers={"Authorization": f"Bearer {TOKEN}"},
        ports=ports,
        allowed_networks=networks_from(networks),
        timeout=2.0,
        total_timeout=total,
        max_bytes=max_bytes,
        resolver=resolving(*addresses),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


@pytest.mark.asyncio
class TestWhereItConnects:
    async def test_it_connects_to_the_address_it_validated_with_the_configured_name_in_host_and_sni(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        response = await client(handler).get("/repos/a/b")

        assert response.status == 200
        (request,) = seen
        assert request.url.host == PUBLIC  # not the name: no second lookup can redirect it
        assert request.url.path == "/repos/a/b"
        assert request.headers["host"] == "api.provider.test"
        assert request.extensions["sni_hostname"] == "api.provider.test"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"

    async def test_the_name_is_resolved_once_per_request_and_never_taken_from_the_caller(
        self,
    ) -> None:
        resolver = resolving(PUBLIC)
        egress = EgressClient(
            API,
            headers={},
            ports=(443,),
            allowed_networks=(),
            timeout=1,
            total_timeout=2,
            max_bytes=100,
            resolver=resolver,  # type: ignore[arg-type]
            transport=httpx.MockTransport(ok),
        )

        await egress.get("/x")

        assert resolver.calls == [("api.provider.test", 443)]  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "bad",
        [
            "//evil.test/x",
            "x",
            "/a b",
            "/a\r\nHost: evil",
            "/a?b=1",
            "/a#f",
            "/a\x00",
            "/" + "a" * 3000,
        ],
    )
    async def test_a_path_that_is_not_a_plain_path_is_refused_before_anything_happens(
        self, bad: str
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with pytest.raises(EgressError):
            await client(handler).get(bad)

        assert seen == []

    async def test_query_parameters_are_encoded_not_concatenated(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        await client(handler).get("/q", params={"a": "x&y=1", "b": "é"})

        assert seen[0].url.params["a"] == "x&y=1"
        assert seen[0].url.params["b"] == "é"
        assert seen[0].url.query.count(b"&") == 1


@pytest.mark.asyncio
class TestWhatTheAddressMayBe:
    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "::1", "169.254.169.254", "10.0.0.5", "192.168.1.1", "0.0.0.0", "fe80::1"],
    )
    async def test_a_name_that_resolves_to_a_forbidden_address_is_never_connected_to(
        self, address: str
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with pytest.raises(EgressError):
            await client(handler, addresses=(address,)).get("/x")

        assert seen == []

    async def test_one_bad_address_among_good_ones_refuses_the_whole_answer(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        with pytest.raises(EgressError):
            await client(handler, addresses=(PUBLIC, "10.0.0.5")).get("/x")

        assert seen == []

    async def test_a_declared_private_network_is_reachable_and_loopback_never_is(self) -> None:
        reached = await client(ok, addresses=("10.1.2.3",), networks=("10.1.0.0/16",)).get("/x")
        assert reached.status == 200
        with pytest.raises(EgressError):
            await client(ok, addresses=("127.0.0.1",), networks=("10.1.0.0/16",)).get("/x")

    async def test_a_name_that_does_not_resolve_is_a_failure_not_a_crash(self) -> None:
        with pytest.raises(EgressError):
            await client(ok, addresses=()).get("/x")

    async def test_a_resolver_that_raises_is_a_failure_too(self) -> None:
        async def broken(host: str, port: int) -> list[str]:
            raise OSError("no such host")

        egress = EgressClient(
            API,
            headers={},
            ports=(443,),
            allowed_networks=(),
            timeout=1,
            total_timeout=2,
            max_bytes=10,
            resolver=broken,
            transport=httpx.MockTransport(ok),
        )

        with pytest.raises(EgressError):
            await egress.get("/x")


class TestWhatItIsBuiltOn:
    @pytest.mark.parametrize(
        "url",
        [
            "http://api.provider.test",
            "ftp://api.provider.test",
            "https://u:p@api.provider.test",
            "https://api.provider.test:8443",
            "api.provider.test",
            "https:///x",
        ],
    )
    def test_only_an_https_address_on_a_declared_port_is_a_target(self, url: str) -> None:
        with pytest.raises(ValueError, match="target"):
            client(ok, url=url)

    def test_an_address_literal_is_checked_like_any_other_answer(self) -> None:
        assert client(ok, url="https://93.184.216.34")  # public literal: fine


@pytest.mark.asyncio
class TestWhatItReads:
    async def test_a_declared_length_over_the_ceiling_is_refused_before_reading(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-length": "5000"}, content=b"x" * 5000)

        with pytest.raises(EgressError, match="large"):
            await client(handler, max_bytes=1000).get("/x")

    async def test_a_stream_that_outgrows_the_ceiling_is_cut_not_buffered(self) -> None:
        sent = 0

        async def body() -> object:
            nonlocal sent
            for _ in range(10_000):
                sent += 100
                yield b"x" * 100

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body())  # type: ignore[arg-type]

        with pytest.raises(EgressError, match="large"):
            await client(handler, max_bytes=1000).get("/x")

        assert sent < 5000  # it stopped reading long before the end

    async def test_the_answer_is_asked_for_and_read_without_decompression(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=httpx.ByteStream(b"\x1f\x8bnot"),
            )

        response = await client(handler).get("/x")

        assert seen[0].headers["accept-encoding"] == "identity"
        assert response.body == b"\x1f\x8bnot"  # raw bytes: a bomb is never inflated

    async def test_a_redirect_is_reported_and_never_followed(self) -> None:
        hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hosts.append(request.headers["host"])
            return httpx.Response(302, headers={"location": "https://evil.test/steal"})

        response = await client(handler).get("/x")

        assert response.status == 302
        assert hosts == ["api.provider.test"]


@pytest.mark.asyncio
class TestWhenItFails:
    async def test_a_network_error_is_a_failure_that_says_nothing_about_the_host(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(EgressError) as raised:
            await client(handler).get("/x")

        assert not raised.value.timed_out
        assert "provider.test" not in str(raised.value)

    async def test_a_slow_answer_is_a_timeout(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(1)
            return httpx.Response(200)

        with pytest.raises(EgressError) as raised:
            await client(handler, total=0.05).get("/x")  # type: ignore[arg-type]

        assert raised.value.timed_out

    async def test_an_httpx_timeout_is_a_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(EgressError) as raised:
            await client(handler).get("/x")

        assert raised.value.timed_out

    async def test_the_token_reaches_no_log_and_no_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"refused {request.headers['authorization']}", request=request)

        with caplog.at_level(logging.DEBUG), pytest.raises(EgressError) as raised:
            await client(handler).get("/x")

        assert TOKEN not in caplog.text
        assert TOKEN not in str(raised.value)
        assert TOKEN not in repr(raised.value)


@pytest.mark.asyncio
class TestAHeaderThatCannotBeEncoded:
    async def test_it_is_a_failure_of_the_call_not_a_crash_and_the_value_stays_out_of_it(
        self,
    ) -> None:
        egress = EgressClient(
            API,
            headers={"Authorization": "Bearer tok\u00e9n-secret"},
            ports=(443,),
            allowed_networks=(),
            timeout=1,
            total_timeout=2,
            max_bytes=100,
            resolver=resolving(PUBLIC),  # type: ignore[arg-type]
            transport=httpx.MockTransport(ok),
        )

        with pytest.raises(EgressError) as raised:
            await egress.get("/x")

        assert "secret" not in str(raised.value)


@pytest.mark.asyncio
class TestWriting:
    async def test_a_write_sends_its_json_once_and_is_never_retried(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(500)

        response = await client(handler).post("/repos/a/b/issues", json={"title": "t"})

        assert response.status == 500
        assert len(seen) == 1
        assert seen[0].method == "POST"
        assert seen[0].headers["content-type"] == "application/json"


def test_the_response_type_carries_only_what_a_connector_reads() -> None:
    assert EgressResponse(200, b"{}").body == b"{}"
