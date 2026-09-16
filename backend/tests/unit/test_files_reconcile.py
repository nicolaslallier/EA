"""The script that makes the file catalogue agree with its bucket.

`make files-reconcile` is the catch-up for the one way `file_metadata` and
MinIO drift: a file written or removed by a door that is not this API — the
`alimenter-catalogue` pipeline, `mc`, the MinIO console. No foreign key can
keep them in step, because the other side of the relation is an object store.

What is worth proving without either server is the order it does things in and
the fact that it lets go of what it opened: both stores are checked *before*
the first object is read, and the engine is disposed whether or not that check
passed. See docs/adr/0039; the reconciling itself is `test_file_service.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ea import files_reconcile as script
from ea.core.config import Settings
from ea.domain.auth import SYSTEM
from ea.domain.files import ReconcileReport, StoredFile
from ea.services.caller import current_caller
from ea.services.files import FileService


def _settings(**overrides: object) -> Settings:
    return Settings(
        debug=True,
        s3_enabled=True,
        s3_access_key="key",
        s3_secret_key="secret",
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every seam of the script with something that only records."""
    log: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            log.append("engine.closed")

    class Bucket:
        async def probe(self) -> None:
            log.append("bucket.probed")

        async def walk(self, prefix: str = "") -> AsyncIterator[StoredFile]:
            log.append("bucket.walked")
            return
            yield  # pragma: no cover - an empty async generator

    class Metadata:
        def __init__(self, _: object) -> None: ...

        async def all_keys(self) -> tuple[str, ...]:
            return ()

    async def check(_: object) -> None:
        log.append("engine.checked")

    monkeypatch.setattr(script, "create_engine", lambda _: Engine())
    monkeypatch.setattr(script, "build_object_store", lambda _: Bucket())
    monkeypatch.setattr(script, "check_connectivity", check)
    monkeypatch.setattr(script, "create_session_factory", lambda _: object())
    monkeypatch.setattr(script, "PostgresFileMetadataRepository", Metadata)
    return log


@pytest.mark.asyncio
class TestTheRun:
    async def test_both_stores_are_checked_before_an_object_is_read(self, steps: list[str]) -> None:
        """Half a bucket recorded and none of it forgotten is a worse state than none."""
        await script.reconcile(_settings())

        assert steps.index("engine.checked") < steps.index("bucket.walked")
        assert steps.index("bucket.probed") < steps.index("bucket.walked")

    async def test_it_reports_what_it_changed(self, steps: list[str]) -> None:
        assert await script.reconcile(_settings()) == ReconcileReport(recorded=0, forgotten=0)

    async def test_it_closes_what_it_opened(self, steps: list[str]) -> None:
        await script.reconcile(_settings())

        assert "engine.closed" in steps

    async def test_it_closes_what_it_opened_even_when_the_bucket_is_missing(
        self, steps: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe failing is the expected failure, not an excuse to leak a pool."""

        class Refusing:
            async def probe(self) -> None:
                msg = "the bucket 'ea-catalogue' does not exist on this MinIO"
                raise RuntimeError(msg)

        monkeypatch.setattr(script, "build_object_store", lambda _: Refusing())

        with pytest.raises(RuntimeError):
            await script.reconcile(_settings())

        assert "engine.closed" in steps


@pytest.mark.asyncio
async def test_it_runs_as_system(
    steps: list[str], monkeypatch: pytest.MonkeyPatch, nobody_calling: None
) -> None:
    """An operator's script has no request behind it, so it calls as SYSTEM."""
    seen: list[object] = []

    async def recording_reconcile(self: FileService) -> ReconcileReport:
        seen.append(current_caller.get())
        return ReconcileReport(recorded=0, forgotten=0)

    monkeypatch.setattr(FileService, "reconcile", recording_reconcile)

    await script.reconcile(_settings())

    assert seen == [SYSTEM]


class TestTheEntryPoint:
    def test_it_refuses_to_run_where_there_is_no_bucket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reconciling a catalogue of a store nothing reaches is a long way to do nothing."""
        monkeypatch.setattr(script, "get_settings", lambda: Settings(debug=True))

        assert script.main() == 1

    def test_it_says_what_it_changed_and_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(script, "get_settings", _settings)
        monkeypatch.setattr(
            script.asyncio,
            "run",
            lambda coroutine: (coroutine.close(), ReconcileReport(recorded=7, forgotten=2))[1],
        )

        assert script.main() == 0
        assert "7 file(s) recorded, 2 row(s) forgotten." in capsys.readouterr().out
