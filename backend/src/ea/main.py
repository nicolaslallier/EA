"""FastAPI application factory and the `fastapi dev` / `uvicorn` entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Final
from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from ea.api.analysis import router as analysis_router
from ea.api.architecture import router as architecture_router
from ea.api.audit import router as audit_router
from ea.api.auth import Authenticated, verifier_of
from ea.api.baselines import router as baselines_router
from ea.api.body_limit import BODY_ENVELOPE_MARGIN, LimitingRequestBodies
from ea.api.dependencies import (
    analysis_service_of,
    architecture_service_of,
    audit_service_of,
    baseline_service_of,
    document_service_of,
    exchange_service_of,
    file_service_of,
    ipam_service_of,
    package_service_of,
    saved_query_service_of,
)
from ea.api.diagrams import router as diagrams_router
from ea.api.documents import router as documents_router
from ea.api.errors import AnsweringUnexpectedFailures, register_error_handlers
from ea.api.exchanges import router as exchanges_router
from ea.api.files import router as files_router
from ea.api.health import router as health_router
from ea.api.ipam import router as ipam_router
from ea.api.me import router as me_router
from ea.api.metamodel import router as metamodel_router
from ea.api.middleware import REQUEST_ID_HEADER, RequestLogging
from ea.api.packages import router as packages_router
from ea.api.queries import router as queries_router
from ea.api.schemas import ErrorResponse
from ea.core.config import Settings, get_settings
from ea.core.logging import configure_logging
from ea.db.postgres import check_connectivity as check_relational_store
from ea.db.postgres import create_api_engine, create_session_factory
from ea.domain.auth import Caller
from ea.domain.ports import (
    AccessTokenVerifier,
    AnalysisRepository,
    AuditRepository,
    BaselineRepository,
    DiagramRepository,
    DocumentRepository,
    ElementFilter,
    ExchangeRepository,
    FileMetadataRepository,
    IpamRepository,
    ObjectStore,
    PackageRepository,
    SavedQueryRepository,
)
from ea.domain.quality import MAX_QUALITY_ELEMENTS, QualityParams
from ea.domain.search import EMBEDDING_DIMENSIONS
from ea.mcp import MCP_PATH, build_mcp_server
from ea.mcp.auth import AsTheLocalDeveloper, KeycloakTokenVerifier, auth_settings
from ea.mcp.transport import LoopbackClientsOnly
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.document_store import PostgresDocumentRepository
from ea.repositories.embeddings import HttpEmbedder
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.repositories.file_metadata_store import PostgresFileMetadataRepository
from ea.repositories.keycloak import JwtVerifier, http_client_for
from ea.repositories.object_store import MinioObjectStore, minio_client
from ea.repositories.package_store import PostgresPackageRepository
from ea.repositories.saved_query_store import PostgresSavedQueryRepository
from ea.services.analysis import AnalysisService, ScopeResolver, analysis_admission
from ea.services.architecture import ArchitectureService
from ea.services.audit import AuditService
from ea.services.baselines import BaselineService
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.exchange_admission import Admission
from ea.services.exchanges import MAX_PARSES_QUEUED, ExchangeService
from ea.services.files import FileService
from ea.services.indexing import DocumentIndexer
from ea.services.ipam import IpamService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService

logger = logging.getLogger(__name__)

#: The two stores that serve one section each, named as `/health` reports them
#: when their boot probe failed — see docs/adr/0037.
#:
#: `SEARCH` is the embedding service behind `search_documents`; without it a
#: document is still stored, and `make docs-reindex` catches the index up.
#: `FILES` is the MinIO bucket behind the *Fichiers* section; without it the
#: `/files` routes and the four MCP file tools answer 503, exactly as they do
#: where `EA_S3_ENABLED` is off.
SEARCH: Final = "search"
FILES: Final = "files"


async def _reachable(name: str, probe: Callable[[], Awaitable[None]], degraded: list[str]) -> bool:
    """Run a peripheral store's boot probe; record it rather than refuse to boot.

    PostgreSQL and Keycloak are not routed through here, and that is the whole
    distinction: they are owed by every route, so a process that cannot reach
    them has nothing to serve and stops. These two are owed by one section
    each, and the services above them are already built to do without — a
    `FileService` with no store, a `DocumentService` with no indexer. Letting
    their probe end the process turned a file browser's missing bucket into a
    `502 Bad Gateway` on `/health`, `/me`, `/elements` and `/metamodel` alike.

    The `except` is blind for the same reason it is in `check_connectivity`:
    there is no failure of a probe that means anything other than "this store
    is not usable", and the original belongs in the log, where the host, the
    bucket and the model may be named. What reaches a caller is the name above
    and nothing else.
    """
    try:
        await probe()
    except Exception as error:
        logger.error(
            "%s is degraded: its boot probe failed, so the section it serves will refuse",
            name,
            exc_info=error,
            extra={"action": "subsystem_degraded", "subsystem": name},
        )
        degraded.append(name)
        return False
    return True


def build_scope_resolver(app: FastAPI) -> ScopeResolver:
    """What a package or a saved query stands for, as element ids (docs/adr/0056).

    At most one more element than a run takes: a scope past the ceiling is refused by the
    service that has the ceiling, not truncated here. The page a query's author chose is
    not its scope, so `limit` and `offset` are replaced.
    """

    async def resolve(package_id: UUID | None, query_id: UUID | None) -> tuple[UUID, ...]:
        if query_id is not None:
            criteria = ElementFilter.from_query(
                (await saved_query_service_of(app).get(query_id)).query
            )
        else:
            criteria = ElementFilter(package_id=package_id, include_subpackages=True)
        found = await architecture_service_of(app).list_elements(
            replace(criteria, limit=MAX_QUALITY_ELEMENTS + 1, offset=0)
        )
        return tuple(element.id for element in found)

    return resolve


def build_quality_params(settings: Settings) -> QualityParams:
    """The quality knobs, validated: a bad `EA_QUALITY_REQUIRED` stops the boot."""
    return QualityParams.from_settings(settings.quality_similarity, settings.quality_required)


def build_embedder(settings: Settings) -> HttpEmbedder:
    """The embedding client this process talks to, built from settings alone.

    The width is `EMBEDDING_DIMENSIONS` and not a setting: it is the width of
    the stored column, so a model of another width is a migration and a full
    reindex — see docs/adr/0019. Passing it here is what makes the client
    refuse such a model at boot instead of at the first `INSERT`.
    """
    return HttpEmbedder(
        base_url=settings.embeddings_base_url,
        model=settings.embeddings_model,
        dimensions=EMBEDDING_DIMENSIONS,
        api_key=settings.embeddings_api_key.get_secret_value(),
        timeout=settings.embeddings_timeout_seconds,
        batch_size=settings.embeddings_batch_size,
        passage_prefix=settings.embeddings_passage_prefix,
        query_prefix=settings.embeddings_query_prefix,
    )


def build_verifier(settings: Settings) -> JwtVerifier:
    """Keycloak's key set for the realm `ea`, reached through the Infra CA when one is given."""
    return JwtVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        http=http_client_for(settings.auth_ca_cert, settings.auth_timeout_seconds),
    )


def build_object_store(settings: Settings) -> MinioObjectStore:
    """The bucket of `s3_bucket` on `s3_endpoint` — see docs/adr/0036."""
    return MinioObjectStore(minio_client(settings), settings.s3_bucket)


def _lifespan(
    settings: Settings,
    *,
    open_graph: bool,
    documents: DocumentRepository | None = None,
    indexer: DocumentIndexer | None = None,
    diagrams: DiagramRepository | None = None,
    packages: PackageRepository | None = None,
    queries: SavedQueryRepository | None = None,
    file_metadata: FileMetadataRepository | None = None,
    audit: AuditRepository | None = None,
    baselines: BaselineRepository | None = None,
    exchanges: ExchangeRepository | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Start and stop everything the process owns, however it was assembled.

    PostgreSQL holds the whole model since docs/adr/0033 — the graph, the
    documents and their passages, the diagrams — so its pool is opened and
    checked first, and a process that cannot reach it does not start. Its
    schema is Alembic's, applied before the process starts, never here.

    The embedding service owns an HTTP connection pool, so it is built once
    and closed here, and it is *probed* at boot: the model configured must
    answer vectors of the width the column was created with. That probe, and
    the bucket's, go through `_reachable`: they serve one section each, so an
    unreachable one is recorded on `app.state.degraded` and the API boots
    without it, rather than the whole deployment answering 502 — docs/adr/0037.

    The MCP transport keeps its sessions in a manager that has to be running
    before `/mcp` answers anything; it is picked up off `app.state`, where
    `_mount_mcp` left it, because the manager only exists once the app it is
    mounted on does.

    An `AsyncExitStack` composes them: an app built with `architecture_service=`
    opens no graph but must still start the session manager.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if open_graph and not settings.postgres_enabled:
            # Refused before anything opens: without the store there is no
            # graph, and every catalogue request would otherwise be a 500.
            msg = (
                "the architecture graph lives in PostgreSQL (docs/adr/0033): "
                "set EA_POSTGRES_ENABLED=true or inject architecture_service"
            )
            raise RuntimeError(msg)
        degraded: list[str] = []
        async with AsyncExitStack() as stack:
            # PostgreSQL first: the graph, the documents and the diagrams all
            # live there, so every service below is built on its sessions.
            logger.info(
                "starting %s",
                settings.app_name,
                extra={
                    "graph": open_graph,
                    "postgres": settings.postgres_enabled,
                    "embeddings": settings.embeddings_enabled,
                    "mcp": settings.mcp_enabled,
                    "auth": settings.auth_enabled,
                    "log_level": settings.log_level,
                    "s3": settings.s3_enabled,
                },
            )
            if settings.auth_enabled and getattr(app.state, "token_verifier", None) is None:
                verifier = build_verifier(settings)
                stack.push_async_callback(verifier.aclose)
                await verifier.probe()
                app.state.token_verifier = verifier
                logger.info("tokens verified against %s", settings.auth_issuer)
            elif not settings.auth_enabled:
                logger.warning(
                    "authentication is off: every caller is the local developer, "
                    "with the editor role"
                )
            document_store = documents
            diagram_store = diagrams
            package_store = packages
            query_store = queries
            metadata_store = file_metadata
            audit_store = audit
            baseline_store = baselines
            exchange_store: ExchangeRepository | None = None
            if settings.postgres_enabled:
                engine = create_api_engine(settings)
                stack.push_async_callback(engine.dispose)
                await check_relational_store(engine)
                app.state.db_sessions = create_session_factory(engine)
                if document_store is None:
                    document_store = PostgresDocumentRepository(app.state.db_sessions)
                if diagram_store is None:
                    diagram_store = PostgresDiagramRepository(app.state.db_sessions)
                if package_store is None:
                    package_store = PostgresPackageRepository(app.state.db_sessions)
                if query_store is None:
                    query_store = PostgresSavedQueryRepository(app.state.db_sessions)
                if metadata_store is None:
                    metadata_store = PostgresFileMetadataRepository(app.state.db_sessions)
                if audit_store is None:
                    audit_store = PostgresAuditRepository(app.state.db_sessions)
                if baseline_store is None:
                    baseline_store = PostgresBaselineRepository(app.state.db_sessions)
                logger.info(
                    "relational store ready at %s:%s/%s",
                    settings.postgres_host,
                    settings.postgres_port,
                    settings.postgres_database,
                )
                if open_graph:
                    repository = PostgresArchitectureRepository(app.state.db_sessions)
                    logger.info(
                        "architecture graph ready in PostgreSQL %s:%s/%s",
                        settings.postgres_host,
                        settings.postgres_port,
                        settings.postgres_database,
                    )
                    app.state.architecture_service = ArchitectureService(
                        repository, separate_duties=settings.approval_separation
                    )
                    # The IP addressing is a reading of that same graph and adds
                    # no store, so it is built from the very repository above —
                    # see docs/adr/0020.
                    app.state.ipam_service = IpamService(app.state.architecture_service, repository)
                    app.state.analysis_service = AnalysisService(
                        app.state.architecture_service,
                        repository,
                        params=app.state.quality_params,
                        admission=app.state.analysis_admission,
                        scope_ids=build_scope_resolver(app),
                    )
                    exchange_store = exchanges or PostgresExchangeRepository(app.state.db_sessions)
            # The index is a table beside the documents, so an embedding client
            # is opened only where there are documents to index: a deployment
            # with the relational store shut has neither.
            index = indexer
            if index is None and settings.embeddings_enabled and document_store is not None:
                embedder = build_embedder(settings)
                # Pushed before the probe, so a boot that goes on without the
                # service still closes the pool it opened.
                stack.push_async_callback(embedder.aclose)
                if await _reachable(SEARCH, embedder.probe, degraded):
                    index = DocumentIndexer(embedder)
                    logger.info(
                        "embedding service ready: %s at %s",
                        settings.embeddings_model,
                        settings.embeddings_base_url,
                    )
            elif index is None and settings.embeddings_enabled:
                logger.warning("embeddings are on but there is no document store to index")
            if document_store is not None:
                app.state.document_service = DocumentService(
                    document_store, architecture_service_of(app), indexer=index
                )
            if package_store is not None:
                app.state.package_service = PackageService(package_store)
            if query_store is not None:
                app.state.saved_query_service = SavedQueryService(
                    query_store, package_store, architecture_service_of(app)
                )
            if diagram_store is not None:
                app.state.diagram_service = DiagramService(
                    diagram_store, architecture_service_of(app), packages=package_store
                )
            if exchange_store is not None:
                app.state.exchange_service = ExchangeService(
                    exchange_store,
                    architecture_service_of(app),
                    diagrams=getattr(app.state, "diagram_service", None),
                    packages=getattr(app.state, "package_service", None),
                    queries=getattr(app.state, "saved_query_service", None),
                    max_bytes=settings.exchange_max_bytes,
                    max_new_folders=settings.exchange_max_new_folders,
                    max_exports=settings.exchange_max_exports,
                    exports_per_caller=settings.exchange_exports_per_caller,
                    export_lease_seconds=settings.exchange_export_lease_s,
                    imports=app.state.import_admission,
                )
            if audit_store is not None:
                app.state.audit_service = AuditService(audit_store)
            if baseline_store is not None:
                app.state.baseline_service = BaselineService(baseline_store)
            # Files live in MinIO, beside every other store (docs/adr/0036). The
            # service is attached either way, so a deployment without it answers
            # 503 on every file route and tool instead of 500.
            if getattr(app.state, "file_service", None) is None:
                store: ObjectStore | None = None
                if settings.s3_enabled:
                    minio = build_object_store(settings)
                    if await _reachable(FILES, minio.probe, degraded):
                        store = minio
                        logger.info(
                            "file storage ready: bucket %s at %s",
                            settings.s3_bucket,
                            settings.s3_endpoint,
                        )
                # The catalogue beside the bucket (docs/adr/0039). It is a table
                # of the relational store, which is fatal at boot, so it is
                # there whenever the store is — and `None` only on the test
                # seam that builds an app without PostgreSQL at all.
                app.state.file_service = FileService(store, metadata=metadata_store)
            # Published once every probe has run: `/health` reads it, and it is
            # the only place an operator is told which store is missing without
            # opening the logs — see docs/adr/0037.
            app.state.degraded = tuple(degraded)
            sessions = getattr(app.state, "mcp_sessions", None)
            if sessions is not None:
                await stack.enter_async_context(sessions.run())
                logger.info("MCP tools served at %s", MCP_PATH)
                if settings.mcp_allow_remote_clients:
                    # Said at every boot, because it is the one setting that
                    # opens the graph's write path to the whole network — see
                    # docs/adr/0023. With auth on a token still decides who
                    # writes (docs/adr/0032); behind a proxy every peer is the
                    # proxy either way.
                    if settings.auth_enabled:
                        logger.warning(
                            "MCP tools are served to remote clients: a Keycloak token is "
                            "still required, but behind a proxy every peer looks like the proxy"
                        )
                    else:
                        logger.warning(
                            "MCP tools are served to remote clients without authentication "
                            "(debug only)"
                        )
            logger.info(
                "%s is up and answering on %s:%s",
                settings.app_name,
                settings.host,
                settings.port,
                extra={"degraded": ",".join(degraded)},
            )
            yield
            logger.info("shutting down")

    return lifespan


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    """Which `Host` and `Origin` headers `/mcp` answers.

    The origins are derived from the hosts rather than configured twice: an
    entry is a host and an optional port, and a browser reaching it does so
    over one of the two schemes. Anything not on the list is a 421.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=[
            f"{scheme}://{allowed}"
            for allowed in settings.mcp_allowed_hosts
            for scheme in ("http", "https")
        ],
    )


class _LazyVerifier:
    """The app's verifier, looked up per call: the lifespan builds it after `_mount_mcp`."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def verify(self, token: str) -> Caller:
        return await verifier_of(self._app).verify(token)


def _mount_mcp(app: FastAPI, settings: Settings) -> None:
    """Serve the MCP tools at `/mcp`, on the app that already serves the API.

    The SDK hands back a Starlette application whose single route is the
    transport. Its routes are spliced onto this app rather than `mount`ed,
    because `Mount("/mcp", ...)` matches only `/mcp/...`: a bare `POST /mcp` —
    the address every client is given — would be answered with a 307 redirect,
    and a client is not obliged to follow one. Splicing keeps the exact path.

    Two consequences worth knowing. The route is a Starlette `Route` and not an
    `APIRoute`, so it stays out of the OpenAPI schema — `/mcp` describes itself
    over MCP, and the generated TypeScript client neither sees it nor needs
    regenerating for it. And the sub-application's own lifespan is dropped,
    which is why its session manager is handed to `_lifespan` instead.

    All four services are looked up per call, off `app.state`, for the same
    reason: they are built by the lifespan and this runs while the app is still
    being assembled. A deployment with the relational store shut therefore serves the
    document tools and fails them one by one — the wiring fault the REST
    adapter answers with a 500, said in the other protocol.

    The transport security is stated rather than inferred. Given a `host`, the
    SDK enables DNS-rebinding protection *only* when that host is loopback — so
    handing it `settings.host` silently switched the protection off the day the
    API started binding every interface. The allowlist is its own setting
    instead.

    That allowlist protects against a browser, not against a caller: it checks
    a header any script writes. Who is served is decided in front of it, from
    the TCP peer — loopback only, unless `mcp_allow_remote_clients` says
    otherwise (docs/adr/0023). The guard wraps the route itself rather than
    matching the path in a middleware, so it cannot drift from the path the
    transport is actually served on.

    Behind that guard `/mcp` wants the same bearer token as the REST API
    (docs/adr/0032): the SDK's resource-server hooks over the app's verifier,
    so a request without one gets a 401 pointing at the protected-resource
    metadata, itself one more spliced route. The SDK puts authentication in
    the transport's *middleware* — two of them — which splicing its routes
    would drop, so each route is wrapped in that stack itself, innermost
    first, with the loopback guard outermost: a remote peer is refused before
    it is invited to log in. With auth off (debug only) each call acts as
    `LOCAL_DEVELOPER` instead, stated here rather than left to whatever the
    context happened to hold.
    """
    auth = settings.auth_enabled
    server = build_mcp_server(
        lambda: architecture_service_of(app),
        lambda: document_service_of(app),
        lambda: ipam_service_of(app),
        lambda: file_service_of(app),
        lambda: package_service_of(app),
        lambda: saved_query_service_of(app),
        lambda: audit_service_of(app),
        lambda: baseline_service_of(app),
        lambda: exchange_service_of(app),
        lambda: analysis_service_of(app),
        version=app.version,
        token_verifier=KeycloakTokenVerifier(_LazyVerifier(app)) if auth else None,
        auth=auth_settings(settings) if auth else None,
    )
    transport = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=_transport_security(settings),
    )
    for route in transport.routes:
        if not isinstance(route, Route):  # pragma: no cover - the SDK ships Routes only
            msg = f"cannot wrap an MCP route of type {type(route).__name__}"
            raise RuntimeError(msg)
        route_app = route.app if auth else AsTheLocalDeveloper(route.app)
        for middleware in reversed(transport.user_middleware):
            route_app = middleware.cls(route_app, *middleware.args, **middleware.kwargs)
        if not settings.mcp_allow_remote_clients:
            route_app = LoopbackClientsOnly(route_app)
        route.app = route_app
    app.router.routes.extend(transport.routes)
    app.state.mcp_sessions = server.session_manager


#: Every route but `/health` carries `Authenticated`, so every one of them can
#: now refuse for these two reasons — see `ea/api/auth.py`, docs/adr/0032.
AUTH_RESPONSES: dict[int | str, dict[str, type[ErrorResponse]]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}


def create_app(
    settings: Settings | None = None,
    *,
    architecture_service: ArchitectureService | None = None,
    documents: DocumentRepository | None = None,
    indexer: DocumentIndexer | None = None,
    ipam: IpamRepository | None = None,
    analysis: AnalysisRepository | None = None,
    diagrams: DiagramRepository | None = None,
    packages: PackageRepository | None = None,
    queries: SavedQueryRepository | None = None,
    files: ObjectStore | None = None,
    file_metadata: FileMetadataRepository | None = None,
    audit: AuditRepository | None = None,
    baselines: BaselineRepository | None = None,
    exchanges: ExchangeRepository | None = None,
    verifier: AccessTokenVerifier | None = None,
) -> FastAPI:
    """Assemble the application.

    Taking `settings` as an argument keeps the app testable without touching
    the process environment. Passing `architecture_service` swaps the graph for
    a double, so an API test never needs a running database — and, conversely,
    an app built without one builds it on the relational store at startup.
    `documents` does the same for the relational store: given one, the document endpoints answer
    without PostgreSQL; given none, the lifespan builds the real repository
    when `postgres_enabled` says the store is open.

    `indexer` does the same for the embedding service: given one, the search
    answers without a model being loaded anywhere; given none, the lifespan
    builds and probes the real client when `embeddings_enabled` says so.

    `diagrams` does the same for the saved diagrams (docs/adr/0031), and
    `packages` for the classification tree and `queries` for the saved queries.
    `files` does the same for the bucket (docs/adr/0036), and `file_metadata`
    for the catalogue kept beside it (docs/adr/0039), and `audit` for the audit
    trail (docs/adr/0046), and `baselines` for the baselines (docs/adr/0047).
    `verifier` does the same for authentication: given one — `StaticVerifier`
    in tests, a `JwtVerifier` over `httpx.MockTransport` — every route answers
    without reaching Keycloak; given none, the lifespan builds and probes the
    real `JwtVerifier` when `auth_enabled` says so.

    An injected pair is wired here rather than in the lifespan, because an API
    test drives the app through `ASGITransport` without ever starting it.
    """
    settings = settings or get_settings()

    # `settings.debug` is deliberately not handed to FastAPI. There it means
    # "answer an unhandled exception with the traceback page", and this API
    # binds every interface (docs/adr/0016) while `.env.example` ships
    # `EA_DEBUG=true`: every file path and local variable on the stack would go
    # to whoever on the LAN made the request fail. `debug` keeps its own
    # meanings — the relaxed password rule, readable logs — and a failure goes
    # to the log, never to the response (`api/errors.py`).
    app = FastAPI(
        title=settings.app_name,
        lifespan=_lifespan(
            settings,
            open_graph=architecture_service is None,
            documents=documents,
            indexer=indexer,
            diagrams=diagrams,
            packages=packages,
            queries=queries,
            file_metadata=file_metadata,
            audit=audit,
            baselines=baselines,
            exchanges=exchanges,
        ),
    )
    app.state.settings = settings
    # Validated here, so a wrong `EA_QUALITY_REQUIRED` stops the boot (docs/adr/0057); and one
    # admission for the analyses that read the whole model, whichever door they come through.
    app.state.quality_params = build_quality_params(settings)
    app.state.analysis_admission = analysis_admission()
    # One admission for every door an import comes through: HTTP's (in the middleware below),
    # MCP's and a script's (in the service), so that three doors cannot hold six places.
    admission = Admission(
        MAX_PARSES_QUEUED,
        per_caller=settings.exchange_imports_per_caller,
        lease_seconds=settings.exchange_import_lease_s,
    )
    app.state.import_admission = admission
    if verifier is not None:
        app.state.token_verifier = verifier
    if architecture_service is not None:
        app.state.architecture_service = architecture_service
        if documents is not None:
            app.state.document_service = DocumentService(
                documents, architecture_service, indexer=indexer
            )
        if ipam is not None:
            app.state.ipam_service = IpamService(architecture_service, ipam)
        if analysis is not None:
            app.state.analysis_service = AnalysisService(
                architecture_service,
                analysis,
                params=app.state.quality_params,
                admission=app.state.analysis_admission,
                scope_ids=build_scope_resolver(app),
            )
        if packages is not None:
            app.state.package_service = PackageService(packages)
        if queries is not None:
            app.state.saved_query_service = SavedQueryService(
                queries, packages, architecture_service
            )
        if diagrams is not None:
            app.state.diagram_service = DiagramService(
                diagrams, architecture_service, packages=packages
            )
        if exchanges is not None:
            app.state.exchange_service = ExchangeService(
                exchanges,
                architecture_service,
                diagrams=getattr(app.state, "diagram_service", None),
                packages=getattr(app.state, "package_service", None),
                queries=getattr(app.state, "saved_query_service", None),
                max_bytes=settings.exchange_max_bytes,
                max_new_folders=settings.exchange_max_new_folders,
                max_exports=settings.exchange_max_exports,
                exports_per_caller=settings.exchange_exports_per_caller,
                export_lease_seconds=settings.exchange_export_lease_s,
                imports=admission,
            )
    if files is not None:
        app.state.file_service = FileService(files, metadata=file_metadata)
    if audit is not None:
        app.state.audit_service = AuditService(audit)
    if baselines is not None:
        app.state.baseline_service = BaselineService(baselines)

    # Added first, so it is the *innermost* layer: the envelope it sends for an
    # unhandled failure then passes back out through CORS and the request id,
    # like every other answer. Starlette's own `Exception` handler sits outside
    # both and stays only as a last resort — see `api/errors.py`.
    # The ceiling on an import's body is innermost of all (docs/adr/0050): its 413 is then an
    # ordinary answer to CORS and the request id, and the disconnect it hands the route on
    # overflow never reaches the net below as an unexpected failure.
    app.add_middleware(
        LimitingRequestBodies,
        path_suffix="/exchanges/imports",
        limit=settings.exchange_max_bytes + BODY_ENVELOPE_MARGIN,
        admission=admission,
        read_timeout=settings.exchange_read_timeout_s,
        total_timeout=settings.exchange_read_total_s,
    )
    app.add_middleware(AnsweringUnexpectedFailures)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # A browser cannot read a response header it was not told about, and
        # the request id is only useful to the SPA if it can put it in its own
        # console line — see docs/adr/0021. The same goes for the name of an
        # exported file, the count of views it left out and the `Retry-After` of
        # a 429: none is CORS-safelisted, and the SPA's Échanges screen reads all three.
        expose_headers=[
            REQUEST_ID_HEADER,
            "Content-Disposition",
            "X-Export-Skipped-Views",
            "Retry-After",
        ],
    )
    # Added last, so it is the outermost layer: it then times and reports the
    # CORS preflight and the 404 as readily as a route that ran.
    app.add_middleware(RequestLogging)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(metamodel_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(architecture_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(documents_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(diagrams_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(packages_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(queries_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(ipam_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(analysis_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(files_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(exchanges_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(audit_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(baselines_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    app.include_router(me_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
    if settings.mcp_enabled:
        _mount_mcp(app, settings)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Build `ea.main:app` on first access rather than at import.

    `uvicorn ea.main:app` resolves the attribute after importing the module, so
    this keeps the familiar entry point while making sure the environment is
    only read when the process actually intends to serve. Importing `ea.main`
    in a test no longer requires a fully configured deployment.
    """
    if name != "app":
        raise AttributeError(name)
    global _app
    if _app is None:
        # Logging is configured here and not in `create_app`, because this is
        # the process entry point: a test that builds an app must not
        # reconfigure the logging of the process running it. uvicorn has
        # already installed its own by now, and this deliberately replaces it
        # — see docs/adr/0021.
        settings = get_settings()
        configure_logging(settings)
        _app = create_app(settings)
    return _app
