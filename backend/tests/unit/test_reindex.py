"""The script that rebuilds the index, wired against doubles rather than servers.

`make docs-reindex` is the catch-up for the two ways an index can fall behind
its store — a document attached while `EA_EMBEDDINGS_ENABLED` was off, and a
corpus whose embedding model changed. What is worth proving without a database
is the order it does things in and the fact that it lets go of what it opened:
everything is checked *before* the first document is read, and the engine, the
driver and the HTTP client are closed whether or not that check passed.

See docs/adr/0019. The reindexing itself is covered in `test_indexing.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from ea import reindex as script
from ea.core.config import Settings
from ea.repositories.embeddings import EmbeddingServiceError


class Recorder:
    """Stands in for every resource the script opens, and remembers the order."""

    def __init__(self, log: list[str], name: str) -> None:
        self._log = log
        self._name = name

    def note(self, what: str) -> None:
        self._log.append(f"{self._name}.{what}")

    async def dispose(self) -> None:
        self.note("closed")

    async def close(self) -> None:
        self.note("closed")

    async def aclose(self) -> None:
        self.note("closed")

    async def probe(self) -> None:
        self.note("probed")

    @property
    def model(self) -> str:
        return "fake-embed"


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every seam of the script with something that only records."""
    log: list[str] = []
    engine, embedder, driver = (Recorder(log, name) for name in ("engine", "embedder", "driver"))

    async def check(_: object) -> None:
        log.append("engine.checked")

    class Documents:
        def __init__(self, _: object) -> None: ...

        async def all_document_ids(self) -> tuple[Any, ...]:
            log.append("documents.walked")
            return ()

    monkeypatch.setattr(script, "create_engine", lambda _: engine)
    monkeypatch.setattr(script, "build_embedder", lambda _: embedder)
    monkeypatch.setattr(script, "create_driver", lambda _: driver)
    monkeypatch.setattr(script, "check_connectivity", check)
    monkeypatch.setattr(script, "create_session_factory", lambda _: object())
    monkeypatch.setattr(script, "PostgresDocumentRepository", Documents)
    monkeypatch.setattr(script, "Neo4jArchitectureRepository", lambda *a, **k: object())
    return log


@pytest.mark.asyncio
class TestTheRun:
    async def test_everything_is_checked_before_a_document_is_read(self, steps: list[str]) -> None:
        """A misconfigured run must stop at once, not halfway through a corpus."""
        await script.reindex(Settings(debug=True))

        assert steps.index("engine.checked") < steps.index("documents.walked")
        assert steps.index("embedder.probed") < steps.index("documents.walked")

    async def test_it_reports_how_many_documents_it_rebuilt(self, steps: list[str]) -> None:
        assert await script.reindex(Settings(debug=True)) == 0

    async def test_it_closes_what_it_opened(self, steps: list[str]) -> None:
        await script.reindex(Settings(debug=True))

        assert {"engine.closed", "embedder.closed", "driver.closed"} <= set(steps)

    async def test_it_closes_what_it_opened_even_when_the_model_is_wrong(
        self, steps: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe failing is the expected failure, not an excuse to leak sockets."""

        async def refuse() -> None:
            raise EmbeddingServiceError("wrong dimension")

        embedder = script.build_embedder(Settings(debug=True))
        monkeypatch.setattr(embedder, "probe", refuse)

        with pytest.raises(EmbeddingServiceError):
            await script.reindex(Settings(debug=True))

        assert {"engine.closed", "embedder.closed", "driver.closed"} <= set(steps)


class TestTheEntryPoint:
    def test_it_refuses_to_run_where_there_is_no_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rebuilding an index nothing reads is a long way to do nothing."""
        monkeypatch.setattr(
            script, "get_settings", lambda: Settings(debug=True, embeddings_enabled=False)
        )

        assert script.main() == 1

    def test_it_says_what_it_rebuilt_and_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(script, "get_settings", lambda: Settings(debug=True))
        monkeypatch.setattr(script.asyncio, "run", lambda coroutine: (coroutine.close(), 7)[1])

        assert script.main() == 0
        assert "7 document(s) reindexed." in capsys.readouterr().out
