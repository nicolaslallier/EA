# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Every declared section works end to end.** A root `Makefile` orchestrates local development. `backend/` serves a FastAPI app with the full ArchiMate 3.2 metamodel, an element/relationship catalogue and two graph traversals, stored in PostgreSQL (see `docs/adr/0033`). `frontend/` is a Vue 3 SPA: a routed shell whose section menu is generated from `src/router/sections.ts` (see `docs/adr/0008`), with eighteen sections built — the element catalogue, which browses, creates, edits and deletes elements through the generated OpenAPI client (see `docs/adr/0007`) and opens the full detail of one when its name is clicked, under `?element=` (see `docs/adr/0011`); relations, which lists the links of one element and adds one, offering only what the metamodel permits for the pair (see `docs/adr/0009`; the same panel opens from a catalogue row); neighbourhood, which *draws* the sub-graph around an element on concentric rings, one per hop, and moves the centre when a neighbour is clicked (see `docs/adr/0010`); metamodel, which reads the ArchiMate 3.2 reference itself — the 61 types by layer, the 11 relationships with their family and the way impact travels, and one row of the 61x61 matrix at a time (see `docs/adr/0012`); impact analysis, which draws the same rings around an element and reads them as how far a failure travels, plus the list of what breaks, wave by wave (see `docs/adr/0013`); IP addressing, which lists the declared subnets with how full each one is, hands out the next free address, and answers "10.0.1.12, that is what?" with the machine *and* what it is wired to (see `docs/adr/0020`); diagrams, which composes an ArchiMate view from the catalogue's elements on a canvas and saves it (see `docs/adr/0031`); and files, which uploads, browses, downloads and deletes files of any type in a MinIO bucket, one folder at a time under `?prefix=` (see `docs/adr/0036`), showing beside each one what PostgreSQL records about it — who deposited it, when, and the title, description and tags a person wrote (see `docs/adr/0039`). Elements and diagrams are filed in a tree of *packages* — a classification with no ArchiMate meaning, `packages` plus a nullable `package_id` on `elements` and `diagrams` (migration `0009`, see `docs/adr/0042`) — and the *Recherche* section runs one pure `ElementQuery` (types, layers, name, package, attribute tests, relations present or absent) that `POST /elements/search` and the MCP tool `search_elements` share and that `saved_queries` (migration `0010`, see `docs/adr/0043`) keeps under a name, shared by every reader; history, which reads the audit trail of every write, for the whole catalogue or for one element (see `docs/adr/0046`); and baselines, which captures, compares and restores snapshots of the whole model (see `docs/adr/0047`). The sheet of an element carries its review banner — draft, in review, approved, locked — whose buttons are the transitions the API lists (see `docs/adr/0048`). The *Échanges* section (`/echanges`, tabs and format in the URL) downloads the model as ArchiMate Exchange XML or Archi-style CSV, for the whole model, a package or a saved query, and imports a file in two steps — a simulation, then a confirmation that re-sends the *same* files only if their SHA-256, computed in the browser, equals the one the simulation reported; the import half is hidden from a reader, the API deciding (see `docs/adr/0050` and `docs/adr/0054`). The model is also *analysed*, with no table and no migration: `/analyse/matrice` crosses two sets of elements (a layer, a type, a package, a saved query, named elements) and creates or removes a link per cell through `/relationships` and nothing else (see `docs/adr/0056`); `/analyse/qualite` lists the findings of seven rules with a stable code, computed on request (see `docs/adr/0057`); `/analyse/tableau-de-bord` aggregates the catalogue by layer, type, package or attribute, with a hand-drawn SVG heat map whose colour is a `level` 0..4 decided by the server (see `docs/adr/0058`). The model is also *dated*: an element or a relationship may carry `valid_from` / `valid_until` (`YYYY-MM-DD`, the last day counts, a missing one is open), two ordinary properties indexed by migration `0014` (see `docs/adr/0059`); the reads take an `as_of` day (`docs/adr/0060`) and the SPA keeps it in a global `?asof=`; and `/analyse/feuille-de-route` draws plateaus, work packages and deliverables on a hand-made SVG roadmap, above the comparison of two plateaus that can draw the `gap` element between them (`docs/adr/0061`). Reports (HTML, PDF, DOCX) of a diagram or a package are built by `ReportService` from one neutral `ReportModel` — downloaded from `GET /reports/render` by any reader, or deposited under `reports/` of the bucket by an editor (`POST /reports`, `generate_report`) through `FileService.upload`, never another door — and every ceiling (500 elements, 2000 relationships, 20 diagrams, 1000 boxes, 2 MiB of documents, 8 MiB of text, 12 000 nodes, 30 s) is checked before the read it bounds, the heavy part running off the event loop under an `Admission` (one report per caller). The diagram is drawn by the one static SVG of `domain/diagram_render.py` (`GET /diagrams/{id}/render.svg`), which the report and the SPA's read-only `/view` mode both show; `/view` is a reader mode of the same SPA — same origin, same login, `GET`s only — and documents are rendered to safe HTML by the server (`GET /documents/{id}/html`). No table, no migration (see `docs/adr/0062`, `0063` and `0064`). `/analyse/couverture` lists the requirements no design realizes and no passing test verifies — a pure, bounded reading of `requirement` / `constraint` elements, their `req_*` and `test_result` properties and `realization` links (junctions crossed), with no table (see `docs/adr/0067`). A `junction` (one of the 61 types, until now refused at creation) is an element like the others, its AND/OR kept in `junction_type` and its Exchange form `AndJunction` / `OrJunction` read and written by the import and export (see `docs/adr/0065`); `PATCH /relationships/{id}` changes a link's name, properties, access type or direction under the locks of both its ends (see `docs/adr/0066`). The same backend also speaks **MCP**: `/mcp` offers the whole catalogue to an agent as sixty-three tools — the element CRUD, the links (and `update_relationship`, which changes what a link says and never its type or ends, see `docs/adr/0066`), the two traversals, the metamodel, the markdown attached to an element, the IP addressing, the files of the bucket, the packages, the structured search, the audit trail, the baselines, the review transitions and the exchange of the model as ArchiMate files (`export_model`, `import_model`) the three read-only analyses (`get_relationship_matrix`, `check_model`, `aggregate_model`) and the three of time (`list_plateaus`, `gap_between`, and `create_gap`, which writes) `get_coverage` (the read-only requirements coverage) and the two of reports (`list_report_templates`, and `generate_report`, which writes a file) — as an adapter *beside* `api/` rather than a client of it, so every ArchiMate rule is enforced for an agent without one line of them being restated (see `docs/adr/0014` and `docs/adr/0018`). This file records the *decisions already made* so that any instance building here converges on the same design instead of inventing its own. When a decision here turns out to be wrong, change this file in the same commit that changes the code, and record the change in `docs/adr/`.

PostgreSQL holds the whole model: the graph in `elements` and `relationships` (migration `0005`, see `docs/adr/0033`), the documents in **two tables**, and what is known about the files of the bucket in a third, `file_metadata` (migration `0008`, see `docs/adr/0039`). `element_documents` stores the markdown files attached to an element — uploaded as `multipart/form-data`, kept as `TEXT`, listed, read and replaced from the catalogue's *Documents* panel (see `docs/adr/0017`), and offered to an agent as text over MCP (see `docs/adr/0018`). `document_chunks` makes those files *findable*: each document is cut at its own headings, every passage is embedded with the trail of headings above it, and the vectors live in the same database under **pgvector** — searchable by an agent through the MCP tool `search_documents` (see `docs/adr/0019`). SQLAlchemy 2 (async), Alembic and the shared PostgreSQL were wired by `docs/adr/0015` — a database that has lived in the `~/OpenCode/Infra` stack on the Mac, not on the cluster, since `docs/adr/0029`; `EA_POSTGRES_ENABLED` is **on** since the first table exists, so a deployment that cannot reach PostgreSQL no longer boots — which is true of Keycloak too, and of nothing else: an unreachable embedder or bucket degrades its own section and `/health` names it (see `docs/adr/0037`).

Two more tables, `diagrams` and `diagram_nodes` (migration `0004`), hold the **saved diagrams** of the diagram builder: a diagram is an ArchiMate *view* — it records which elements are drawn, where, and at what size (`width`/`height`, migration `0007`, see `docs/adr/0035`), and owns no fact, so removing a box never deletes an element and a link drawn on one is a real relationship from `POST /relationships`. `/diagrams` lists, creates, renames and deletes them; `GET /diagrams/{id}` opens one with its elements and the relationships whose two ends are on it (one query, `view_of`); `PUT /diagrams/{id}/layout` replaces every box in one transaction. `diagram_nodes.element_id` is a foreign key to `elements`, like `element_documents.element_id`, so deleting an element takes its boxes and its documents in the same transaction (migration `0006`, see `docs/adr/0033`). The SPA's *Diagrammes* section builds them; there is no MCP tool for diagrams.

**`audit_events`** (migration `0011`) is the history of every write; **`baselines`** and the `baseline_*` copies (`0012`) freeze the model; the review state is columns of `elements` (`0013`); the validity indexes are `0014`. Their sections below say what is decided.

A second Python project, `pipelines/`, sits beside `backend/` with its own
lockfile: Prefect 3 and LiteLLM, self-hosted, running one flow,
`alimenter-catalogue` — it reads a source file from MinIO, asks an LLM for the
ArchiMate elements and relationships it describes, and writes them into the
same catalogue through the EA API, never through `/mcp`, never through a
repository (see `docs/adr/0028`). **Nothing here has been deployed**: the
Docker stack (Prefect server, LiteLLM, the worker) has never run against the
shared cluster, so `docs/adr/0028` is still a *Proposition*.

**Every caller is authenticated by Keycloak**, realm `ea`: the API, `/mcp` and the pipeline's worker each verify or present a bearer token, every read needs a caller and every write the realm role `ea-editor`, decided in `services/` and nowhere else (see `docs/adr/0032`, still a *Proposition* — the realm has not been imported and the stack not redeployed). Approving is a second realm role, `ea-approver`, that does **not** imply `ea-editor`: writing still needs `ea-editor` (see `docs/adr/0048`, also a *Proposition* — the realm import, in the Infra repository, must gain the role).

**Not yet scaffolded** (do not assume these exist): Playwright.

`EA` = Enterprise Architecture. Expect domain modelling (capabilities, applications, flows, owners) to be the core of the backend, not CRUD-for-its-own-sake.

## Locked stack decisions

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic | Typed end to end; the OpenAPI schema is the front/back contract |
| Metamodel | ArchiMate 3.2, complete | 61 element types, 11 relationship types, rules-based validation — see `docs/adr/0005` |
| IP addressing | Properties of the existing elements, no new table | A subnet is a `communication_network`, an address is an attribute of the machine — so it is in the catalogue, the neighbourhood and the impact analysis for free — see `docs/adr/0020` |
| File storage | SeaweedFS S3 of the Infra (was MinIO, `docs/adr/0056`), one bucket (`ea-catalogue`), through the API — never presigned | One door for the SPA and an agent, authorisation in `services/` — see `docs/adr/0036` |
| File metadata | A `file_metadata` row per object, beside the bucket — never `x-amz-meta-*` | MinIO knows no uploader and no description, and S3 metadata is frozen at write, unsearchable and unfilterable — see `docs/adr/0039` |
| Storage | PostgreSQL + SQLAlchemy 2 (async, `asyncpg`) + Alembic | One database for the whole model: the graph as two tables walked by recursive CTEs, the documents and their index, the diagrams — see `docs/adr/0033` |
| Document search | pgvector in that same PostgreSQL, `vector(1024)` + HNSW | The corpus is small and already there; a third store would be a third consistency to keep — see `docs/adr/0019` |
| Embeddings | An OpenAI-shaped `/v1/embeddings` — Ollama on the cluster (`192.168.2.10:11434/v1`), `mxbai-embed-large` | Measured against the alternative on French prose; the API shape, not the supplier, is what we depend on — which is why changing supplier was a base URL and a model name — see `docs/adr/0019`, `docs/adr/0038`, and `docs/adr/0040` for the port and the `/v1` suffix |
| Reports | `markdown-it-py` (safe markdown), WeasyPrint (PDF, needs Pango in the API image), `python-docx` (DOCX); HTML is always served, a format whose engine does not load is a typed 501 | Three serialisers of one neutral model; a library that cannot load degrades one format, not the API — see `docs/adr/0062` |
| Python tooling | `uv` (deps + venv), `ruff` (lint + format), `mypy --strict` | Single fast toolchain, one lockfile |
| Agent-facing API | MCP (`mcp` SDK 2.x), streamable HTTP served at `/mcp` | A second adapter over the same service, not a second API — see `docs/adr/0014` |
| Authentication | Keycloak realm `ea`; EA a resource server (`pyjwt[crypto]` + the realm's JWKS over `httpx`); the SPA on `oidc-client-ts`, code + PKCE | Keycloak already runs in the Infra; a gateway would cover neither `/mcp` nor the pipeline and would trust a header — see `docs/adr/0032` |
| Frontend | Vue 3 (`<script setup>`) + TypeScript + Vite | SPA consuming the generated OpenAPI client — see `docs/adr/0002` |
| Frontend routing | `vue-router` 5, `history` mode | Routes and menu are both derived from one section catalogue — see `docs/adr/0008` |
| Frontend tests | Vitest + Testing Library, Playwright for E2E | Unit/component in-process, E2E against a real stack |
| Containers | Docker + `docker compose` for the throwaway PostgreSQL the integration tests run against | Reproducible, and a destructive test never reaches a shared database — see `docs/adr/0024` |
| Pipeline orchestration | Prefect 3, `flow.serve(name="manuel", limit=1)` — no work pool, no `prefect.yaml` | One flow, triggered by hand while the process runs, is the whole need so far — see `docs/adr/0028` |
| LLM gateway | LiteLLM, behind the aliases `smart`/`fast` only (`pipelines/litellm.yaml`) | The code never names a real model; the deployment decides which one answers each alias — see `docs/adr/0028` |
| Pipeline object storage | MinIO of the `~/OpenCode/Infra` stack, read-only from `pipelines/` | The pipeline reads a source file, it is not that store's lifecycle manager — see `docs/adr/0028` |

Do not introduce a second HTTP client, ORM, state manager, or test runner alongside these without recording an ADR.

## Repository layout

```
Makefile                 # single entry point for local dev and the quality gate — see docs/adr/0001, 0026
docker-compose.yml       # throwaway PostgreSQL for the integration tests, on 127.0.0.1 only
deploy/ea.stack.yml      # the API and the SPA as a Portainer stack behind the Infra NGINX — see docs/adr/0027
scripts/portainer-stack.sh  # `make app-up/down/restart/delete`: that stack driven through Portainer's API
{backend,frontend}/Dockerfile  # the two images that stack builds
.pre-commit-config.yaml  # opt-in hooks (`make hooks`): ruff, mypy, vue-tsc, ESLint, gitleaks
.github/                 # workflows/ci.yml and dependabot.yml — see docs/adr/0026
backend/
  src/ea/
    api/           # FastAPI routers, request/response schemas, dependencies
      auth.py      # the bearer dependency on every router but health; me.py is GET /me
    mcp/           # the same service offered to an agent as MCP tools
      auth.py      # /mcp as a resource server of the realm — see docs/adr/0032
      transport.py # /mcp answers loopback peers only — see docs/adr/0023
    core/          # config (pydantic-settings), logging
    domain/        # entities, value objects, domain services — NO framework imports
      archimate/   # the ArchiMate 3.2 metamodel: taxonomy, relations, rules
      approval.py  # review states, transitions, the one guard every element write asks — docs/adr/0048
      audit.py     # AuditEvent, diff_fields, the cursor — docs/adr/0046
      auth.py      # Caller, EDITOR_ROLE, APPROVER_ROLE, LOCAL_DEVELOPER, SYSTEM — no JWT here
      baselines.py # Baseline, ModelSnapshot, diff_snapshots, plan_restore — docs/adr/0047
      chunking.py  # where a markdown document is cut, and what is embedded
      diagrams.py  # a saved view: which elements are drawn, and where — no fact of its own
      matrix.py    # the relationship matrix: a pure grid of two axes — docs/adr/0056
      quality.py   # the quality rules, each with a stable code, and `run_checks` — docs/adr/0057
      analytics.py # property readings, dimensions, aggregates, the colour `level` — docs/adr/0058
      timeline.py  # Lifespan, validity dates, plateau diff, the roadmap build — docs/adr/0059, 0061
      requirements.py # requirement vocabularies and the coverage computation — docs/adr/0067
      junctions.py # junction_type (and/or) validation — docs/adr/0065
      reports.py   # report templates, the neutral ReportModel, its ceilings — docs/adr/0062
      report_html.py   # the self-contained HTML serialiser of a ReportModel
      diagram_render.py # the one static SVG drawing of a diagram — docs/adr/0063
      markdown_html.py  # markdown to safe HTML (raw HTML and images off)
      packages.py  # the classification tree: names, cycles, depth — no ArchiMate meaning
      query.py     # ElementQuery: the one structured filter, and its stored JSON
      saved_queries.py # a named, shared ElementQuery
      ipam.py      # what an IP address is, where it may live, what is free
      files.py     # what a stored file is, which paths are allowed, and what is
                 #   known about it — see docs/adr/0036 and docs/adr/0039
      search.py    # what the passage index holds and answers with
    reindex.py     # `python -m ea.reindex` — rebuild the index over the whole corpus
    files_reconcile.py # `python -m ea.files_reconcile` — make the file catalogue
                 #   agree with the bucket (docs/adr/0039)
    baseline_capture.py # `python -m ea.baseline_capture` — the snapshot before go-live
    graph_import.py # the one-off copy from Neo4j (docs/adr/0033), deleted after the cut-over
    services/      # use cases; orchestrate domain + repositories, own transactions
      caller.py    # current_caller, require_caller, require_editor, acting_as
      analysis.py  # AnalysisService: matrix, quality run, aggregates — reads only, admitted
      timeline.py  # TimelineService: plateaus, the gap between two, the roadmap — docs/adr/0061
      requirements.py # RequirementService: the coverage, read only, bounded — docs/adr/0067
      reports.py   # ReportService: build (any reader), deposit (an editor) — docs/adr/0062
      files.py     # FileService: list/open/read for a reader, upload/delete for an editor
    repositories/  # implementations of the ports declared in domain: SQL,
                 #   file_metadata_store.py — what is known about the files
                 #   of that bucket (docs/adr/0039), and the outbound HTTP
                 #   clients (the embedding service, keycloak.py — the realm's
                 #   keys and the JWT check, object_store.py — the MinIO
                 #   bucket, docs/adr/0036, report_renderers.py — the PDF and DOCX
                 #   engines, which need native libraries, docs/adr/0062)
    db/            # the PostgreSQL engine, session factory and declarative base
    db/models/     # every mapped table — the one module Alembic autogenerates from
  migrations/      # Alembic revisions for PostgreSQL — the graph included since 0005
  scripts/import_neo4j.py  # `make graph-import`, same lifetime
  tests/{unit,integration,e2e}/
frontend/
  src/api/                             # GENERATED ONLY — never hand-write there
  src/router/                          # the section catalogue, the routes it produces, the 404
  src/features/                        # one directory per screen: components + its composables
                                        #   (matrix/, quality/, dashboard/ — the three analysis screens)
                                        #   (e.g. features/files/ — the bucket browser, docs/adr/0036)
  src/{components,lib}/                # shared components (the shell menu, the graph drawing, `UserBadge`); hand-written glue (the API client, the ring geometry, `latest.ts`, `debounce.ts`, `auth.ts` — the login — and `me.ts`)
  tests/                               # Vitest specs, mirroring src/
pipelines/                             # second Python project, own pyproject.toml + uv.lock — docs/adr/0028
  src/pipelines/
    settings.py                        # PIPELINES_-prefixed env config: the EA API, LiteLLM, MinIO
    llm.py                             # one validated call to the gateway's /v1/chat/completions
    ea.py                              # EaClient: the only door into the EA catalogue, HTTP only
    storage.py                         # MinIO read-only access: list a prefix, read one object as text
    auth.py                            # ClientCredentials: the worker's token, as the client ea-pipelines
    catalogue.py                       # the alimenter-catalogue flow and its @task steps
    __main__.py                        # `python -m pipelines` — alimenter_catalogue.serve(...)
  tests/                               # MockTransport / fake S3 doubles, its own socket guard
  docker-compose.yml                   # Prefect server + LiteLLM + the worker, ports on 127.0.0.1 only
  Dockerfile                           # the worker image only
  litellm.yaml                         # the smart/fast aliases and their real models
docs/adr/                              # architecture decision records
```

**Dependency direction is one-way: `api → services → domain ← repositories`.** `mcp/` sits *beside* `api/` in that arrow — same distance from the domain, never behind it. `domain/` imports nothing from FastAPI, SQLAlchemy, or `api/`. A test that needs a database is not a unit test — move it to `tests/integration/`.

## Commands

Everyday local development *and* the quality gate go through the root `Makefile`
(`make help` lists every target):

```bash
make install                    # uv sync --all-extras + npm ci + pipelines-install (the lockfiles, exactly)
make run                        # backend and frontend in parallel, interleaved logs
make run-be                     # backend only  — binds 0.0.0.0:8000, reachable on the LAN
make run-fe                     # frontend only — binds 0.0.0.0:5173, prints the LAN origin
make run-be BE_PORT=8001        # every port is an overridable variable
make clean                      # drop .venv, node_modules, caches, build output
```

Checking and fixing are two targets, because a check that fixes as it goes can
never fail. `make check` is what CI runs, and **it modifies no file** — see
`docs/adr/0026`:

```bash
make lint                       # FIXES: ruff format, then ruff check --fix
make lint-check                 # verifies only: ruff format --check, ruff check
make lint-fe                    # ESLint on the frontend, no fixing
make typecheck                  # typecheck-be (mypy --strict src migrations) + typecheck-fe (vue-tsc)
make test                       # backend unit + API suites with --cov=ea: fails under 90%
make test-unit                  # fast loop, no coverage
make test-fe                    # Vitest, one pass
make check                      # lint-check lint-fe typecheck openapi-check test test-fe pipelines-check
make audit                      # bandit, pip-audit --skip-editable, npm audit --audit-level=high (network)
make hooks                      # opt-in: install the pre-commit hooks into .git
make pg-up                      # start the throwaway PostgreSQL (127.0.0.1)
make minio-up                   # start the throwaway MinIO (127.0.0.1:9100)
make test-integration           # the throwaway PostgreSQL and MinIO, started if needed — never the shared ones
make test-postgres              # only the `postgres`-marked tests, against the throwaway PostgreSQL
make pg-down                    # stop the throwaway container (alias: compose-down)
make compose-up                 # the throwaway container, waiting until healthy
make compose-ps                 # compose-logs, compose-reset drops the PostgreSQL volume
make app-up                     # create or redeploy the deployed stack through Portainer's API
make app-ps                     # app-logs s=api, app-down, app-restart, app-delete CONFIRM=yes
make graph-import CONFIRM=yes   # once: copy the Neo4j graph into PostgreSQL, then migration 0006
```

Pipelines (from the repo root; `pipelines-check` is folded into `make check`,
`pipelines-install` into `make install` — see `docs/adr/0028`):

```bash
make pipelines-install                  # uv sync --all-extras in pipelines/
make pipelines-lint                     # FIXES: ruff format, then ruff check --fix
make pipelines-lint-check               # verifies only
make pipelines-typecheck                # mypy --strict src
make pipelines-test                     # pytest --cov=pipelines: fails under 90%
make pipelines-check                    # lint-check + typecheck + test
make pipelines-audit                    # bandit, pip-audit --skip-editable
make pipelines-db-howto                 # prints the manual PostgreSQL/MinIO setup — runs nothing
make pipelines-up                       # docker compose up: Prefect server + LiteLLM + the worker
make pipelines-down                     # stop that stack
make pipelines-logs                     # docker compose logs -f
make pipelines-run PREFIX=inbox/        # trigger alimenter-catalogue/manuel in the worker
```

Backend (run from `backend/`):

```bash
uv sync --all-extras            # install/refresh the venv from uv.lock
uv run uvicorn ea.main:app --reload    # or `make run-be` from the repo root
uv run pytest                   # everything; integration tests skip unless pointed at throwaway stores
uv run pytest tests/unit -q     # fast loop, no DB
uv run pytest tests/unit/test_ipam.py -x  # one file — without --cov, which would fail the floor
make docs-reindex               # rebuild the passage index over every stored document
make files-reconcile            # make the file catalogue agree with the bucket
make baseline-capture CONFIRM=yes  # writes a baseline into the SHARED database (NAME=, DESCRIPTION= optional)
uv run alembic upgrade head     # or `make pg-migrate` — targets the SHARED database
uv run ruff format . && uv run ruff check --fix .
uv run mypy src migrations
uv run bandit -c pyproject.toml -r src
uv run pip-audit --skip-editable
```

Frontend (run from `frontend/`):

```bash
npm ci
npm run dev
npm test -- --run                       # Vitest once (no watch)
npm test -- tests/BackendStatus.spec.ts  # single file/dir
npm run test:e2e                         # Playwright — NOT SET UP YET
npm run lint && npm run typecheck         # ESLint (eslint.config.js), then vue-tsc
npm run generate:api                     # regenerate src/api/ — or `make openapi` from the root
```

Whole stack: `make run`. `make run-be` also serves the MCP tools at <http://127.0.0.1:8000/mcp>; the committed `.mcp.json` points Claude Code at it, logging in as the Keycloak client `ea-mcp` on `callbackPort` 33418 — the one redirect URI that client allows, so the two change together. That login runs inside Claude Code, a Node process that reads neither `EA_AUTH_CA_CERT` nor, by default, the macOS keychain: start Claude Code with `NODE_EXTRA_CA_CERTS=~/OpenCode/Infra/certs/infra-ca.crt` or its discovery of Keycloak fails TLS — and should the discovery itself fail, `.mcp.json`'s `oauth.authServerMetadataUrl` can name Keycloak's metadata outright (deliberately not set). `EA_MCP_ENABLED=false` turns it off; by default it answers only clients on this machine (`docs/adr/0023`), and only with a token (`docs/adr/0032`). PostgreSQL is the one shared instance, in the Infra stack: since `docs/adr/0029` the `ea` database lives there, behind the same NGINX on `127.0.0.1:5432`, and `deploy/postgres.stack.yml` is deployed nowhere. `make pg-ping` checks it, `make pg-stack` recalls how to provision it, `make pg-migrate` applies the Alembic chain to it, and `make pg-up` starts only the throwaway container the integration tests use — see `docs/adr/0015`. **Its image must carry pgvector** (`pgvector/pgvector:pgNN`), because the document index is a `vector` column; `make pg-vector-check` says so before a migration discovers it — and says it only after actually reaching the server, since a check that cannot connect knows nothing about the extension. **Every `psql` target uses the Mac's own client when there is one**, and falls back to one in a container: the container was the only path, and it adds two failures the database does not have — a stopped Docker daemon, and a container that does not reach the LAN the Mac reaches. Both used to be reported as the second one. **And every target names the database `backend/.env` names**: the host, the port, the role and the database — like the embedder's base URL and model — are read from it at parse time by `backend/scripts/dotenv_get.py`, the environment winning over the file and `make pg-ping POSTGRES_HOST=…` over both. They were constants in the `Makefile` (`127.0.0.1`, `ea`) while the Python read `EA_POSTGRES_*`, and nothing held the two together: from a machine that does not host the database, `make docs-reindex` announced « Cible : 127.0.0.1/ea, la base PARTAGÉE » and then reindexed somewhere else, while `pg-ping`, `pg-backup` and `pg-restore` — which hand those coordinates to `psql` — really did aim at the loopback. A red banner naming the shared database is worth nothing unless it is the one the command opens. The embedding service is what is left on the Docker cluster (192.168.2.10) — Ollama, serving an OpenAI-shaped `/v1/embeddings` on port **11434**, its own (`docs/adr/0038` wrote 11435 without probing it; `docs/adr/0040` is the correction). **The `/v1` is part of the base URL**, everywhere it is written: `HttpEmbedder` appends `/embeddings` and nothing else, so a base URL without it reaches a 404 — a degraded search whose log names a cause that is not the one. `make embed-ping` checks the model answers and at what width, `make embed-models` lists what it holds. Nothing here starts either. Two deployments were degraded by the same wrong port, the second one *because* the repository had been corrected: `docs/adr/0038` made the stack's default credible, `make app-up` then named the `deploy/ea.env` line contradicting it, the override was dropped, and every container probed the closed port together. A wrong default is most dangerous once it has stopped being distrusted — so an address goes into a default only once `curl -sf $URL/models` has answered from it.

**Backups** are `docs/adr/0025`, still a *Proposition*: nothing has been run against the cluster yet, and a backup never restored is a hypothesis. `make pg-backup` writes a `pg_dump -Fc` into `backups/` (git-ignored), under its final name only once it has been read back; `make pg-restore FILE=… CONFIRM=yes` replaces the shared database in one transaction. Since `docs/adr/0033` that dump is the whole model. A dump that has not left the host is not a backup.

**Both servers bind `0.0.0.0`** — the API since `docs/adr/0016`, the Vite dev server since `docs/adr/0022` — and **an address to listen on authorises nobody.** Four settings decide who is actually served, and none of them follows from a bind address: `EA_CORS_ORIGINS` for browsers; `EA_MCP_ALLOW_REMOTE_CLIENTS` (default `false`) for who may call `/mcp` — the request's TCP peer must be loopback or it gets a 403, checked by `mcp/transport.py` around the transport's route, because the peer is the one thing a caller does not write itself; `EA_MCP_ALLOWED_HOSTS`, which is *only* the DNS-rebinding defence, since any script sends whatever `Host` it likes; and Vite's own `server.allowedHosts`, left at its default. Behind a reverse proxy the peer is the proxy, so every client looks local — the loopback guard then distinguishes nobody. The deployed stack does exactly that, on purpose, for LibreChat (`docs/adr/0034`), leaving the token of `docs/adr/0032` as the only barrier; anywhere else, `make run-be` included, keep the guard. A remote agent takes an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 host`), or the opt-in *plus* its `Host` in the list — see `docs/adr/0023`. The MCP SDK enables DNS-rebinding protection by itself *only* on a loopback host, so passing it `EA_HOST` would switch that protection off precisely when the API stops being loopback — `main._transport_security` states it instead; setting Vite's `allowedHosts` to `true` would be the same mistake, which is why it is left alone.

Three things follow for the SPA. A browser on another machine sends *that machine's* origin, so `EA_CORS_ORIGINS` needs an entry per host that serves the SPA — an origin is an exact string, and the validator refuses `*`. **With auth on, a plain-http LAN origin cannot log in at all**, whatever is added to Keycloak: `oidc-client-ts` builds PKCE with `crypto.subtle`, which a browser grants only to a secure context, so on `http://192.168.x.y:5173` the redirect throws before it starts — the SPA lands on `/auth/failed`, the reason in the console. Open it from a secure context instead: `http://localhost:5173` on the machine running Vite; from another machine through `ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 <host>` and then `http://localhost:5173` there — `localhost` is a secure context, already one of `ea-spa`'s redirect URIs and already in `EA_CORS_ORIGINS`, so no LAN entry is needed on either side; or through the https vhost (`docs/adr/0032`). `make run-fe` prints the tunnel. And the API's URL cannot be a constant: `src/lib/api.ts` defaults to **this page's own host** on port 8000 (`defaultApiBaseUrl`, a pure function so it is tested without a DOM), because `http://localhost:8000` read by a browser elsewhere names the viewer's machine. `VITE_API_BASE_URL` still wins, for a backend that is genuinely somewhere else.

**Deployed, the app is one origin behind the Infra NGINX** (`docs/adr/0027`). `deploy/ea.stack.yml` is a Portainer *Git* stack on the Infra's Docker (the Mac running Docker Desktop): `api` and `web` join `infra-net` as `ea-api` / `ea-web`, publish no port, and the Infra repo's `nginx/conf.d/ea.conf` serves them at `https://ea.infra.famillelallier.net` — the SPA at `/`, the API under `/api/` with the prefix stripped, because the SPA's routes (`/elements`, `/ipam`) collide with the API's. So the image is built with `VITE_API_BASE_URL=/api`, uvicorn is told `UVICORN_ROOT_PATH=/api`, and **the stack serves `/mcp` at `/api/mcp` for LibreChat** (`docs/adr/0034`): `EA_MCP_ALLOW_REMOTE_CLIENTS=true`, since the peer is always NGINX, with `EA_MCP_ALLOWED_HOSTS` and `EA_MCP_RESOURCE_URL` on the vhost's name — `test_deploy_stack.py` ties both to `EA_CORS_ORIGINS`. The vhost also routes `/.well-known/oauth-protected-resource/api/mcp` to `ea-api`, because RFC 9728 puts it outside `/api/`. LibreChat logs each of its users in to the realm as the confidential client `ea-librechat`, whose one redirect is `https://chat.famillelallier.net/api/mcp/ea/oauth/callback`. PostgreSQL there is the Infra's (`postgres` on `infra-net`, database and role `ea` from `make provision-app app=ea`), and the container applies the Alembic chain before uvicorn starts. **The files (`docs/adr/0036`) are the Infra's SeaweedFS too (MinIO is gone, `docs/adr/0056`), reached as the service `s3:8333` inside `infra-net`, in plain HTTP like `postgres`** — `minio.famillelallier.net` has no alias there, only Keycloak's name does, so `EA_S3_ENDPOINT` and `EA_S3_SECURE=false` differ from a Mac's `backend/.env`, and no CA is mounted for it. The vhost's `location /api/` carries `client_max_body_size 51m;`, for the multipart envelope on a 50 MB file upload, and the Infra's `make s3-provision app=ea bucket=ea-catalogue` creates the bucket and the `ea` identity; `location = /api/mcp` stays at `2m`, since the MCP file tools carry their own, much lower ceiling (`upload_file`'s `MAX_MCP_UPLOAD_BYTES`, 1 MB — see `mcp/server.py`). Do not add uvicorn's `--forwarded-allow-ips`: it would make the peer a header the caller writes. Inside `infra-net`, `keycloak.famillelallier.net` is an alias of the Infra's `nginx`, which presents the Infra certificate: the stack mounts `INFRA_CA_CERT` (mandatory) read-only as `EA_AUTH_CA_CERT`, or the API cannot read the realm's keys and does not boot; and the SPA image takes `VITE_AUTH_AUTHORITY` / `VITE_AUTH_CLIENT_ID` as build args, since Vite writes them into the bundle. **Writes to that stack go through Portainer's API, reads through `docker compose -p ea`**: `make app-up` creates or git-redeploys it (`scripts/portainer-stack.sh`, curl in a throwaway container on `infra-net`, so Portainer stays the stack's owner), `app-down` stops it, `app-restart` stops and starts it again so the containers re-read their configuration and re-probe (nothing else does, see `docs/adr/0037`) — with the variables Portainer already holds, so an edited `deploy/ea.env` still needs `app-up` — `app-delete CONFIRM=yes` removes it, `app-ps` / `app-logs` read the containers on this Docker. Portainer clones GitHub `main`, never this working copy — an unpushed commit is not deployed. **And a pushed one is**: `app-up` sets the stack's GitOps polling, so Portainer checks `main` every `POLL_INTERVAL` (5 min) and redeploys on a new commit, rebuilding both images (`pull_policy: build`) — GitHub Actions cannot reach this Portainer, and a self-hosted runner on a public repo is within reach of a fork's PR (`docs/adr/0041`). A red commit on `main` deploys too, so branch protection is now the deploy gate; a changed `deploy/ea.env` still needs `app-up`. **The self-hosted runner** of `docs/adr/0044` is not on the Infra's Docker, so it calls Portainer by URL (`PORTAINER_URL`, TLS checked against `infra-ca.crt` mounted read-only, no Docker socket — `docs/adr/0045`); `make app-up` on the Infra host keeps the `infra-net` curl container. The API key (root on the Docker daemon) lives in the git-ignored `.portainer.env` or `PORTAINER_ENV_FILE`, the stack variables in the git-ignored `deploy/ea.env` (from `deploy/ea.env.example`), and `app-up` refuses to leave while a `:?` variable of the stack file is empty there. `make app-stack` prints the steps.

`make check` runs the non-fixing lint on both sides, types on both sides, the generated-client check, the DB-free suites with the coverage floor, and `pipelines-check` — the local half of what CI runs (`.github/workflows/ci.yml` adds `make audit`, `pipelines-audit` and the integration suite against throwaway containers).

## Front/back contract

The backend's OpenAPI schema is the single source of truth. **Never hand-write a TypeScript interface that mirrors a Pydantic model** — regenerate `frontend/src/api/` and import from there. A backend change that alters the schema and does not regenerate the client is an incomplete change.

`backend/openapi.json` and `frontend/src/api/schema.d.ts` are both committed. `make openapi` regenerates the pair; `make openapi-check` fails when they no longer match the code, and `make check` runs it. `openapi-fetch` calls the generated types; `src/lib/api.ts` holds the base URL and the error handling — the only hand-written half — and nothing else there describes a payload. See `docs/adr/0007`.

## Exchange files: a door that is held, not trusted

`GET /exchanges/export` and `POST /exchanges/imports` (and the MCP tools `export_model` / `import_model`) exchange the
model as ArchiMate Exchange XML or as Archi-style CSV; the decisions are `docs/adr/0050` to `0055`. What a change here
must keep: an import is `dry_run=true` unless said otherwise and goes through the services (never a repository:
`test_exchange_through_services.py`); its place among the imports in flight is a **lease** taken before the body is read
(`services/exchange_admission.py`, one global ceiling and one per `sub`, shared by HTTP and MCP, expiring by itself);
reading the body has a delay per message and a total one (`EA_EXCHANGE_READ_TIMEOUT_S`, `EA_EXCHANGE_READ_TOTAL_S`);
an export is rendered to a temporary file and the database connection goes back to the pool **before** the first byte is
sent (sending has `EA_EXCHANGE_SEND_TIMEOUT_S`); an import's names reach no log (`names_in_logs` / `logged()`, guarded by
`test_log_names_guard.py`) and an unhandled database failure is logged as its structure only (`api/errors.safe_failure`).
`EA_LOG_SQL=true` logs query parameters, a file's values included: never on a deployment that imports.

## The MCP adapter is a sibling of `api/`, not a client of it

`ea/mcp/` translates one agent request into one `ArchitectureService` call and
renders the answer with **the API's own read models** (`ElementRead`,
`GraphRead`, `MetamodelRead`). Never give it a repository, never let it reach
past the service, and never let it speak HTTP to our own API: the rules that
need more than one object — the element must exist before it is linked,
containment must not loop — live in `services/`, and an adapter that skips them
writes a graph the API would refuse. The same reason forbids restating a rule
here: the palette is *asked for* (`describe_metamodel`), exactly as the SPA
asks for it.

The markdown of `docs/adr/0017` is exposed too, and the IP addressing of
`docs/adr/0020` with it, so the adapter holds **eleven** service providers
rather than one — never a repository. An agent has no file to
upload, so the five document tools take the text itself and land on
`DocumentService.attach_text` / `revise_text`; the byte-taking entry points the
HTTP upload uses now delegate to those, so the two paths differ by the decoding
step and nothing else. `get_documents` is required rather than optional: the
tool list belongs to the adapter, not to the deployment, so a shut relational
store means the document tools are offered and fail — exactly as `/documents`
stays routed and answers a 500. See `docs/adr/0018`. `get_ipam` is required for
the same reason, and so is `get_files` (`docs/adr/0036`): the six file tools
stay listed and fail 503 when `EA_S3_ENABLED` is off. Two of those six —
`describe_file` and `set_file_details` — are the record PostgreSQL keeps beside
each object (`docs/adr/0039`), and the `INSTRUCTIONS` tell a model to write one
when it uploads a binary: `rapport-q3.pdf` says nothing, a sentence beside it
says everything. An agent has no file to
attach here either, so `upload_file` takes the content as text
(`encoding="text"`) or, for anything that is not text, base64
(`encoding="base64"`) — the same choice the document tools made, generalised
to a bucket that also holds PDFs and images. `upload_file`'s own ceiling is
`MAX_MCP_UPLOAD_BYTES` (1 MB decoded), not the domain's 50 MB: the SDK's
request body defaults to 4 MiB and the deployed Infra NGINX caps
`location = /api/mcp` at `client_max_body_size 2m`, and base64 plus the
JSON-RPC envelope both add their own overhead on top — a bigger file goes
through the web interface instead.

The eight IP tools exist because the arithmetic is the server's job. An agent
that wrote an address with `update_element` would be doing addition in its
head, and would eventually hand one out twice; `allocate_ip_address` and
`assign_ip_address` are what the `INSTRUCTIONS` point it at instead.

`search_documents` is the tool that changes how an agent
should work here: it searches the *passages* of every document by meaning and
answers with the section, so reading ten documents to check one sentence is no
longer the way in. The server's `INSTRUCTIONS` say so, because that is where a
model learns it.

Adding a tool means: a method on the service if it is a new use case, a
function in `mcp/server.py` decorated with `@server.tool(annotations=...)` and
`@speaking_plainly`, and an entry in the whole-list assertion in
`tests/unit/test_mcp_server.py`. Two things are part of the behaviour and not
decoration — the docstring, which is what the model reads to decide whether to
call it, and the annotation, which is what a client shows the person who has to
approve a write. A tool that deletes says so.

**A bound is declared once, in `api/schemas.py`.** Every limit an HTTP endpoint
also enforces — a name's length, a page, a depth, a prefix — is imported by
`mcp/server.py`, which lays only a `Field(description=...)` over it, so the two
adapters cannot drift into refusing different values. A new bounded argument
goes into `schemas.py` and into `TestTheSameBoundsAsTheHttpAdapter` in
`tests/unit/test_mcp_server.py`. **`api/` imports it too, including from a
multipart form**: `POST /files` declared `tags` as a bare `list[str]` while
`FileTags` already existed and the `PUT` and both MCP tools used it, so the one
door that dropped the bound was the HTTP one — and, the bound being absent from
the OpenAPI document, the generated client could not guard it either. A value a
schema cannot express is a domain refusal, and it needs its own row in
`_STATUS` (`api/errors.py`) or it comes back as a bare `400 invalid_request`
instead of the typed 422 every neighbouring refusal gets.

**`/mcp` is a resource server of the same realm.** `mcp/auth.py` hands the SDK
a `KeycloakTokenVerifier` over the API's own `JwtVerifier`; the SDK publishes
`/.well-known/oauth-protected-resource/mcp` (spliced like `/mcp`), and
`speaking_plainly` sets the caller the token proves before the tool runs — so a
reader's `create_element` is refused by the service, exactly as over HTTP. The
loopback guard of `mcp/transport.py` stays in front of it, except on the
deployed stack, where NGINX is every peer and the token alone decides. See
`docs/adr/0032` and `docs/adr/0034`.

Domain failures come back as `ToolError` (`mcp/errors.py`), the exact
counterpart of `api/errors.py`: an anticipated refusal reaches the model with
its message so it can correct itself, anything else stays in the logs.

The transport's routes are *spliced* onto the FastAPI app rather than mounted,
because `Mount("/mcp", …)` answers a bare `POST /mcp` with a 307. It is a
Starlette route, so **`/mcp` never appears in the OpenAPI schema** and no
client regeneration follows from it. See `docs/adr/0014`.

## The model is analysed on demand, and nothing is stored

`AnalysisService` (`docs/adr/0056` to `0058`) computes the matrix, the quality findings and the
aggregates from `elements` and `relationships` on every call: a stored result would be a second truth
every edit made stale. Five decisions hold.

**A link is written by `POST /relationships` or not at all.** The matrix cell reads
`allowed_between` (`permitted_relationships` filtered by `permits(..., same_element=)`) and the SPA
offers only that; it has no write path of its own, so no ArchiMate rule is restated.

**Bounds are declared once, in the domain** (`MAX_MATRIX_AXIS`, `MAX_FINDINGS`, `MAX_BUCKETS`,
`MAX_QUALITY_ELEMENTS`), because a service cannot import `api/`; `api/schemas.py` and `mcp/server.py`
re-import them. A quality run over more than `MAX_QUALITY_ELEMENTS` is refused (`scope_too_large`),
never cut: a partial report reads as a clean one.

**What reads the whole model is admitted and runs off the event loop.** `check`, `aggregate` and
`heatmap` take a place in an `Admission` (the one of `services/exchange_admission.py`: 4 in all, 2 per
caller, 429 `analysis_busy`) and run their CPU-bound step with `asyncio.to_thread`; the place is held
until the thread ends (`lease.hold`), so an expired lease whose computation still runs keeps counting.

**`properties` stays strings.** `ANALYTIC_PROPERTIES` says how to read `lifecycle`, `risk` (ordered)
and `cost` (a number); a value that does not read is counted as `unreadable` and reported by the rule
`unreadable_property`, never dropped in silence. SQL groups by the *raw* value and `summarise` folds
the spellings once, so SQL and the pure reference (`group_elements`) cannot disagree. The key is
validated by `PROPERTY_KEY` and bound as a parameter. The colour is a server `level` 0..4; the SPA only
maps `level → swatch`. `Severity` is the one of `domain/exchange.py`, reused.

**A scope (a package, sub-packages included, or a saved query) is a `ScopeResolver` injected by
`main.build_scope_resolver`**: the service knows neither packages nor queries. Two quality knobs,
`EA_QUALITY_SIMILARITY` and `EA_QUALITY_REQUIRED`, are validated at boot.

## A pipeline writes through the API and never names a model

`pipelines/` is a second client of the EA API, exactly as the SPA is one —
never a third adapter beside `mcp/`, never a repository. Three rules hold for
every flow there, see `docs/adr/0028`:

**Each step of a flow is its own `@task`, every LLM output is validated
against a strict JSON schema before it is trusted, and the code only ever
names the aliases `smart`/`fast` — never a real model id.** `catalogue.py`'s
`extract_architecture`, `write_element` and `write_relationship` are separate
tasks so Prefect can retry one without repeating the others; `llm.extract`
turns the Pydantic schema into an OpenAI-shaped `strict` `json_schema` and
raises `ExtractionFailed` on anything that does not validate — a truncated
answer, invalid JSON, or a value outside an injected `enum`; `Alias =
Literal["smart", "fast"]` is the only type a caller can pass as `model=`, and
`pipelines/litellm.yaml` is the one place that maps an alias to a provider.

**Writes reach EA only over its REST API, never over `/mcp`, never through a
repository.** `/mcp` answers only a loopback peer (`docs/adr/0023`) and the
pipeline's container is not that peer; a repository would let the pipeline
write a graph `services/` would have refused. The worker proves who it is with Keycloak client credentials, as the confidential client `ea-pipelines` whose service account holds `ea-editor` (`ClientCredentials`, an `httpx.Auth`, so `EaClient` never sees a token — `docs/adr/0032`). `EaClient` (`pipelines/src/pipelines/ea.py`) calls `GET /metamodel` at the start of every run for the
element and relationship types it may use — never a restated list — the same
rule the SPA and the MCP adapter follow.

**Two different mistakes are avoided on write.** Duplicating an element is
caught by the `element_name_unique_per_type` constraint plus an exact-name
match on the page it returns (`EaClient._resolve_duplicate_element`); a
relationship has no equivalent constraint, so `ensure_relationship` searches
for the exact link before creating it. Completing is not overwriting: a
`PATCH` only ever fills an empty `description` — never `properties`, because
`PATCH /elements/{id}` *replaces* that whole map, so writing it here would
erase whatever a human already recorded there. Every refusal (`EaRefused`,
a 4xx) is recorded as a row in the flow's table artifact rather than raised;
only a file whose extraction failed outright fails the run.

**One retry layer per kind of failure.** LiteLLM retries the provider itself
(`num_retries: 2` in `litellm.yaml`, with `drop_params: false` so a `response_format` the
provider cannot honour fails loudly instead of being dropped); the pipeline's own call to the gateway
(`llm.extract`) is a single `httpx` POST with no retry of its own — a
malformed answer is a schema or a prompt to fix, not a transient fault; the
tasks that write to EA carry `@task(retries=2, retry_delay_seconds=[2, 10])`,
for the network between the worker and the Mac. Nothing retries a bad
extraction: an `ExtractionFailed` is never worth repeating verbatim.

## Adding a section to the SPA

Sections live in one list: `frontend/src/router/sections.ts`. Add an entry to
`SECTIONS` — path, route name, label, one-line summary, group — and both the
router and the menu follow; there is no second list to keep in step. Leave `view`
out until the screen exists: the menu then shows the section greyed out with an
*à venir* badge and the router creates no route for it, so a link can never
resolve to a screen that is not there. When you build it, add
`view: () => import('../features/<section>/<Screen>.vue')` — the lazy import is
what keeps each section in its own bundle chunk. A new group is an entry in
`GROUPS`. See `docs/adr/0008`. Every declared section has a
screen today, so the *à venir* badge is covered by a spec that hands `AppNav` a
menu of its own — the component takes its entries as a prop, defaulting to
`menu()`, for exactly that reason.

## The SPA holds no copy of the metamodel

The 61 element types, the 11 relationships, their families and the 61x61 matrix
of Appendix B all live in `domain/archimate/` and are asked for: `/metamodel`
serves the palette and the relationship types, `/metamodel/relationships` one
pair, `/metamodel/matrix?source=` one row of the matrix. `features/metamodel/`
holds translation tables — `LAYER_LABELS`, `ASPECT_LABELS`, `CATEGORY_LABELS`,
and `RELATIONSHIP_LABELS` next to the relations — and **not one rule**. A rule
restated in TypeScript is a second metamodel, free to promise a link the API
then refuses. See `docs/adr/0012`.

## A document is markdown, and markdown is text

`element_documents` holds the files attached to an element. Four things about
it are decisions, not details — see `docs/adr/0017`.

**It is `TEXT`, never `bytea`.** Markdown is prose: it is read, searched and
diffed by people. The price is paid up front, in `domain/documents.py`: a file
that is not UTF-8, or that holds a NUL, is refused at the door — PostgreSQL
cannot store a NUL in a `TEXT` column, so accepting it would fail at `INSERT`,
as a driver error, in a log. The check is on the *file name* (`.md`,
`.markdown`) and not on the `Content-Type`, which browsers report three
different ways.

**It follows its element by a foreign key.** `element_documents.element_id`
references `elements` `ON DELETE CASCADE` since migration `0006`;
`DocumentService` still reads the element before attaching, for a readable 404
rather than a constraint violation (see `docs/adr/0033`).

**Listing is not reading.** `DocumentSummaryRead` names the files;
`DocumentRead` carries the text. A client that downloaded ten bodies to draw
ten names is the mistake the two models prevent, and the size is computed by
the server (`octet_length`) rather than stored, so it cannot drift.

**The relational repository takes the session factory** and opens one unit of
work per call — every use case here is a single write, so that *is* a
transaction per use case. A use case spanning
two writes takes an `AsyncSession` argument instead, and
`api.dependencies.get_session` becomes the seam it was built to be.

## A document is found by its headings, not by its file name

`document_chunks` is what makes the markdown of `docs/adr/0017` searchable, and
four things about it are decisions — see `docs/adr/0019`.

**A document is cut at its own headings, and the trail of headings is embedded
in front of each passage.** `domain/chunking.py` is pure and decides both: a
section becomes a passage, and what is handed to the model is
`runbook.md > Incidents > Escalade` then the text. That trail is exactly the
information a passage does not contain about itself — "restart the container
and check `/health`" is a sentence with no subject — and putting it back is the
difference between a corpus that answers questions and one that matches words.
The *stored* text is the passage alone; both come out of `heading_trail`, so a
hit renders its trail exactly as it was indexed. Fenced code blocks are
respected (`# restart the service` is the first line of half the shell examples
ever written) and YAML front matter is not indexed.

**The width of a vector is not a setting.** `EMBEDDING_DIMENSIONS` is the width
of the column; the model, the migration and `Settings` agree by importing it.
Changing it is a migration plus a full `make docs-reindex`, and boot refuses a
model that answers anything else — otherwise the failure arrives at `INSERT`,
as a driver error, in a log, after the upload was accepted.

**Every vector records the model that produced it, and every search filters on
it.** Cosine distance between vectors from two models is a number that means
nothing, so a half-reindexed corpus returns *too little*, visibly, rather than
something plausible and wrong.

**This was the first table to carry a foreign key**, because a passage
names a document — a row next door. Its cascade is DDL, and a document and its passages are written in **one transaction**.
The embedding call happens *before* that transaction opens: holding one across
a call to another machine is how a slow embedder becomes a locked table.

The embedder is a port with **two** methods, `embed_passages` and `embed_query`,
because several model families want a different instruction on a stored passage
than on a question — `mxbai` on the query only, the e5 line on both, `bge-m3` on
neither. Both prefixes are in `.env`, stated rather than assumed. An index can
still fall behind its store in two ways, both configuration — a document
attached while `EA_EMBEDDINGS_ENABLED` was off, and a change of model — and
`make docs-reindex` is the catch-up for both.

## An IP address is an attribute of the machine, not a record beside it

There is no IP address table and no subnet table, and adding one needs an
ADR that supersedes `docs/adr/0020`. A **subnet is a `communication_network`
element** carrying `cidr` in its properties, and an **address is `ip_address` in
the properties of the element that answers on it**. Four things about that are decisions.

**One address per element, and that is what makes uniqueness real.** A list
would have been easier to write and impossible to constrain: a unique index
sees one value per row. With one address, the partial unique index
`uq_elements_vrf_ip_address` on `(properties ->> 'vrf', properties ->> 'ip_address')`
is declarable, and it is the only thing that survives two agents reading
"free" in the same instant. A multi-homed host is two `technology_interface`
elements composed into a node — which is how ArchiMate says to model it
anyway. A prefix gets the same treatment: `uq_elements_vrf_cidr` declares it
once per VRF, in the database, because two callers can both look before
creating. `vrf` is written *beside* every address and prefix, because the
index's `WHERE` leaves out an element missing either key; and both are stored
in one canonical spelling, because the constraint compares strings.
`validate_ipam_properties` returns the properties to store, and every write
path stores what it returns.

**Belonging to a subnet is computed, never stored.** No edge from an address to
its subnet, none between a subnet and its parent. Longest prefix wins, as in a
routing table, so declaring `10.0.0.0/8` as the corporate range does not move
the hosts of `10.0.1.0/24` under it. A stored edge would be a second truth,
free to contradict the arithmetic.

**The convention is checked wherever properties are written**, not only behind
the IPAM door: `ArchitectureService.create_element` and `update_element` both
call `validate_ipam_properties`. `PATCH /elements/{id}` takes free-form
properties, and a rule enforced behind one of the two doors is a rule the
inventory cannot lean on.

**`domain/ipam.py` is pure and holds every rule an address can check alone** —
what a prefix keeps for itself (RFC 3021 for a /31, the subnet-router anycast
address for IPv6), what a reservation covers, what is free next. `reserved_count`
leaves out what the protocol keeps anyway, which `capacity` never counted.
`services/ipam.py` holds the ones needing the rest of the catalogue: the type
must be addressable, a declared subnet must hold the address, nothing else may
have it. An allocation that loses the race to the constraint tries the next
free address, up to `ALLOCATION_ATTEMPTS` (3). The SPA restates none of them —
it asks, exactly as it does for the metamodel.

## Time is two properties, a gap is an ordinary element, and nothing about either is stored twice

`valid_from` and `valid_until` are keys of `properties` (`docs/adr/0059`), `YYYY-MM-DD`, the last day
included, a missing one open; there is no lifespan table and adding one needs an ADR that supersedes it.
`domain/timeline.py` is pure: `Lifespan` (`covers`, `overlaps`), `validate_lifespan_properties` — called by
`create_element`, `update_element`, `complete_element` and `connect`, so `PATCH`, an import and the pipeline's
completion are not back doors — which canonicalises, removes a blank (a stored `""` would hide the element from
every dated read) and refuses `2027-06` with a message that **names the key and never echoes the value**.
`readable_lifespan_properties` is its never-raising twin, for what is *read back* from a copy that predates the
rule (a baseline). **Migration `0014` never refuses and never loses a value**: a container that cannot migrate
is an outage, so a stored value that is not a day moves to `legacy_valid_from` / `legacy_valid_until`, with no
SQL `CAST`, one pass per (table, key), and a `lock_timeout` on the index build.

`as_of` (`docs/adr/0060`) is one predicate, `visible_at`, in `architecture_store.py`: absent it adds no
condition and no join; present, a link is visible only with both ends, and it sits in the **recursive term** of
both walks so a retired neighbour *cuts* the path. A diagram is a view: `as_of` removes no box, it fills
`inactive_element_ids` for the SPA to grey. **The plateaus, the gap and the roadmap (`docs/adr/0061`) are
computed, never stored**: a plateau contains what it composes or aggregates; a gap is a `gap` element
associated to both plateaus, written by `TimelineService.create_gap` through `ArchitectureService` in one
shared transaction under a per-pair advisory lock (so the guard, the audit and the review rules all apply and a
failure leaves nothing). Every read there is bounded **before** it is made — a window over 20 years is a 422
before the first query, every set is read one past its ceiling with a SQL `LIMIT` and refused as
`scope_too_large` rather than cut — and the folding runs off the event loop under a place of the analyses'
`Admission`. The SPA draws the roadmap with `lib/roadmapLayout.ts` (pure geometry, no rule) and holds the day in
`?asof=` (`lib/asof.ts`), which a router guard carries from section to section.

## MinIO says what exists, PostgreSQL says what is known about it

`file_metadata` is the record kept beside each object of the bucket
(`docs/adr/0039`). Five things about it are decisions, not details.

**The bucket stays the source of truth for what exists.** Listing, downloading
and deleting go to MinIO; an object it holds is a file whether or not
PostgreSQL has heard of it, and a file with no row is listed *undescribed*
(`metadata: null`), never absent. That is the only split that leaves the
`alimenter-catalogue` pipeline's own door open: it drops sources into `inbox/`
knowing nothing about this table, and what it drops stays visible.

**A read never writes.** Listing a folder does not create rows for what it
finds. The catch-up is explicit — `POST /files/reconcile`, or
`make files-reconcile` — and it is what gives a row to a file that arrived by
another door and drops a row whose object is gone. There is no foreign key that
could do either: the other side of this relation is an object store, and this
is the one table in the repository in that position.

**The object is written first, the row second.** A crash between them leaves a
file in the bucket with nothing recorded about it — exactly the state a file
written by any other door is in, and exactly what a reconcile repairs. The
reverse order would leave a row describing a file that was never stored, which
nothing repairs.

**Replacing a file never unwrites what a person said about it.** Both upload
paths are `INSERT ... ON CONFLICT DO UPDATE`, and their `SET` list holds
neither `title`, nor `description`, nor `tags`, nor `created_at`. Supplying
details on an upload is a second, deliberate statement; supplying none keeps
what was there. `describe` is the only thing that changes them, and it replaces
all three together — sending one is what clears the other two, because merging
them in a client would be a second answer to what a record is. For the same
reason `record` and `note_seen` are two methods and not one flag: an upload read
the bytes and has a digest and a caller, a reconcile has a listing and has
neither, and folding them together would mean a reconcile blanking the uploader
of every file the API stored.

**`sha256` is nullable, and `NULL` means "not computed".** It is known for a
file this API received and unknown for one a reconcile picked up from a
listing — computing it there would mean downloading the object, and a catch-up
that reads every byte of a bucket is not a catch-up. An empty string would say
"computed, and empty", which is the digest of no bytes and a different fact. A
reconcile *drops* a digest the `etag` says is stale: a silently wrong digest
answers the only question it exists for — "the same file under another name?" —
wrongly.

## Drawing a graph in the SPA

There is no graph-rendering library and adding one needs an ADR. A sub-graph is
drawn as hand-written SVG (`components/GraphDiagram.vue`) over a **pure,
deterministic layout module** (`lib/graphLayout.ts`): concentric rings, one per
hop from the subject, so the geometry is unit-tested without mounting anything
and the same sub-graph always draws the same way. Boxes carry the conventional
ArchiMate layer colours (`LAYER_COLOURS`, beside `LAYER_LABELS`) — light fills
that state their own dark ink, because `var(--text)` inverts in the dark theme —
and every neighbour is a real focusable control, not a painted pixel.

**Both traversals share that drawing, and mean different things by a ring.**
`layout(graph, rootId, hops?)` *accepts* the distances instead of insisting on
computing them: the neighbourhood lets it walk links either way, and the impact
analysis hands it its own walk, which follows each hop the way dependency runs
(`features/impact/propagation.ts`). Which way that is per relationship type is
`impact_follows_direction`, read from `/metamodel` through
`useMetamodel().followsArrow` — never restated in TypeScript, per the rule
above. A link that explains no distance is drawn dashed rather than dropped.
See `docs/adr/0013`.

**A screen whose state is a question the user would want to share or walk back
keeps that state in the URL, not in a `ref`.** The neighbourhood reads
`?element=`, `?depth=` and `?relation=` from the route and writes them back:
changing subject pushes a history entry, turning a dial replaces one. See
`docs/adr/0010`. The impact analysis reads the same three (see `docs/adr/0013`),
the catalogue follows the rule for the element it details (`?element=`, see
`docs/adr/0011`), the metamodel for the cell of the matrix it is showing
(`?source=`, `?relation=`, see `docs/adr/0012`), and the IP addressing for the
subnet it has open and the address it was asked about (`?subnet=`, `?address=`,
`?vrf=`, see `docs/adr/0020`); an unsaved form — the subnet declaration, say —
is not that kind of state and stays in a `ref`. The day the whole catalogue is read on is `?asof=` (`lib/asof.ts`, `docs/adr/0060`), replaced when changed and carried by a router guard to the next section; the roadmap's comparison keeps its two plateaus in `?a=` and `?b=` (`docs/adr/0061`).

## The relational store holds the whole model

PostgreSQL is the one shared instance — in the `~/OpenCode/Infra` stack on the
Mac (127.0.0.1:5432) since `docs/adr/0029`, not on the cluster — and its password is a real shared secret — `.env.example`
leaves it blank. **Its image has to carry pgvector**: the document
index is a `vector` column and migration `0003` runs `CREATE EXTENSION vector`,
which fails on an image that does not have it (`make pg-vector-check`). `db/postgres.py` owns the engine, the session
factory and the boot-time check; `db/base.py` holds the declarative `Base` and
**the constraint naming convention, which is frozen** — changing it renames
constraints already in the deployed database. See `docs/adr/0015`.

Three rules hold for every table, starting with `element_documents`:

1. **Its module is imported by `db/models/__init__.py`.** Alembic autogenerates
   by diffing `Base.metadata`; a model that package does not import is one
   autogenerate proposes to *drop*.
2. **Every change to a table is a versioned revision.** `make pg-revision
   m="..."` generates it, and the generated file is read before it is
   committed. `EA_POSTGRES_ENABLED` is on now, so a machine out of reach of the
   database no longer boots the API — that is the intended rule, the same one
   the graph has always had.
3. **Never write a DSN anywhere.** `dsn_of()` builds it from `Settings` with
   `URL.create` — a password holding `@`, `/` or `:` spliced into a URL string
   silently addresses a *different* database. `alembic.ini` carries no
   connection string; `migrations/env.py` reads `Settings` like everything else.
   **The one exception**: `pipelines/.env` holds `PREFECT_DATABASE_URL` and
   `LITELLM_DATABASE_URL` as literal URLs, because Prefect and LiteLLM both
   expect a connection string, not the separate fields `dsn_of()` assembles —
   confined to that file, never committed, and passwords generated with
   `openssl rand -hex 32` precisely because nothing here escapes them the way
   `URL.create` does (see `docs/adr/0028`).

A third rule arrived with the second table: **a use case that writes two tables
writes them in one transaction**, which is possible for every use case now that
the graph is here too. `add`/`replace` take the
document and its passages together, and flush the document first — the two
mappers are related by a foreign key and by no `relationship()`, so the unit of
work is free to emit the chunk inserts first, and does.

`make pg-migrate` targets the **shared** database. The integration tests apply
and then reverse the whole chain, which is why they run against the throwaway
container in `docker-compose.yml` instead, and why `postgres_engine` refuses a
host that is not loopback — see `docs/adr/0024`.

Transactions belong to `services/`, not to the route: `get_session` opens a
session per request and commits nothing.

## The graph is two tables

`elements` and `relationships` (migration `0005`, `db/models/architecture.py`)
are read and written by `repositories/architecture_store.py` alone. User-defined
attributes are one `jsonb` map, `properties`, with no prefix. The neighbourhood,
the impact analysis and the containment-cycle check are recursive CTEs built
with SQLAlchemy Core — the depth is a bound parameter, clamped to
`MAX_TRAVERSAL_DEPTH` — and the impact walk takes each hop the way
`impact_follows_direction` says, read from the metamodel. A uniqueness refusal
is translated by the **name** of the constraint (`constraint_of`), never by its
message. Renaming a stored value — an element type, say — is a data migration:
an Alembic revision like any other. See `docs/adr/0033`.

## A repository opens its session through `ea.db.unit_of_work`, and an import shares one

`work(self._sessions)` for a write, `reading(self._sessions)` for a read — never
`self._sessions.begin()` or `self._sessions()` in a model store (an AST test,
`test_one_way_to_open_a_unit.py`, fails on it). Outside an import they behave
exactly as before; inside `shared_transaction(...)` (a `ContextVar`, like the
caller) every write is a `SAVEPOINT` of one shared session — except inside
`apply_rows(...)`, which runs rows in batches of one savepoint (PostgreSQL tracks
only 64 sub-transactions per transaction) — `commit=False` is the dry run, `exclusive=True` takes the one-import-at-a-time
lock, and a 40001/40P01 condemns the whole import (nothing is written). A call that
sets its own isolation level passes `alone=True`. The API's engine
(`create_api_engine`) gives up a row-lock wait after `EA_DB_LOCK_TIMEOUT_MS`
(10 s; SQLSTATE 55P03 is a 409 `write_conflict`); scripts and migrations wait.
See `docs/adr/0052`.

## Who is calling is decided once, in `services/`

`services/caller.py` holds `current_caller`, a `ContextVar` exactly like the
request id: the adapter that knows who is calling sets it — `api/auth.py`'s
bearer dependency, `speaking_plainly` for `/mcp`, `acting_as(SYSTEM)` in
`ea.reindex` — and the service reads it. **Every public method of
`ArchitectureService`, `DocumentService`, `IpamService`, `DiagramService`,
`FileService`, `PackageService`, `SavedQueryService`, `AuditService` and
`BaselineService`
starts with `require_caller()` (a read) or `require_editor()` (a write)**, and nobody set
means `NotAuthenticatedError`: a forgotten wire-up is a 401 in a test, not an
open door. `tests/unit/test_service_guards.py` reads the source (AST) and fails
on a public method whose first statement is not one of the two, so adding a use
case means choosing which. `NotAuthenticatedError` is a 401 with
`WWW-Authenticate: Bearer`, `NotAuthorisedError` a 403. See `docs/adr/0032`.
Saved diagrams follow the same rule: any caller lists and opens them, only
`ea-editor` creates, renames, lays out or deletes one, and the SPA hides the
palette, the link handle and every such control from a reader.

## Every write leaves a line in the audit trail

`audit_events` (migration `0011`, `docs/adr/0046`) is append-only and written
by the repository, in the same `begin()` as the write it describes: `services/`
builds one `AuditEvent` per use case — actor from `current_caller`, never a
parameter; time from the injected clock; the channel (`http`, `mcp`, `system`)
from a `ContextVar` like the caller — and hands it to the repository as
`audit=`. A refused write (404, 409, 403, 422) writes no event; it leaves a
structured log line instead. The row has no foreign key to the entity, so it
outlives a deletion, and triggers refuse `UPDATE`, `DELETE` and `TRUNCATE` — the
`INSERT, SELECT`-only database role is still owed by the Infra. Events carry
names, sizes and digests, never the text of a document. `GET /audit` and
`GET /elements/{id}/history` read it (cursor on `(at, id)`, `422 invalid_cursor`
when forged), the SPA's *Historique* section and the element sheet draw it, and
two MCP tools read it. **Nothing purges it**: retention is an operator's job,
`docs/audit-retention.md`.

## Baselines freeze the model, and restoring one is a plan first

A baseline (`docs/adr/0047`) is a normalised copy of the graph and the diagram
boxes — `baselines`, `baseline_elements`, `baseline_relationships`,
`baseline_boxes`, with no foreign key to the live tables — captured by
`INSERT … SELECT` in one `REPEATABLE READ` transaction. **Documents, the package
tree and the saved queries are not captured.** `GET /baselines/{id}/diff` compares
two baselines or one with the live model. A restore is `plan_restore` (pure),
shown with `dry_run` — true by default, in HTTP and in MCP — then applied only
with `dry_run=false` and the `digest` of the plan that was read, in one
`SERIALIZABLE` transaction; any other plan is `409 restore_conflict` and nothing
is written. A restore never touches an approved or locked element (`protected_ids`,
`docs/adr/0049`): the plan is blocked until an approver reopens it, and the
`baseline_*` copies do not hold the approval, so a recreated element is a draft. Every capture is bounded, none is
purged and their number is unlimited (`docs/audit-retention.md`).
`make baseline-capture CONFIRM=yes` takes the one before go-live: it names the
shared database it writes to and refuses without the confirmation.

## An element is reviewed before it is trusted, under a role of its own

`approval_state` (`draft` → `in_review` → `approved`), `locked`, `approved_by_sub`,
`approved_at`, `revision` and `last_modified_by_sub` are columns of `elements`,
outside `properties` (`docs/adr/0048`). `domain/approval.py` holds the table of
transitions and **the one guard, `guard_element_write`, that every path writing
an element asks** — on the row as read under its lock, in the transaction that
writes; `tests/unit/test_review_guard.py` reads the source and fails on a door
that does not. `ea-approver` approves, rejects, reopens, locks and unlocks and
does not imply `ea-editor`. Approving names the `revision` read
(`expected_revision`, else `409 approval_stale`), and an approver cannot approve
what they changed last — fail-closed when that is unknown; only
`EA_APPROVAL_SEPARATION=false` lifts it, and logs `separation_disabled`. On an
approved element, changing its content is the approver's and sends it back to
review; adding or removing a link is any editor's and does the same; documents,
filing in a package and restoring a baseline are nobody's until it is reopened;
a locked one accepts nothing. The SPA draws one button per entry of
`allowed_transitions` and decides nothing.

## What is fatal at boot, and what only degrades

**A dependency every route needs is fatal; one that serves a single section
degrades.** PostgreSQL holds the whole model and Keycloak verifies every call,
so a process that cannot reach either has nothing to serve and stops. The
embedding service and the MinIO bucket serve one section each, so an
unreachable one is recorded and the API boots without it — `search_documents`
and `/files` then answer 503, exactly as they do where their setting is off.
Both go through `_reachable()` in `main.py`; PostgreSQL and Keycloak
deliberately do not. See `docs/adr/0037`.

This is not a preference. Every probe used to be fatal, so an unreachable
MinIO — `docs/adr/0036` ships `EA_S3_ENABLED=true` in the stack while its own
"à faire hors de ce repo" still owes the `ea-api` MinIO user — took the whole
deployment down: `502 Bad Gateway` on `/health`, `/me`, `/elements` and
`/metamodel` alike, because a file browser could not reach its bucket.

**`/health` is what says so**, and it is the only place that does without
opening the container's logs: `status` is `ok` or `degraded`, and `degraded`
names the sections that will refuse. It stays a **200** either way — it is the
`Dockerfile`'s `HEALTHCHECK` and the SPA's first call, and liveness is not
readiness — and it names **sections, never reasons**: it carries no
`Authenticated`, so which host, bucket or model is at fault stays in the log,
under `action=subsystem_degraded`. A third peripheral store adds a constant
beside `SEARCH` and `FILES`; a dependency every route needs adds none.

**Nothing re-probes.** A section degraded at boot stays degraded until the
container restarts, so fixing the store means `make app-restart` (or
`make app-up`, which redeploys as well) afterwards. And a document attached while the embedder was down is not
indexed — the same state `EA_EMBEDDINGS_ENABLED=false` produces, with
`make docs-reindex` as the same catch-up.

## Nothing is logged until something configures logging

`core/logging.py` is that something, and it is a **pure function of the
settings**: `logging_config(settings)` returns the `dictConfig`,
`configure_logging` applies it, and it is called from the **process entry
point** — `main.__getattr__` for `uvicorn ea.main:app`, `main()` for
`python -m ea.reindex` — and never from `create_app`, so a test that builds an
app does not reconfigure the logging of the process running it. Four things
about it are decisions, not details — see `docs/adr/0021`.

**The request id is a `ContextVar`, set by `api/middleware.py` and put on every
record by a filter.** A call site logs what it has to say; which request it was
serving is not its business. The middleware is a plain ASGI one and not a
`BaseHTTPMiddleware`, because `/mcp` answers over a streaming transport that
`BaseHTTPMiddleware` buffers. The id is echoed in `X-Request-Id`, and CORS
exposes that header so the SPA can print it too — a line in the browser console
and a line in the server log are then the same request.

**And the order the three middlewares are added in is load-bearing.**
`create_app` adds `AnsweringUnexpectedFailures` first (innermost), then
`CORSMiddleware`, then `RequestLogging` (outermost), so that *every* answer
leaves through the CORS headers and the id — the 500 included. Starlette
answers an unhandled exception from `ServerErrorMiddleware`, which wraps
everything an app adds: while that was the normal path, a 500 arrived with
neither header, the browser refused to let the SPA read it, and `lib/api.ts`
reported « Backend injoignable » for an API that was running and had just
answered. The envelope is therefore sent by that innermost middleware
(`api/errors.py`), which logs the traceback itself — with the request id on it,
which the ASGI server's own log cannot know — and steps aside once the response
has started, because a download that fails halfway has nothing left to replace.
The handler registered for `Exception` stays as the last resort for a failure
raised by CORS or the access log themselves.

**Each noisy stream has its own name and its own switch.** `EA_LOG_LEVEL` is
the level of *our* code; `EA_LOG_SQL` (`sqlalchemy.engine`) and `EA_LOG_EMBEDDINGS` (`ea.embeddings`
plus `httpx`) are two firehoses opened one at a time. `EA_LOG_LEVEL=DEBUG`
deliberately opens none of them. The logger names are constants in
`core/logging.py`, imported by whoever writes to them, so a switch cannot drift
from the logger it is meant to open.

**`extra=` is where the facts go, and both formatters render them** — as JSON
fields for a collector, as `key=value` at the end of the line for a terminal. A
key colliding with a `LogRecord` attribute (`args`, `module`, `name`) raises at
emit time; `action`, `element_id`, `duration_ms`, `status` and `tool` do not.
Secrets are masked by `RedactingFilter` on the handler, per the rule above.

**Every write logs one line in `services/`, and that is on purpose**: `POST
/elements -> 201` says an element was created and not which, and `/mcp` reaches
the same use cases without HTTP at all. The MCP side is traced inside
`speaking_plainly` — the decorator every tool already carries — rather than by
a third decorator somebody has to remember. The SPA's half is
`lib/logging.ts` (`VITE_LOG_LEVEL`, `console`, no dependency) wired into
`lib/api.ts` as an `openapi-fetch` middleware.

## One question at a time in the SPA

**Every load that a watch, the URL or repeated input can trigger goes through
`useLatestRequest()`** (`lib/latest.ts`). Asking again aborts the request in
flight and drops whatever it still produces, so the answer on screen is the
answer to the question asked last — without it, two neighbours clicked in quick
succession leave the drawing on the first while `?element=` names the second.
One instance per independent question (the subnet list and the subnet opened
beside it must not cancel each other), and the task hands its `signal` to
`openapi-fetch`. `clear`/`close` call `cancel()`; a search box waits for a pause
through `lib/debounce.ts`. A load failure lands in the composable's state
(`error`, `lookupError`, `readError`) rather than being thrown; a write's
refusal is still the screen's to show.

ESLint is a flat config, `frontend/eslint.config.js`, on
`@vue/eslint-config-typescript` with the type-aware rules on — and
`no-floating-promises` / `no-misused-promises` are the reason: an unawaited load
is how a stale answer reaches the screen. `src/api/` is ignored, since it is
generated. A few Vue template-layout rules are off on purpose: they decide where
a newline goes, not whether the code is right, and turning them on rewrites
every template.

## TDD is the default working mode

Write the failing test first, watch it fail for the right reason, then make it pass. Concretely, per change:

1. **Unit** (`tests/unit`, no I/O): domain rules, validation, pure functions. Milliseconds.
2. **Integration** (`tests/integration`): repositories and services against a real PostgreSQL — the **throwaway local container** of `docker-compose.yml`, never the shared database (see `docs/adr/0024`). No mocked driver, no faked records — these exist to prove the SQL. Each test migrates the chain up to `head` (`engine_at_head`) and back to `base`. So the fixtures refuse any host that is not loopback — and, even on loopback, PostgreSQL's 5432, where the shared database listens (`docs/adr/0029`); the throwaway container publishes 5433 (`tests/integration/throwaway.py`) — and *skip* with a message naming it. A throwaway MinIO on `127.0.0.1:9100` sits beside it, for `ObjectStore`: `refuse_a_shared_minio` skips the same way on any other endpoint, since a destructive test empties the bucket it runs against (`docs/adr/0036`). `make test-integration` starts both containers and points at them; a bare `uv run pytest` skips these tests.
3. **API** (`tests/e2e` backend-side): `httpx.AsyncClient` against the app, covering status codes and error envelopes, and auth: 401 without a token, 403 for a reader who writes, `GET /me`.

**Every test has a caller.** The autouse `_an_editor_is_calling` fixture in `tests/conftest.py` acts as `an_editor()`, because a suite about what a use case *does* is not about who may run it; a refusal is tested with `nobody_calling` or `acting_as(a_reader())`. An API test builds `Settings(..., auth_enabled=False)` unless it tests auth, and then hands the app a `StaticVerifier` — tokens known in advance, no Keycloak.

**No test leaves this machine.** An autouse fixture in `tests/conftest.py` patches `socket` and raises `NetworkAccessInTestError` on any connection or name lookup that is not loopback. The settings default to the shared stores, so a lifespan test must inject doubles (`architecture_service=`, `documents=`, `indexer=`) or turn the stores off in the `Settings` it builds (`postgres_enabled=False`, `embeddings_enabled=False`).

`pipelines/tests/conftest.py` copies that same autouse socket guard rather than importing it — the two projects share no code, only the rule. Every test doubles the two outbound HTTP calls with `httpx.MockTransport` (the EA API in `test_ea.py`, LiteLLM in `test_llm.py`) and MinIO with a fake S3 object (`test_storage.py`); no test opens a socket to another machine, and `test_network_guard.py` proves the guard refuses one. `prefect_test_harness` (a temporary Prefect server, served on loopback — which is why the guard leaves loopback open) is used only in `test_catalogue.py`, to run the `alimenter-catalogue` flow itself for real — every other suite tests a function directly, with no Prefect runtime involved.

Rules that matter here: every bug fix starts with a regression test reproducing it; tests assert behaviour through public entry points, not private attributes; objects are built by small hand-written helpers with defaults and overrides (`an_element`, `a_document`) so adding a field never breaks a hundred tests — no factory library is installed; no `time.sleep` — inject a clock. Coverage floor is 90% on `backend/src`, enforced by `fail_under = 90` in `pyproject.toml` for any run with `--cov` (`make test`, hence `make check` and CI) — but a covered line proving nothing is a failure regardless of the number.

## Security rules for this stack

- Config comes from environment via `pydantic-settings` only. No literal secret, DSN, or key in code or tests — commit `.env.example`, never `.env`. `Settings` refuses to build without a PostgreSQL password while `postgres_enabled` is on, unless `EA_DEBUG` is on.
- Auth: Keycloak, realm `ea` — EA stores no password. Every access token is verified by `repositories/keycloak.py`: RS256 only, never the algorithm the token names; `iss`, `aud=ea-api`, `exp`, `iat`, `sub` required; keys from the realm's JWKS, refetched on an unknown `kid` at most once per interval. Authorization is enforced in `services/` (`require_caller` / `require_editor`), failing closed, never only in the router, and never in the frontend — the SPA asks `GET /me` and hides UI, the API decides. `EA_AUTH_ENABLED=false` is refused outside `EA_DEBUG`. See `docs/adr/0032`.
- All DB access goes through SQLAlchemy constructs; raw `text()` requires bound parameters and a comment justifying it.
- Request bodies are Pydantic models with explicit constraints; response models are declared so internal fields cannot leak. `model_config = ConfigDict(extra="forbid")` on inputs.
- CORS is an explicit allowlist from settings — never `allow_origins=["*"]` with credentials.
- `settings.debug` is never handed to FastAPI, which would answer an unhandled exception with a traceback to whoever on the LAN caused it; that exception gets the typed `internal_error` 500 envelope instead.
- Errors returned to clients are typed and generic; stack traces and DB messages go to structured logs (stdlib `logging` through `core/logging.py` — JSON in a deployment, with a request id), never to the response body. Redaction is a filter on the handler, never a call site's job — see `docs/adr/0021`. **`pydantic.ValidationError` is a `ValueError`**, so the handler that answers a rejected input with 422 and `str(error)` would hand over the model, the field and the value pydantic refused: it has its own handler in `api/errors.py`, answering the mute 500, and Starlette's MRO lookup makes that one win whatever order they are registered in. A request body that does not validate is unaffected — FastAPI raises `RequestValidationError`, which is not a `ValueError`.
- Never log tokens, passwords, or PII. Redact at the logging processor, not at each call site.
- Frontend: no `dangerouslySetInnerHTML` without sanitisation; tokens in memory (`oidc-client-ts`'s `InMemoryWebStorage`), never `localStorage` — only the single-use PKCE state goes to `sessionStorage`. A 401 restarts the login once per page load, and not at all right after a login completed (`REAUTH_GUARD_MS`), so an API refusing a token Keycloak accepts cannot loop the browser.
- `bandit`, `pip-audit --skip-editable` and `npm audit --audit-level=high` run in CI (`.github/workflows/ci.yml`) and locally as `make audit`. The npm audit of production dependencies tolerates nothing; the development one accepts the advisories named in `frontend/audit-exceptions.json` until they expire (`scripts/npm_audit.py`, `docs/audit-exceptions.md`). A false positive in `src` is silenced at the line, `# nosec BXXX` with its reason — never by a global exclusion. Dependabot (`.github/dependabot.yml`) keeps the actions, both lockfiles and the compose images current. **Nothing blocks a red merge yet**: requiring green CI is a GitHub branch-protection rule still to switch on — and since `docs/adr/0041` a merge to `main` is a deploy.
- Stack images are pinned by tag *and* digest (`deploy/*.stack.yml`), so a redeploy from Portainer cannot change server without a commit.
- `pipelines/.env` follows the same rule as `backend/.env`: never committed (`.gitignore`), every secret in `pipelines/.env.example` left blank. The passwords embedded in its two new PostgreSQL URLs (`PREFECT_DATABASE_URL`, `LITELLM_DATABASE_URL`) are generated with `openssl rand -hex 32`, never chosen by hand, and Prefect's and LiteLLM's own connection strings are the one place a DSN is written as a URL rather than assembled field by field (the exception recorded under *The relational store holds the documents and their index*, above). Every port `pipelines/docker-compose.yml` publishes is bound to `127.0.0.1`, enforced by `pipelines/tests/test_stack.py`; the infra CA certificate is mandatory and mounted read-only (`INFRA_CA_CERT` on the host, at the path compose fixes as `PIPELINES_S3_CA_CERT`), never baked into the image — an empty file in its place would fail every TLS call to MinIO. `pipelines/.env` is read by `docker compose --env-file` only: `Settings` has no `env_file`, and the worker has none either, receiving by name only the `PIPELINES_*` variables `Settings` reads — never `ANTHROPIC_API_KEY`, `LITELLM_MASTER_KEY` or the two database URLs (`test_stack.py` checks both).

## SDLC

- Branch from `main`: `feat/`, `fix/`, `chore/`, `docs/`. `main` stays releasable.
- [Conventional Commits](https://www.conventionalcommits.org/) — the changelog and version bump are derived from them.
- `pre-commit` (`.pre-commit-config.yaml`) runs ruff (format + check), mypy, vue-tsc, ESLint and gitleaks, and modifies nothing. It is opt-in: `make hooks` installs it, nothing else writes to `.git/hooks`. Do not `--no-verify`. `pipelines/` gets its own three hooks (`pipelines-ruff-format`, `pipelines-ruff-check`, `pipelines-mypy`), scoped to `^pipelines/.*\.py$` and run inside its own venv (`cd pipelines && uv run --extra dev ...`) — the same separation as its lockfile.
- Every PR: green CI per `docs/adr/0026` (lint, types, generated-client check, tests with the coverage gate, the three scans, integration against throwaway containers), small enough to review, description stating what and why.
- Structural or cross-cutting decisions (new dependency, new bounded context, auth change, storage change, a new entry in `_EXTRA_ALLOWED`) get an ADR in `docs/adr/NNNN-title.md` — context, decision, consequences. Supersede ADRs, don't edit history.

## Definition of done

A change is done when: tests written first and passing; `ruff`, `mypy --strict`, and the security scans clean; any new constraint shipped as an Alembic revision and applied cleanly to a database that already had data; OpenAPI client regenerated if the schema moved; docs/ADR updated; and any behaviour visible to a user is exercised by an E2E test.
