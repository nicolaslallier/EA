# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Every declared section works end to end.** A root `Makefile` orchestrates local development. `backend/` serves a FastAPI app with the full ArchiMate 3.2 metamodel, an element/relationship catalogue and two graph traversals, stored in Neo4j. `frontend/` is a Vue 3 SPA: a routed shell whose section menu is generated from `src/router/sections.ts` (see `docs/adr/0008`), with five sections built — the element catalogue, which browses, creates, edits and deletes elements through the generated OpenAPI client (see `docs/adr/0007`) and opens the full detail of one when its name is clicked, under `?element=` (see `docs/adr/0011`); relations, which lists the links of one element and adds one, offering only what the metamodel permits for the pair (see `docs/adr/0009`; the same panel opens from a catalogue row); neighbourhood, which *draws* the sub-graph around an element on concentric rings, one per hop, and moves the centre when a neighbour is clicked (see `docs/adr/0010`); metamodel, which reads the ArchiMate 3.2 reference itself — the 61 types by layer, the 11 relationships with their family and the way impact travels, and one row of the 61x61 matrix at a time (see `docs/adr/0012`); and impact analysis, which draws the same rings around an element and reads them as how far a failure travels, plus the list of what breaks, wave by wave (see `docs/adr/0013`). The same backend also speaks **MCP**: `/mcp` offers the whole architecture service to an agent as fourteen tools — the element CRUD, the links, the two traversals and the metamodel — as an adapter *beside* `api/` rather than a client of it, so every ArchiMate rule is enforced for an agent without one line of them being restated (see `docs/adr/0014`). This file records the *decisions already made* so that any instance building here converges on the same design instead of inventing its own. When a decision here turns out to be wrong, change this file in the same commit that changes the code, and record the change in `docs/adr/`.

**Not yet scaffolded** (do not assume these exist): auth, SQLAlchemy, Alembic, any PostgreSQL table, `bandit`, `pip-audit`, ESLint (`npm run lint`), Playwright, `pre-commit`, CI.

`EA` = Enterprise Architecture. Expect domain modelling (capabilities, applications, flows, owners) to be the core of the backend, not CRUD-for-its-own-sake.

## Locked stack decisions

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic | Typed end to end; the OpenAPI schema is the front/back contract |
| Architecture graph | Neo4j (`neo4j` async driver, Cypher) | The model *is* a graph; impact analysis is a variable-depth traversal — see `docs/adr/0004` |
| Metamodel | ArchiMate 3.2, complete | 61 element types, 11 relationship types, rules-based validation — see `docs/adr/0005` |
| Everything not a graph | PostgreSQL + SQLAlchemy 2 (async) + Alembic | Auth, audit, scheduled work. **No table exists yet**; add the dependencies with the first one |
| Python tooling | `uv` (deps + venv), `ruff` (lint + format), `mypy --strict` | Single fast toolchain, one lockfile |
| Agent-facing API | MCP (`mcp` SDK 2.x), streamable HTTP served at `/mcp` | A second adapter over the same service, not a second API — see `docs/adr/0014` |
| Frontend | Vue 3 (`<script setup>`) + TypeScript + Vite | SPA consuming the generated OpenAPI client — see `docs/adr/0002` |
| Frontend routing | `vue-router` 4, `history` mode | Routes and menu are both derived from one section catalogue — see `docs/adr/0008` |
| Frontend tests | Vitest + Testing Library, Playwright for E2E | Unit/component in-process, E2E against a real stack |
| Containers | Docker + `docker compose` for local Postgres and E2E | Reproducible; no "works on my machine" DB |

Do not introduce a second HTTP client, ORM, state manager, or test runner alongside these without recording an ADR.

## Repository layout

```
Makefile         # single entry point for local dev — see docs/adr/0001
backend/
  src/ea/
    api/           # FastAPI routers, request/response schemas, dependencies
    mcp/           # the same service offered to an agent as MCP tools
    domain/        # entities, value objects, domain services — NO framework imports
      archimate/   # the ArchiMate 3.2 metamodel: taxonomy, relations, rules
    services/      # use cases; orchestrate domain + repositories, own transactions
    repositories/  # Cypher implementations of the ports declared in domain
    db/            # Neo4j driver lifecycle and schema (constraints + indexes)
    core/          # config (pydantic-settings), security, logging, errors
  tests/{unit,integration,e2e}/
frontend/
  src/api/                             # GENERATED ONLY — never hand-write there
  src/router/                          # the section catalogue, the routes it produces, the 404
  src/features/                        # one directory per screen: components + its composables
  src/{components,lib}/                # shared components (the shell menu, the graph drawing); hand-written glue (the API client, the ring geometry)
  tests/                               # Vitest specs, mirroring src/
docs/adr/                              # architecture decision records
```

**Dependency direction is one-way: `api → services → domain ← repositories`.** `mcp/` sits *beside* `api/` in that arrow — same distance from the domain, never behind it. `domain/` imports nothing from FastAPI, SQLAlchemy, or `api/`. A test that needs a database is not a unit test — move it to `tests/integration/`.

## Commands

Everyday local development goes through the root `Makefile` (`make help` lists the
targets):

```bash
make install                    # uv sync + npm install
make run                        # backend and frontend in parallel, interleaved logs
make run-be                     # backend only  — http://127.0.0.1:8000
make run-fe                     # frontend only — http://localhost:5173
make run-be BE_PORT=8001        # every port is an overridable variable
make clean                      # drop .venv, node_modules, caches, build output
```

The Makefile deliberately covers running the stack, not testing it: tests, lint
and type checks are invoked directly, as below.

Backend (run from `backend/`):

```bash
uv sync --all-extras            # install/refresh the venv from uv.lock
uv run uvicorn ea.main:app --reload    # or `make run-be` from the repo root
uv run pytest                   # full suite
uv run pytest tests/unit -q     # fast loop, no DB
uv run pytest tests/unit/test_capability.py::test_rename -x  # single test
uv run pytest --cov=ea --cov-report=term-missing --cov-fail-under=90
make test-integration           # against the real Neo4j — EMPTIES the SHARED cluster graph
uv run ruff format . && uv run ruff check --fix .
uv run mypy src
uv run bandit -c pyproject.toml -r src
uv run pip-audit
```

Frontend (run from `frontend/`):

```bash
npm ci
npm run dev
npm test -- --run                       # Vitest once (no watch)
npm test -- tests/BackendStatus.spec.ts  # single file/dir
npm run test:e2e                         # Playwright — NOT SET UP YET
npm run lint && npm run typecheck         # lint NOT SET UP YET (no ESLint config)
npm run generate:api                     # regenerate src/api/ — or `make openapi` from the root
```

Whole stack: `make run`. `make run-be` also serves the MCP tools at <http://127.0.0.1:8000/mcp>; the committed `.mcp.json` points Claude Code at it, and `EA_MCP_ENABLED=false` turns it off. The graph is a single instance on the Docker cluster (192.168.1.252), deployed as a Portainer stack from `deploy/neo4j.stack.yml` — see `docs/adr/0006`. Nothing starts it locally: `make db-ping` checks it answers, `make db-stack` recalls how to deploy it, `make db-shell` opens a `cypher-shell` on it, `make db-reset` empties it (`CONFIRM=yes`, and it is everyone's graph). The Neo4j browser is on http://192.168.1.252:7474. The password lives in `backend/.env`, never in a committed file. `make pg-up` starts the still-unused local PostgreSQL.

`make check` runs lint, types (backend and frontend), the generated-client check and the DB-free suites on both sides — what CI will check.

## Front/back contract

The backend's OpenAPI schema is the single source of truth. **Never hand-write a TypeScript interface that mirrors a Pydantic model** — regenerate `frontend/src/api/` and import from there. A backend change that alters the schema and does not regenerate the client is an incomplete change.

`backend/openapi.json` and `frontend/src/api/schema.d.ts` are both committed. `make openapi` regenerates the pair; `make openapi-check` fails when they no longer match the code, and `make check` runs it. `openapi-fetch` calls the generated types; `src/lib/api.ts` holds the base URL and the error handling — the only hand-written half — and nothing else there describes a payload. See `docs/adr/0007`.

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

Adding a tool means: a method on the service if it is a new use case, a
function in `mcp/server.py` decorated with `@server.tool(annotations=...)` and
`@speaking_plainly`, and an entry in the whole-list assertion in
`tests/unit/test_mcp_server.py`. Two things are part of the behaviour and not
decoration — the docstring, which is what the model reads to decide whether to
call it, and the annotation, which is what a client shows the person who has to
approve a write. A tool that deletes says so.

Domain failures come back as `ToolError` (`mcp/errors.py`), the exact
counterpart of `api/errors.py`: an anticipated refusal reaches the model with
its message so it can correct itself, anything else stays in the logs.

The transport's routes are *spliced* onto the FastAPI app rather than mounted,
because `Mount("/mcp", …)` answers a bare `POST /mcp` with a 307. It is a
Starlette route, so **`/mcp` never appears in the OpenAPI schema** and no
client regeneration follows from it. See `docs/adr/0014`.

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
`docs/adr/0011`), and the metamodel for the cell of the matrix it is showing
(`?source=`, `?relation=`, see `docs/adr/0012`); an unsaved form is not that kind
of state and stays in a `ref`.

## The graph has no Alembic

Neo4j has no schema to migrate; it has constraints and indexes. `backend/src/ea/db/schema.py` declares them with `IF NOT EXISTS` and the application applies the whole list at startup, so adding one is adding a line to `SCHEMA_STATEMENTS`. Renaming a stored value — an element type, say — is a *data* migration and needs a versioned Cypher script; that has not come up yet.

Elements are `:Element` nodes with the ArchiMate type as an indexed property; relationships carry their ArchiMate type as the real Neo4j relationship type. User-defined attributes are stored flat under a `p_` prefix so they stay queryable. `db/schema.py` explains why.

## TDD is the default working mode

Write the failing test first, watch it fail for the right reason, then make it pass. Concretely, per change:

1. **Unit** (`tests/unit`, no I/O): domain rules, validation, pure functions. Milliseconds.
2. **Integration** (`tests/integration`): repositories and services against a real Neo4j. No mocked driver, no faked records — these exist to prove the Cypher. Neo4j Community serves one database, so isolation is "empty the graph between tests" rather than a rolled-back transaction; that is destructive, so it is gated behind `EA_ALLOW_DESTRUCTIVE_TESTS=1`, which only `make test-integration` sets. A bare `uv run pytest` skips them.
3. **API** (`tests/e2e` backend-side): `httpx.AsyncClient` against the app, covering auth, status codes, and error envelopes.

Rules that matter here: every bug fix starts with a regression test reproducing it; tests assert behaviour through public entry points, not private attributes; fixtures build objects via factories (`polyfactory`/`factory_boy`) so adding a field never breaks a hundred tests; no `time.sleep` — inject a clock. Coverage floor is 90% on `backend/src`, but a covered line proving nothing is a failure regardless of the number.

## Security rules for this stack

- Config comes from environment via `pydantic-settings` only. No literal secret, DSN, or key in code or tests — commit `.env.example`, never `.env`. `Settings` refuses to build without a Neo4j password unless `EA_DEBUG` is on.
- Auth: OAuth2 password/bearer with short-lived JWT access tokens plus refresh; `argon2` for password hashing. Authorization is enforced in `services/`, never only in the router, and never in the frontend — the SPA hides UI, the API decides.
- All DB access goes through SQLAlchemy constructs; raw `text()` requires bound parameters and a comment justifying it. **Cypher follows the same rule**: every runtime value is a bound parameter. Cypher cannot parameterise a relationship type or the bound of a variable-length path — those three call sites build from a closed enum or a clamped integer and each says so in a comment. Adding a fourth needs the same justification.
- Request bodies are Pydantic models with explicit constraints; response models are declared so internal fields cannot leak. `model_config = ConfigDict(extra="forbid")` on inputs.
- CORS is an explicit allowlist from settings — never `allow_origins=["*"]` with credentials.
- Errors returned to clients are typed and generic; stack traces and DB messages go to structured logs (`structlog`, JSON, with a request id), never to the response body.
- Never log tokens, passwords, or PII. Redact at the logging processor, not at each call site.
- Frontend: no `dangerouslySetInnerHTML` without sanitisation; tokens in memory or httpOnly cookies, not `localStorage`.
- `bandit`, `pip-audit`, and `npm audit --audit-level=high` run in CI and block merges. Dependabot/Renovate keeps lockfiles current.

## SDLC

- Branch from `main`: `feat/`, `fix/`, `chore/`, `docs/`. `main` stays releasable.
- [Conventional Commits](https://www.conventionalcommits.org/) — the changelog and version bump are derived from them.
- `pre-commit` runs ruff (format + check), mypy, and secret detection. Do not `--no-verify`.
- Every PR: green CI (lint, types, tests, coverage gate, security scans, generated-client check), small enough to review, description stating what and why.
- Structural or cross-cutting decisions (new dependency, new bounded context, auth change, storage change, a new entry in `_EXTRA_ALLOWED`) get an ADR in `docs/adr/NNNN-title.md` — context, decision, consequences. Supersede ADRs, don't edit history.

## Definition of done

A change is done when: tests written first and passing; `ruff`, `mypy --strict`, and the security scans clean; any new graph constraint added to `SCHEMA_STATEMENTS` and applied cleanly to a database that already had data; OpenAPI client regenerated if the schema moved; docs/ADR updated; and any behaviour visible to a user is exercised by an E2E test.
