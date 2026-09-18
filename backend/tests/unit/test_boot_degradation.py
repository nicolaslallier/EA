"""A store that serves one section must not be able to take the API down.

The deployed stack answered `502 Bad Gateway` on `/api/health`, `/api/me`,
`/api/elements` and `/api/metamodel` at once — every route, including the one
that needs nothing. It was not a routing fault: the API process was not
running. `minio.probe()` had raised in the lifespan, because the Infra MinIO
had no `ea-api` user yet (docs/adr/0036 records that as work still to do
outside the repo), and a lifespan that raises is a process that exits.

So the catalogue, the metamodel, the IP addressing and the diagrams were all
unreachable because a *file browser* could not reach its bucket — while the
very ADR that added the bucket says a deployment without one "démarre quand
même" and answers 503 on the file routes alone.

These tests hold the rule that resolves it: PostgreSQL holds the whole model
and Keycloak verifies every call, so those two stay fatal; the embedding
service and the bucket serve one section each, so an unreachable one is
recorded and the API boots without it. See docs/adr/0037.
"""

from __future__ import annotations

import logging

import pytest

from ea.core.config import Settings
from ea.domain.errors import FileStorageUnavailableError, SearchUnavailableError
from ea.domain.files import FileListing
from ea.main import FILES, SEARCH, create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryDocuments


def _settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "debug": True,
        "postgres_enabled": False,
        "embeddings_enabled": False,
        "mcp_enabled": False,
        "auth_enabled": False,
        **overrides,
    }
    return Settings(**fields)  # type: ignore[arg-type]


class TestTheBucket:
    """`EA_S3_ENABLED` on, and MinIO out of reach — docs/adr/0036."""

    @pytest.mark.asyncio
    async def test_an_unreachable_bucket_does_not_stop_the_api_from_booting(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        """The failure that produced the 502: boot must survive it."""
        monkeypatch.setattr("ea.main.build_object_store", _refusing_store)
        app = create_app(
            _settings(s3_enabled=True, s3_access_key="key", s3_secret_key="secret"),
            architecture_service=service,
        )

        async with app.router.lifespan_context(app):
            assert app.state.degraded == (FILES,)

    @pytest.mark.asyncio
    async def test_the_file_routes_then_answer_503_and_not_500(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        """Exactly what `EA_S3_ENABLED=false` answers: the section refuses, alone."""
        monkeypatch.setattr("ea.main.build_object_store", _refusing_store)
        app = create_app(
            _settings(s3_enabled=True, s3_access_key="key", s3_secret_key="secret"),
            architecture_service=service,
        )

        async with app.router.lifespan_context(app):
            with pytest.raises(FileStorageUnavailableError):
                await app.state.file_service.list_folder("")

    @pytest.mark.asyncio
    async def test_a_reachable_bucket_leaves_nothing_degraded(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        monkeypatch.setattr("ea.main.build_object_store", _working_store)
        app = create_app(
            _settings(s3_enabled=True, s3_access_key="key", s3_secret_key="secret"),
            architecture_service=service,
        )

        async with app.router.lifespan_context(app):
            assert app.state.degraded == ()
            assert await app.state.file_service.list_folder("") is not None

    @pytest.mark.asyncio
    async def test_the_reason_reaches_the_log_and_not_the_caller(
        self,
        monkeypatch: pytest.MonkeyPatch,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`/health` names the section; which bucket on which host is the log's job."""
        monkeypatch.setattr("ea.main.build_object_store", _refusing_store)
        app = create_app(
            _settings(s3_enabled=True, s3_access_key="key", s3_secret_key="secret"),
            architecture_service=service,
        )

        with caplog.at_level(logging.ERROR, logger="ea.main"):
            async with app.router.lifespan_context(app):
                pass

        assert "no ea-api user on this MinIO" in caplog.text


class TestTheEmbeddingService:
    """Ollama on the cluster, a machine nobody watches — docs/adr/0019, docs/adr/0038."""

    @pytest.mark.asyncio
    async def test_an_unreachable_embedder_does_not_stop_the_api_from_booting(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        monkeypatch.setattr("ea.main.build_embedder", _refusing_embedder)
        app = create_app(
            _settings(embeddings_enabled=True),
            architecture_service=service,
            documents=InMemoryDocuments(),
        )

        async with app.router.lifespan_context(app):
            assert app.state.degraded == (SEARCH,)

    @pytest.mark.asyncio
    async def test_the_search_then_refuses_but_a_document_is_still_stored(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        """The state `EA_EMBEDDINGS_ENABLED=false` already produces, and which
        `make docs-reindex` catches up — never a lost document."""
        monkeypatch.setattr("ea.main.build_embedder", _refusing_embedder)
        app = create_app(
            _settings(embeddings_enabled=True),
            architecture_service=service,
            documents=InMemoryDocuments(),
        )

        async with app.router.lifespan_context(app):
            with pytest.raises(SearchUnavailableError):
                await app.state.document_service.search("quoi que ce soit")

    @pytest.mark.asyncio
    async def test_the_client_is_closed_even_though_the_probe_failed(
        self, monkeypatch: pytest.MonkeyPatch, service: ArchitectureService
    ) -> None:
        """A degraded boot is still a boot: its sockets are the lifespan's to close."""
        embedder = _RefusingEmbedder()
        monkeypatch.setattr("ea.main.build_embedder", lambda _settings: embedder)
        app = create_app(
            _settings(embeddings_enabled=True),
            architecture_service=service,
            documents=InMemoryDocuments(),
        )

        async with app.router.lifespan_context(app):
            pass

        assert embedder.closed is True


class TestWhatStaysFatal:
    """Two dependencies are not a section, and refusing to boot is right for them."""

    @pytest.mark.asyncio
    async def test_a_graph_without_postgres_is_still_refused(self) -> None:
        """PostgreSQL holds the whole model (docs/adr/0033): degrading it would
        serve a catalogue with nothing in it."""
        app = create_app(_settings(postgres_enabled=False))

        with pytest.raises(RuntimeError, match="EA_POSTGRES_ENABLED=true or inject"):
            async with app.router.lifespan_context(app):
                pass  # pragma: no cover - the lifespan must not get this far


class _RefusingStore:
    async def probe(self) -> None:
        msg = "no ea-api user on this MinIO"
        raise RuntimeError(msg)


class _WorkingStore:
    async def probe(self) -> None:
        return None

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing:
        return FileListing(prefix, (), (), truncated=False)


class _RefusingEmbedder:
    model = "a-model"

    def __init__(self) -> None:
        self.closed = False

    async def probe(self) -> None:
        msg = "the embedding service at http://192.168.2.10:11434/v1 is unreachable"
        raise RuntimeError(msg)

    async def aclose(self) -> None:
        self.closed = True


def _refusing_store(_settings: Settings) -> object:
    return _RefusingStore()


def _working_store(_settings: Settings) -> object:
    return _WorkingStore()


def _refusing_embedder(_settings: Settings) -> object:
    return _RefusingEmbedder()
