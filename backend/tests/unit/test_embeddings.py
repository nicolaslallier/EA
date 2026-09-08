"""The client that turns text into vectors, against a transport that never leaves.

It speaks the OpenAI `/embeddings` shape because that is what both candidates
for the deployed service speak — Ollama and text-embeddings-inference — so the
choice between them stays a URL rather than a rewrite. See docs/adr/0019.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from ea.core.logging import EMBEDDINGS_LOGGER
from ea.repositories.embeddings import EmbeddingServiceError, HttpEmbedder

WIDTH = 4


def responder(
    *, width: int = WIDTH, status: int = 200, body: Any | None = None
) -> tuple[list[dict[str, Any]], httpx.MockTransport]:
    """A fake embedding service, plus the list of requests it was sent."""
    seen: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append({"url": str(request.url), "payload": payload, "headers": request.headers})
        if body is not None or status != 200:
            return httpx.Response(status, json=body if body is not None else {"error": "nope"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index)] * width}
                    for index, _ in enumerate(payload["input"])
                ]
            },
        )

    return seen, httpx.MockTransport(handle)


def embedder(transport: httpx.MockTransport, **overrides: Any) -> HttpEmbedder:
    options: dict[str, Any] = {
        "base_url": "http://embeddings.invalid/v1",
        "model": "bge-m3",
        "dimensions": WIDTH,
        "batch_size": 2,
    }
    options.update(overrides)
    return HttpEmbedder(client=httpx.AsyncClient(transport=transport), **options)


pytestmark = pytest.mark.asyncio


class TestEmbeddingPassages:
    async def test_it_returns_one_vector_per_text_in_order(self) -> None:
        seen, transport = responder()

        vectors = await embedder(transport).embed_passages(["a", "b", "c"])

        assert len(vectors) == 3
        assert all(len(vector) == WIDTH for vector in vectors)
        assert [text for call in seen for text in call["payload"]["input"]] == ["a", "b", "c"]

    async def test_it_batches_rather_than_sending_one_huge_request(self) -> None:
        """A thousand passages in one body is a timeout waiting to happen."""
        seen, transport = responder()

        await embedder(transport).embed_passages(["a", "b", "c", "d", "e"])

        assert [len(call["payload"]["input"]) for call in seen] == [2, 2, 1]

    async def test_it_reorders_a_service_that_answers_out_of_order(self) -> None:
        """`index` is in the response for a reason, so it is what is trusted."""

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [1.0] * WIDTH},
                        {"index": 0, "embedding": [0.0] * WIDTH},
                    ]
                },
            )

        vectors = await embedder(httpx.MockTransport(handle), batch_size=8).embed_passages(
            ["first", "second"]
        )

        assert vectors[0][0] == 0.0
        assert vectors[1][0] == 1.0

    async def test_embedding_nothing_asks_the_service_nothing(self) -> None:
        seen, transport = responder()

        assert await embedder(transport).embed_passages([]) == ()
        assert seen == []

    async def test_the_configured_prefix_is_prepended_to_every_passage(self) -> None:
        """Several families want `passage:` here and `query:` there — e5 does."""
        seen, transport = responder()

        await embedder(transport, passage_prefix="passage: ").embed_passages(["a"])

        assert seen[0]["payload"]["input"] == ["passage: a"]


class TestEmbeddingAQuery:
    async def test_it_returns_a_single_vector(self) -> None:
        _, transport = responder()

        vector = await embedder(transport).embed_query("où redémarrer le service ?")

        assert len(vector) == WIDTH

    async def test_a_query_carries_its_own_prefix(self) -> None:
        seen, transport = responder()

        await embedder(transport, query_prefix="query: ").embed_query("quoi ?")

        assert seen[0]["payload"]["input"] == ["query: quoi ?"]

    async def test_the_query_prefix_is_not_the_passage_prefix(self) -> None:
        """Swapping the two costs nothing visible and a great deal of recall."""
        seen, transport = responder()
        client = embedder(transport, passage_prefix="passage: ", query_prefix="query: ")

        await client.embed_passages(["a"])
        await client.embed_query("a")

        assert seen[0]["payload"]["input"] == ["passage: a"]
        assert seen[1]["payload"]["input"] == ["query: a"]


class TestWhatItRefuses:
    async def test_a_vector_of_the_wrong_width_is_refused_rather_than_stored(self) -> None:
        """It would fail at INSERT instead, as a driver error, in a log."""
        _, transport = responder(width=WIDTH + 1)

        with pytest.raises(EmbeddingServiceError, match="dimension"):
            await embedder(transport).embed_passages(["a"])

    async def test_a_service_that_answers_with_an_error_status_is_reported(self) -> None:
        _, transport = responder(status=503)

        with pytest.raises(EmbeddingServiceError, match="503"):
            await embedder(transport).embed_passages(["a"])

    async def test_a_service_that_answers_something_else_entirely_is_reported(self) -> None:
        _, transport = responder(body={"not": "an embedding"})

        with pytest.raises(EmbeddingServiceError):
            await embedder(transport).embed_passages(["a"])

    async def test_a_short_answer_is_refused_rather_than_silently_padded(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * WIDTH}]})

        with pytest.raises(EmbeddingServiceError, match="2"):
            await embedder(httpx.MockTransport(handle), batch_size=8).embed_passages(["a", "b"])

    async def test_an_unreachable_service_is_reported_as_one(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nothing listening")

        with pytest.raises(EmbeddingServiceError, match="unreachable"):
            await embedder(httpx.MockTransport(handle)).embed_query("a")


class TestHowItIsAddressed:
    async def test_the_path_is_appended_to_the_configured_base_url(self) -> None:
        seen, transport = responder()

        await embedder(transport).embed_query("a")

        assert seen[0]["url"] == "http://embeddings.invalid/v1/embeddings"

    async def test_an_api_key_is_sent_as_a_bearer_token_when_there_is_one(self) -> None:
        seen, transport = responder()

        await embedder(transport, api_key="s3cret").embed_query("a")

        assert seen[0]["headers"]["authorization"] == "Bearer s3cret"

    async def test_no_authorization_header_is_sent_when_there_is_no_key(self) -> None:
        """The self-hosted service has none, and an empty bearer is a 401."""
        seen, transport = responder()

        await embedder(transport).embed_query("a")

        assert "authorization" not in seen[0]["headers"]

    async def test_the_model_it_reports_is_the_one_it_asks_for(self) -> None:
        """It is stored beside every vector, so the two must be the same string."""
        seen, transport = responder()
        client = embedder(transport, model="multilingual-e5-large")

        await client.embed_query("a")

        assert client.model == "multilingual-e5-large" == seen[0]["payload"]["model"]


class TestTheBootProbe:
    async def test_it_embeds_something_and_checks_the_width(self) -> None:
        _, transport = responder()

        await embedder(transport).probe()

    async def test_it_fails_loudly_when_the_width_is_not_the_column_s(self) -> None:
        """The alternative is discovering it on the first upload of the first user."""
        _, transport = responder(width=WIDTH * 2)

        with pytest.raises(EmbeddingServiceError, match="dimension"):
            await embedder(transport).probe()


class TestTheTrace:
    """Off unless `EA_LOG_EMBEDDINGS` opens it — see docs/adr/0021.

    Indexing a corpus is the one thing here that takes minutes, and it is one
    HTTP call per batch to another machine. Without the trace, "the reindex is
    slow" cannot be told apart from "the model is slow" or "the batches are
    tiny".
    """

    def traces(self, caplog: pytest.LogCaptureFixture) -> list[Any]:
        return [record for record in caplog.records if record.name == EMBEDDINGS_LOGGER]

    async def test_one_line_per_batch_actually_sent(self, caplog: pytest.LogCaptureFixture) -> None:
        _, transport = responder()

        with caplog.at_level(logging.DEBUG, logger=EMBEDDINGS_LOGGER):
            await embedder(transport).embed_passages(["a", "b", "c"])

        # batch_size is 2, so three passages are two round trips.
        assert [line.inputs for line in self.traces(caplog)] == [2, 1]

    async def test_the_line_says_which_model_and_how_long_it_took(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _, transport = responder()

        with caplog.at_level(logging.DEBUG, logger=EMBEDDINGS_LOGGER):
            await embedder(transport).embed_query("what restarts the collector?")

        (line,) = self.traces(caplog)
        assert line.model == "bge-m3"
        assert line.duration_ms >= 0

    async def test_it_says_nothing_until_it_is_switched_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _, transport = responder()

        with caplog.at_level(logging.INFO):
            await embedder(transport).embed_query("anything")

        assert self.traces(caplog) == []
