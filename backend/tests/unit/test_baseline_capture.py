"""The operator's capture, wired against doubles rather than servers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from ea import baseline_capture as script
from ea.core.config import Settings
from ea.domain.audit import AuditChannel
from ea.domain.auth import SYSTEM
from ea.domain.baselines import Baseline
from ea.domain.errors import (
    BaselineTooLargeError,
    DuplicateBaselineError,
    NotAuthenticatedError,
    NotAuthorisedError,
)
from ea.services.baselines import BaselineService
from tests.conftest import InMemoryAuditLog, InMemoryBaselines


@pytest.mark.asyncio
class TestTheCapture:
    async def test_it_captures_as_the_system_with_no_request_behind_it(
        self,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
        nobody_calling: None,
    ) -> None:
        baseline = await script.capture(
            baseline_service, name="avant mise en service", description=""
        )

        (event,) = audit_log.events
        assert baseline.created_by_sub == SYSTEM.subject
        assert event.actor_sub == SYSTEM.subject
        assert event.channel is AuditChannel.SYSTEM
        assert event.request_id == "-"

    async def test_it_closes_the_engine_it_opened(
        self,
        baseline_store: InMemoryBaselines,
        monkeypatch: pytest.MonkeyPatch,
        nobody_calling: None,
    ) -> None:
        log: list[str] = []

        class Engine:
            async def dispose(self) -> None:
                log.append("engine.closed")

        async def check(_: Any) -> None:
            log.append("engine.checked")

        monkeypatch.setattr(script, "create_engine", lambda _: Engine())
        monkeypatch.setattr(script, "check_connectivity", check)
        monkeypatch.setattr(script, "create_session_factory", lambda _: object())
        monkeypatch.setattr(script, "PostgresBaselineRepository", lambda _: baseline_store)

        baseline = await script.run(Settings(debug=True), "v1", "")

        assert baseline.name == "v1"
        assert log == ["engine.checked", "engine.closed"]


class TestTheArguments:
    def test_a_name_is_required(self) -> None:
        with pytest.raises(SystemExit):
            script.parse_arguments([])

    def test_the_description_is_optional(self) -> None:
        arguments = script.parse_arguments(["--name", "v1"])

        assert (arguments.name, arguments.description) == ("v1", "")


class TestTheEntryPoint:
    def test_it_reports_what_was_captured(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        stored = Baseline(
            id=uuid4(),
            name="v1",
            description="",
            created_by_sub=SYSTEM.subject,
            created_by="ea-system",
            created_at=datetime(2026, 10, 11, tzinfo=UTC),
            element_count=3,
            relationship_count=2,
            box_count=1,
        )

        async def fake_run(_: Settings, name: str, description: str) -> Baseline:
            assert (name, description) == ("v1", "why")
            return stored

        monkeypatch.setattr(script, "run", fake_run)
        monkeypatch.setattr(script, "get_settings", lambda: Settings(debug=True))
        monkeypatch.setattr(script, "configure_logging", lambda _: None)

        assert script.main(["--name", "v1", "--description", "why"]) == 0
        assert "3 element(s), 2 relationship(s), 1 box(es)" in capsys.readouterr().out


class TestTheRefusalsAreSaidInOneLine:
    @staticmethod
    def _failing(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
        async def fake_run(_: Settings, name: str, description: str) -> Baseline:
            raise error

        monkeypatch.setattr(script, "run", fake_run)
        monkeypatch.setattr(script, "get_settings", lambda: Settings(debug=True))
        monkeypatch.setattr(script, "configure_logging", lambda _: None)

    def test_a_name_already_taken_says_how_to_choose_another(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._failing(monkeypatch, DuplicateBaselineError("name taken"))

        assert script.main(["--name", "v1"]) == 1
        err = capsys.readouterr().err
        assert "name taken" in err
        assert "NAME=" in err

    @pytest.mark.parametrize(
        "error",
        [
            BaselineTooLargeError("too many elements"),
            NotAuthorisedError("needs ea-editor"),
            NotAuthenticatedError("nobody"),
            ValueError("a baseline name cannot be blank"),
        ],
    )
    def test_any_other_refusal_is_a_message_and_a_failure_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
    ) -> None:
        self._failing(monkeypatch, error)

        assert script.main(["--name", "v1"]) == 1
        assert str(error) in capsys.readouterr().err
