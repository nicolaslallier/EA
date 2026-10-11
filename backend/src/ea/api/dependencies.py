"""What the routers ask for, and where it comes from.

The service is built once during the application lifespan and handed out here,
so a router never sees the driver or the repository.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from ea.services.audit import AuditService
from ea.services.baselines import BaselineService
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.exchanges import ExchangeService
from ea.services.files import FileService
from ea.services.ipam import IpamService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from ea.services.timeline import TimelineService


def architecture_service_of(app: FastAPI) -> ArchitectureService:
    """The service the lifespan attached, or a clear failure saying it did not.

    Taken from the application rather than the request because the MCP adapter
    needs the same lookup and has no request to ask — see `main._mount_mcp`.
    """
    service: ArchitectureService | None = getattr(app.state, "architecture_service", None)
    if service is None:  # pragma: no cover - a misassembled app, not a request error
        msg = "the architecture service was not attached to the application"
        raise RuntimeError(msg)
    return service


def get_architecture_service(request: Request) -> ArchitectureService:
    return architecture_service_of(request.app)


Architecture = Annotated[ArchitectureService, Depends(get_architecture_service)]


def document_service_of(app: FastAPI) -> DocumentService:
    """The document service the lifespan attached, or a clear failure.

    Absent means the relational store is shut (`EA_POSTGRES_ENABLED`), and the
    documents live in it — see docs/adr/0017. That is a wiring fault rather
    than a request error, so it is a `RuntimeError` and a 500, not a 404
    pretending the element simply has no documents.
    """
    service: DocumentService | None = getattr(app.state, "document_service", None)
    if service is None:
        msg = "no document service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_document_service(request: Request) -> DocumentService:
    return document_service_of(request.app)


Documents = Annotated[DocumentService, Depends(get_document_service)]


def diagram_service_of(app: FastAPI) -> DiagramService:
    """The diagram use cases the lifespan attached, or a clear failure.

    Absent means the relational store is shut, and the diagrams live in it —
    a wiring fault and a 500, never an empty list of diagrams (docs/adr/0031).
    """
    service: DiagramService | None = getattr(app.state, "diagram_service", None)
    if service is None:
        msg = "no diagram service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_diagram_service(request: Request) -> DiagramService:
    return diagram_service_of(request.app)


Diagrams = Annotated[DiagramService, Depends(get_diagram_service)]


def package_service_of(app: FastAPI) -> PackageService:
    """The package use cases the lifespan attached, or a clear failure.

    Absent means the relational store is shut, and the tree lives in it: a
    wiring fault and a 500, never an empty tree a client could not tell from
    a catalogue with nothing filed.
    """
    service: PackageService | None = getattr(app.state, "package_service", None)
    if service is None:
        msg = "no package service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_package_service(request: Request) -> PackageService:
    return package_service_of(request.app)


Packages = Annotated[PackageService, Depends(get_package_service)]


def ipam_service_of(app: FastAPI) -> IpamService:
    """The IPAM use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled with a doubled architecture service and
    no repository behind it: the addressing needs two queries `ElementFilter`
    cannot express (`domain/ports.py`), and there is nothing to answer them
    with. A wiring fault, so a 500 — never an empty inventory, which a client
    could not tell from a network with nothing in it.
    """
    service: IpamService | None = getattr(app.state, "ipam_service", None)
    if service is None:
        msg = "no IPAM service on the application — was it assembled without a graph?"
        raise RuntimeError(msg)
    return service


def get_ipam_service(request: Request) -> IpamService:
    return ipam_service_of(request.app)


Ipam = Annotated[IpamService, Depends(get_ipam_service)]


def audit_service_of(app: FastAPI) -> AuditService:
    """The audit reader the lifespan attached, or a clear failure — a wiring fault, so a 500."""
    service: AuditService | None = getattr(app.state, "audit_service", None)
    if service is None:
        msg = "no audit service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_audit_service(request: Request) -> AuditService:
    return audit_service_of(request.app)


Audit = Annotated[AuditService, Depends(get_audit_service)]


def baseline_service_of(app: FastAPI) -> BaselineService:
    """The baseline use cases the lifespan attached, or a clear failure (a wiring fault: 500)."""
    service: BaselineService | None = getattr(app.state, "baseline_service", None)
    if service is None:
        msg = "no baseline service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_baseline_service(request: Request) -> BaselineService:
    return baseline_service_of(request.app)


Baselines = Annotated[BaselineService, Depends(get_baseline_service)]


def file_service_of(app: FastAPI) -> FileService:
    """The file use cases the lifespan attached, or a clear failure.

    Always attached once the app has started — with no store behind it when
    `EA_S3_ENABLED` is off, in which case every use case answers 503. Absent
    means the app was never started nor handed `files=`: a wiring fault.
    """
    service: FileService | None = getattr(app.state, "file_service", None)
    if service is None:
        msg = "no file service on the application — was it started, or given files=?"
        raise RuntimeError(msg)
    return service


def get_file_service(request: Request) -> FileService:
    return file_service_of(request.app)


Files = Annotated[FileService, Depends(get_file_service)]


def exchange_service_of(app: FastAPI) -> ExchangeService:
    """The exchange use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled without a relational store (a wiring
    fault, so a 500), never an empty export a client would take for a model
    with nothing in it.
    """
    service: ExchangeService | None = getattr(app.state, "exchange_service", None)
    if service is None:
        msg = "no exchange service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_exchange_service(request: Request) -> ExchangeService:
    return exchange_service_of(request.app)


Exchanges = Annotated[ExchangeService, Depends(get_exchange_service)]


def session_factory_of(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """The factory the lifespan attached, or a clear failure saying it did not.

    Absent means `postgres_enabled` is off, which is the default until the
    first table exists — see docs/adr/0015.
    """
    factory: async_sessionmaker[AsyncSession] | None = getattr(app.state, "db_sessions", None)
    if factory is None:
        msg = "no relational session factory on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, closed when the response is done.

    It is not committed here. A transaction spans a use case, not an HTTP
    request, so `services/` opens and commits it — see `CLAUDE.md`.
    """
    async with session_factory_of(request.app)() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def saved_query_service_of(app: FastAPI) -> SavedQueryService:
    """The saved-query use cases the lifespan attached, or a clear failure.

    Absent means the relational store is shut, and the queries live in it: a
    wiring fault and a 500, never an empty list a client could take for "none saved".
    """
    service: SavedQueryService | None = getattr(app.state, "saved_query_service", None)
    if service is None:
        msg = "no saved-query service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_saved_query_service(request: Request) -> SavedQueryService:
    return saved_query_service_of(request.app)


SavedQueries = Annotated[SavedQueryService, Depends(get_saved_query_service)]


def analysis_service_of(app: FastAPI) -> AnalysisService:
    """The analysis use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled with a doubled architecture service and
    no `AnalysisRepository` behind it: a wiring fault and a 500, never an empty
    answer a client could not tell from a model with nothing to report.
    """
    service: AnalysisService | None = getattr(app.state, "analysis_service", None)
    if service is None:
        msg = "no analysis service on the application — was it assembled without a graph?"
        raise RuntimeError(msg)
    return service


def get_analysis_service(request: Request) -> AnalysisService:
    return analysis_service_of(request.app)


Analysis = Annotated[AnalysisService, Depends(get_analysis_service)]


def timeline_service_of(app: FastAPI) -> TimelineService:
    """The plateau, gap and roadmap use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled with a doubled architecture service and no
    `TimelineRepository` behind it: a wiring fault and a 500, never an empty roadmap.
    """
    service: TimelineService | None = getattr(app.state, "timeline_service", None)
    if service is None:
        msg = "no timeline service on the application — was it assembled without a graph?"
        raise RuntimeError(msg)
    return service


def get_timeline_service(request: Request) -> TimelineService:
    return timeline_service_of(request.app)


Timeline = Annotated[TimelineService, Depends(get_timeline_service)]
