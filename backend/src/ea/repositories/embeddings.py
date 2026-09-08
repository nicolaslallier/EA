"""The embedding service, reached over HTTP — the one outbound call this makes.

It is a repository in the sense this package means: an adapter implementing a
port the domain declared (`Embedder`), with the protocol details kept out of
everything above it. That it speaks HTTP rather than Bolt or SQL changes where
it sits not at all.

**It speaks the OpenAI `/embeddings` shape**, and that is a decision rather
than a convenience. Both candidates for the deployed service answer it — Ollama
and Hugging Face's text-embeddings-inference — as do the hosted providers, so
swapping one for another is a base URL and a model name in the environment
rather than a second client. See docs/adr/0019.

**A vector of the wrong width never reaches the store.** The column is
`vector(1024)`; a service quietly configured with another model would otherwise
fail at `INSERT`, as a driver error, in a log, after the upload was accepted.
The width is checked here, on every answer, and once more at boot by `probe`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from time import perf_counter
from typing import Any, Final

import httpx

from ea.core.logging import EMBEDDINGS_LOGGER

logger = logging.getLogger(__name__)

#: The call trace, on its own switch — see `ea.core.logging`. Indexing a corpus
#: is one HTTP round trip per batch to another machine, and it is the only
#: thing here that takes minutes; without this, "the reindex is slow" cannot be
#: told apart from "the model is slow".
traffic = logging.getLogger(EMBEDDINGS_LOGGER)

#: Appended to the configured base URL, which is expected to end in `/v1`.
EMBEDDINGS_PATH: Final = "/embeddings"

#: What `probe` embeds at boot. Any short string does; this one says why it is
#: in the logs of whatever is serving the model.
PROBE_TEXT: Final = "ea boot probe"


class EmbeddingServiceError(RuntimeError):
    """The embedding service was unreachable, refused, or answered nonsense.

    A `RuntimeError` and not a `DomainError`: nothing about the request was
    wrong, a machine this one depends on is down. It reaches a client as a 500
    and the details reach the log, exactly like the relational store's own
    unavailability — see `db/postgres.py`.
    """


class HttpEmbedder:
    """An OpenAI-compatible embedding endpoint, held as one pooled client.

    The client is built once per process and closed by the application
    lifespan, for the same reason the Neo4j driver and the SQLAlchemy engine
    are: it owns a connection pool.

    `passage_prefix` and `query_prefix` exist because several model families —
    the e5 line most notably — are trained with a different instruction on a
    stored passage and on a question, and get materially worse recall without
    it. `bge-m3`, the model docs/adr/0019 deploys, wants neither, so both
    default to empty.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        api_key: str = "",
        timeout: float = 30.0,
        batch_size: int = 32,
        passage_prefix: str = "",
        query_prefix: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._batch_size = max(1, batch_size)
        self._passage_prefix = passage_prefix
        self._query_prefix = query_prefix
        self._url = base_url.rstrip("/") + EMBEDDINGS_PATH
        # No header at all rather than an empty bearer: the self-hosted service
        # has no key, and `Authorization: Bearer ` is a 401 on anything that
        # looks at the header before deciding it does not need one.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)
        if client is not None and headers:
            self._client.headers.update(headers)

    @property
    def model(self) -> str:
        """The name stored beside every vector this produces."""
        return self._model

    async def embed_passages(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Embed passages for storage, in the order they were given.

        Batched, and the batches are sent one after another rather than at
        once: the service is usually a single CPU-bound model, and flooding it
        with parallel requests makes a document slower to index, not faster.
        """
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed([f"{self._passage_prefix}{text}" for text in batch]))
        return tuple(vectors)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed one question, to be compared against stored passages."""
        return (await self._embed([f"{self._query_prefix}{text}"]))[0]

    async def probe(self) -> None:
        """Fail at boot rather than on the first upload of the first user.

        The same discipline as `check_connectivity` next door, plus the one
        check only this can make: that the model actually configured produces
        vectors of the width the column was created with.
        """
        await self._embed([PROBE_TEXT])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _embed(self, inputs: list[str]) -> list[tuple[float, ...]]:
        """One request, and everything that can be wrong with its answer."""
        if not inputs:
            return []
        started = perf_counter()
        try:
            response = await self._client.post(
                self._url, json={"model": self._model, "input": inputs}
            )
        except httpx.HTTPError as error:
            logger.error("the embedding service is unreachable", exc_info=error)
            msg = f"the embedding service at {self._url} is unreachable"
            raise EmbeddingServiceError(msg) from error
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # The body may carry the model name and the host; the caller gets
            # the status, the log gets the rest.
            logger.error(
                "the embedding service refused the request",
                extra={"status": response.status_code, "body": response.text[:500]},
            )
            msg = f"the embedding service answered {response.status_code}"
            raise EmbeddingServiceError(msg)
        vectors = self._vectors_of(response, expected=len(inputs))
        traffic.debug(
            "embedded %d input(s) with %s",
            len(inputs),
            self._model,
            extra={
                "model": self._model,
                "inputs": len(inputs),
                "characters": sum(len(text) for text in inputs),
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            },
        )
        return vectors

    def _vectors_of(self, response: httpx.Response, *, expected: int) -> list[tuple[float, ...]]:
        """Read the answer, in the order it was asked for, at the declared width.

        `index` is honoured rather than the position in the list: it is in the
        response format precisely because a batching server is free to answer
        out of order, and a silently permuted batch attaches every passage's
        vector to its neighbour.
        """
        try:
            payload: dict[str, Any] = response.json()
            data = payload["data"]
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [tuple(float(value) for value in item["embedding"]) for item in ordered]
        except (ValueError, KeyError, TypeError) as error:
            logger.error("the embedding service answered an unreadable body", exc_info=error)
            msg = "the embedding service answered something that is not an embedding"
            raise EmbeddingServiceError(msg) from error
        if len(vectors) != expected:
            msg = f"asked the embedding service for {expected} vectors and got {len(vectors)}"
            raise EmbeddingServiceError(msg)
        wrong = next((vector for vector in vectors if len(vector) != self._dimensions), None)
        if wrong is not None:
            msg = (
                f"{self._model} answers vectors of dimension {len(wrong)}, "
                f"and the index stores {self._dimensions}"
            )
            raise EmbeddingServiceError(msg)
        return vectors
