"""Make the file catalogue agree with the bucket it describes.

    uv run python -m ea.files_reconcile    # or `make files-reconcile` from the repo root

`file_metadata` is written by this API on every upload and every delete
(docs/adr/0039), and the bucket has another door: the `alimenter-catalogue`
pipeline drops sources into `inbox/`, `mc` and the MinIO console write whatever
an operator tells them to, and none of them knows this table exists. There is
no foreign key that could keep the two in step — the other side of the relation
is an object store — so a catch-up is what keeps them honest.

It is a script and not an endpoint for the same reasons `ea.reindex` is one: it
is an operator's job with no request behind it, it walks a whole bucket, and
running it twice is harmless. What a person wrote about a file is never
touched; only the facts the bucket reports are refreshed, and only rows whose
object is gone are dropped.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from ea.core.config import Settings, get_settings
from ea.core.logging import configure_logging
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.domain.auth import SYSTEM
from ea.domain.files import ReconcileReport
from ea.main import build_object_store
from ea.repositories.file_metadata_store import PostgresFileMetadataRepository
from ea.services.caller import acting_as
from ea.services.files import FileService

logger = logging.getLogger(__name__)


async def reconcile(settings: Settings) -> ReconcileReport:
    """Walk the bucket, refresh every row, drop the rows whose object is gone.

    Both stores are checked before the first object is read — PostgreSQL
    answers, and the bucket exists — so a misconfigured run stops at once
    rather than halfway through, having recorded some of the bucket and
    forgotten none of it.
    """
    engine = create_engine(settings)
    try:
        await check_connectivity(engine)
        store = build_object_store(settings)
        await store.probe()
        service = FileService(
            store, metadata=PostgresFileMetadataRepository(create_session_factory(engine))
        )
        # An operator's script has no request behind it, so it runs as SYSTEM.
        with acting_as(SYSTEM):
            return await service.reconcile()
    finally:
        await engine.dispose()


def main() -> int:
    settings = get_settings()
    # The same configuration the server uses: this is another entry point, and
    # `configure_logging` is never called from `create_app` — see docs/adr/0021.
    configure_logging(settings)
    if not settings.s3_enabled:
        print("EA_S3_ENABLED is off — there is no bucket to reconcile.", file=sys.stderr)
        return 1
    print(f"reconciling {settings.s3_bucket} at {settings.s3_endpoint} ...")
    report = asyncio.run(reconcile(settings))
    print(f"{report.recorded} file(s) recorded, {report.forgotten} row(s) forgotten.")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    raise SystemExit(main())
