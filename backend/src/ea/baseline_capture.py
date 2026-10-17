"""Freeze the whole model into a baseline, from a terminal.

    uv run python -m ea.baseline_capture --name "Avant mise en service 2026-10-11"
    # or `make baseline-capture CONFIRM=yes` from the repo root

The backfill of revision 0011 gives the history a first line, but it makes no
baseline (docs/adr/0047): the operator takes one before the service goes live, so
there is something to compare against and to go back to. It is a script and
not an endpoint because it is an operator's job with no request behind it; it
acts as SYSTEM, which the audit trail then shows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from ea.core.config import Settings, get_settings
from ea.core.logging import configure_logging
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.domain.auth import SYSTEM
from ea.domain.baselines import Baseline
from ea.domain.errors import DomainError, DuplicateBaselineError
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as
from ea.services.packages import PackageService


async def capture(service: BaselineService, *, name: str, description: str) -> Baseline:
    with acting_as(SYSTEM):
        return await service.create(name=name, description=description)


async def run(settings: Settings, name: str, description: str) -> Baseline:
    """Check the store answers before capturing, and let go of the pool whatever happens."""
    engine = create_engine(settings)
    try:
        await check_connectivity(engine)
        sessions = create_session_factory(engine)
        packages = PackageService(PostgresPackageRepository(sessions))
        service = BaselineService(PostgresBaselineRepository(sessions), freezes=packages.freezes)
        return await capture(service, name=name, description=description)
    finally:
        await engine.dispose()


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m ea.baseline_capture", description=__doc__)
    parser.add_argument("--name", required=True, help="the name of the baseline (unique)")
    parser.add_argument("--description", default="", help="what this baseline is for")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    settings = get_settings()
    configure_logging(settings)
    try:
        baseline = asyncio.run(run(settings, arguments.name, arguments.description))
    except DuplicateBaselineError as error:
        print(
            f"refusé : {error}\nChoisis un autre nom : make baseline-capture CONFIRM=yes NAME='…'",
            file=sys.stderr,
        )
        return 1
    except (DomainError, ValueError) as error:
        print(f"refusé : {error}", file=sys.stderr)
        return 1
    print(
        f"baseline {baseline.name!r} captured: {baseline.element_count} element(s), "
        f"{baseline.relationship_count} relationship(s), {baseline.box_count} box(es)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    raise SystemExit(main())
