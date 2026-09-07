# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Scaffolded, thin.** A root `Makefile` orchestrates local development; `backend/` serves a FastAPI app with a `/health` endpoint; `frontend/` is a Vue 3 SPA that displays that health status. There is no database, no auth, and no domain model yet — the layers listed below are the agreed target, not the current tree. This file records the *decisions already made* so that any instance building here converges on the same design instead of inventing its own. When a decision here turns out to be wrong, change this file in the same commit that changes the code, and record the change in `docs/adr/`.

**Not yet scaffolded** (do not assume these exist): PostgreSQL, SQLAlchemy, Alembic, `bandit`, `pip-audit`, ESLint (`npm run lint`), `npm run generate:api`, Playwright, `pre-commit`, Docker, CI.

`EA` = Enterprise Architecture. Expect domain modelling (capabilities, applications, flows, owners) to be the core of the backend, not CRUD-for-its-own-sake.

## Locked stack decisions

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2 (async) + Alembic | Typed end to end; the OpenAPI schema is the front/back contract |
| Database | PostgreSQL | Alembic migrations, no auto-`create_all` outside tests |
| Python tooling | `uv` (deps + venv), `ruff` (lint + format), `mypy --strict` | Single fast toolchain, one lockfile |
| Frontend | Vue 3 (`<script setup>`) + TypeScript + Vite | SPA consuming the generated OpenAPI client — see `docs/adr/0002` |
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
    services/      # use cases; orchestrate domain + repositories, own transactions
    repositories/  # SQLAlchemy implementations of the ports declared in domain
    db/            # engine, session, models, alembic/
    core/          # config (pydantic-settings), security, logging, errors
  tests/{unit,integration,e2e}/
frontend/
  src/{api,features,components,lib}/   # api/ is GENERATED ONLY — never hand-write there
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
uv run ruff format . && uv run ruff check --fix .
uv run mypy src
uv run alembic revision --autogenerate -m "add capability table"
uv run alembic upgrade head
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
npm run generate:api                     # NOT SET UP YET — see the contract section
```

Whole stack: `make run`. Docker (`docker compose up -d db` before any integration/E2E run) is the intended shape once a database exists — it is not scaffolded yet.

## Front/back contract

The backend's OpenAPI schema is the single source of truth. **Never hand-write a TypeScript interface that mirrors a Pydantic model** — regenerate `frontend/src/api/` with `npm run generate:api` and import from there. A backend change that alters the schema and does not regenerate the client is an incomplete change. CI must fail if the committed client differs from a fresh generation.

## TDD is the default working mode

Write the failing test first, watch it fail for the right reason, then make it pass. Concretely, per change:

1. **Unit** (`tests/unit`, no I/O): domain rules, validation, pure functions. Milliseconds.
2. **Integration** (`tests/integration`): repositories and services against a real Postgres in a rolled-back transaction. No mocked SQLAlchemy.
3. **API** (`tests/e2e` backend-side): `httpx.AsyncClient` against the app, covering auth, status codes, and error envelopes.

Rules that matter here: every bug fix starts with a regression test reproducing it; tests assert behaviour through public entry points, not private attributes; fixtures build objects via factories (`polyfactory`/`factory_boy`) so adding a field never breaks a hundred tests; no `time.sleep` — inject a clock. Coverage floor is 90% on `backend/src`, but a covered line proving nothing is a failure regardless of the number.

## Security rules for this stack

- Config comes from environment via `pydantic-settings` only. No literal secret, DSN, or key in code or tests — commit `.env.example`, never `.env`.
- Auth: OAuth2 password/bearer with short-lived JWT access tokens plus refresh; `argon2` for password hashing. Authorization is enforced in `services/`, never only in the router, and never in the frontend — the SPA hides UI, the API decides.
- All DB access goes through SQLAlchemy constructs; raw `text()` requires bound parameters and a comment justifying it.
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
- Structural or cross-cutting decisions (new dependency, new bounded context, auth change, storage change) get an ADR in `docs/adr/NNNN-title.md` — context, decision, consequences. Supersede ADRs, don't edit history.

## Definition of done

A change is done when: tests written first and passing; `ruff`, `mypy --strict`, and the security scans clean; migration written *and* checked to downgrade; OpenAPI client regenerated if the schema moved; docs/ADR updated; and any behaviour visible to a user is exercised by an E2E test.
