"""What an agent's call leaves behind.

`/mcp` writes to the architecture graph and, until auth exists, does so without
authenticating anybody (docs/adr/0014). A tool call that leaves no trace is
therefore the one write to this catalogue nobody can account for afterwards —
which is why the tracing lives in `speaking_plainly`, the decorator every tool
already carries, rather than in a third one somebody has to remember.
"""

from __future__ import annotations

import logging

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ea.core.logging import TOOLS_LOGGER
from ea.domain.errors import ElementNotFoundError
from ea.mcp.errors import speaking_plainly


@speaking_plainly
async def answering(name: str, text: str = "") -> str:
    """A tool that works."""
    return f"hello {name}{text}"


@speaking_plainly
async def refusing(name: str) -> str:
    raise ElementNotFoundError(f"no element named {name}")


@speaking_plainly
async def breaking(name: str) -> str:
    raise RuntimeError("the driver is on fire")


def lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == TOOLS_LOGGER]


@pytest.mark.asyncio
class TestWhatIsTraced:
    async def test_a_call_says_which_tool_and_how_long(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO):
            await answering("srv-app-01")

        (line,) = lines(caplog)
        assert line.tool == "answering"  # type: ignore[attr-defined]
        assert line.outcome == "ok"  # type: ignore[attr-defined]
        assert line.duration_ms >= 0  # type: ignore[attr-defined]

    async def test_the_arguments_are_there_for_whoever_asks_for_them(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """At DEBUG: the tool and the outcome are the everyday line."""
        with caplog.at_level(logging.DEBUG, logger=TOOLS_LOGGER):
            await answering(name="srv-app-01")

        opening = lines(caplog)[0]
        assert opening.levelno == logging.DEBUG
        assert opening.arguments == {"name": "srv-app-01"}  # type: ignore[attr-defined]

    async def test_a_document_sent_by_an_agent_is_not_printed_whole(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=TOOLS_LOGGER):
            await answering(name="x", text="y" * 9000)

        opening = lines(caplog)[0]
        assert opening.arguments["text"].endswith("(9000 chars)")  # type: ignore[attr-defined]

    async def test_a_refusal_is_the_agent_being_told_something_and_not_a_fault(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO), pytest.raises(ToolError):
            await refusing("ghost")

        (line,) = [record for record in lines(caplog) if record.levelno == logging.INFO]
        assert line.outcome == "refused"  # type: ignore[attr-defined]
        assert "ghost" in line.getMessage()
        assert line.exc_info is None

    async def test_a_crash_keeps_its_traceback_in_the_log_and_out_of_the_answer(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
            await breaking("srv-app-01")

        (line,) = [record for record in lines(caplog) if record.levelno == logging.ERROR]
        assert line.outcome == "failed"  # type: ignore[attr-defined]
        assert line.exc_info is not None


@pytest.mark.asyncio
async def test_the_signature_the_sdk_reads_is_untouched() -> None:
    """`functools.wraps` is load-bearing: the SDK builds the schema from it."""
    assert answering.__name__ == "answering"
    assert answering.__doc__ == "A tool that works."
