# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**The element catalogue and the links between elements work end to end; the two traversals are backend-only.** A root `Makefile` orchestrates local development. `backend/` serves a FastAPI app with the full ArchiMate 3.2 metamodel, an element/relationship catalogue and two graph traversals, stored in Neo4j. `frontend/` is a Vue 3 SPA: a routed shell whose section menu is generated from `src/router/sections.ts` (see `docs/adr/0008`), with two sections built — the element catalogue, which browses, creates, edits and deletes elements through the generated OpenAPI client (see `docs/adr/0007`), and relations, which lists the links of one element and adds one, offering only what the metamodel permits for the pair (see `docs/adr/0009`; the same panel opens from a catalogue row). The metamodel and the two traversals are declared in the menu as *à venir* and have no screen yet: they want a drawing, not a table. This file records the *decisions already made* so that any instance building here converges on the same design instead of inventing its own. When a decision here turns out to be wrong, change this file in the same commit that changes the code, and record the change in `docs/adr/`.

**Not yet scaffolded** (do not assume these exist): auth, SQLAlchemy, Alembic, any PostgreSQL table, `bandit`, `pip-audit`, ESLint (`npm run lint`), Playwright, `pre-commit`, CI, any frontend view of the graph itself.

`EA` = Enterprise Architecture. Expect domain modelling (capabilities, applications, flows, owners) to be the core of the backend, not CRUD-for-its-own-sake.

## Locked stack decisions

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic | Typed end to end; the OpenAPI schema is the front/back contract |
| Architecture graph | Neo4j (`neo4j` async driver, Cypher) | The model *is* a graph; impact analysis is a variable-depth traversal — see `docs/adr/0004` |
| Metamodel | ArchiMate 3.2, complete | 61 element types, 11 relationship types, rules-based validation — see `docs/adr/0005` |
| Everything not a graph | PostgreSQL + SQLAlchemy 2 (async) + Alembic | Auth, audit, scheduled work. **No table exists yet**; add the dependencies with the first one |
| Python tooling | `uv` (deps + venv), `ruff` (lint + format), `mypy --strict` | Single fast toolchain, one lockfile |
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
  src/{components,lib}/                # shared components (the shell menu); hand-written glue (the API client)
  tests/                               # Vitest specs, mirroring src/
docs/adr/                              # architecture decision records
```

**Dependency direction is one-way: `api → services → domain ← repositories`.** `domain/` imports nothing from FastAPI, SQLAlchemy, or `api/`. A test that needs a database is not a unit test — move it to `tests/integration/`.

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

Whole stack: `make run`. The graph is a single instance on the Docker cluster (192.168.1.252), deployed as a Portainer stack from `deploy/neo4j.stack.yml` — see `docs/adr/0006`. Nothing starts it locally: `make db-ping` checks it answers, `make db-stack` recalls how to deploy it, `make db-shell` opens a `cypher-shell` on it, `make db-reset` empties it (`CONFIRM=yes`, and it is everyone's graph). The Neo4j browser is on http://192.168.1.252:7474. The password lives in `backend/.env`, never in a committed file. `make pg-up` starts the still-unused local PostgreSQL.

`make check` runs lint, types (backend and frontend), the generated-client check and the DB-free suites on both sides — what CI will check.

## Front/back contract

The backend's OpenAPI schema is the single source of truth. **Never hand-write a TypeScript interface that mirrors a Pydantic model** — regenerate `frontend/src/api/` and import from there. A backend change that alters the schema and does not regenerate the client is an incomplete change.

`backend/openapi.json` and `frontend/src/api/schema.d.ts` are both committed. `make openapi` regenerates the pair; `make openapi-check` fails when they no longer match the code, and `make check` runs it. `openapi-fetch` calls the generated types; `src/lib/api.ts` holds the base URL and the error handling — the only hand-written half — and nothing else there describes a payload. See `docs/adr/0007`.

## Adding a section to the SPA

Sections live in one list: `frontend/src/router/sections.ts`. Add an entry to
`SECTIONS` — path, route name, label, one-line summary, group — and both the
router and the menu follow; there is no second list to keep in step. Leave `view`
out until the screen exists: the menu then shows the section greyed out with an
*à venir* badge and the router creates no route for it, so a link can never
resolve to a screen that is not there. When you build it, add
`view: () => import('../features/<section>/<Screen>.vue')` — the lazy import is
what keeps each section in its own bundle chunk. A new group is an entry in
`GROUPS`. See `docs/adr/0008`.

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
