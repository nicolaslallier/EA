# Fichiers dans MinIO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload, browse, download and delete files of any type in one MinIO bucket, from the SPA and from `/mcp`.

**Architecture:** A new `ObjectStore` port (domain) implemented by `MinioObjectStore` (repositories, `minio` SDK behind `asyncio.to_thread`), orchestrated by `FileService` (services, `require_caller`/`require_editor`), exposed by `api/files.py` (multipart upload, streamed download) and four MCP tools in `mcp/server.py`. The SPA gets a `/fichiers` section whose folder lives in `?prefix=`.

**Tech Stack:** FastAPI, Pydantic v2, `minio` 7.2.20, MCP SDK, Vue 3 + openapi-fetch, Vitest, pytest, docker compose (throwaway MinIO).

**Spec:** `docs/superpowers/specs/2026-09-15-fichiers-minio-design.md` — read it before your task.

## Global Constraints

- Read `CLAUDE.md` at the repo root first. Its rules win over anything here.
- Dependency direction: `api → services → domain ← repositories`; `mcp/` beside `api/`. `domain/` imports no framework.
- TDD: write the failing test, watch it fail for the right reason, then implement.
- `MAX_FILE_BYTES = 50 * 1024 * 1024`; `MAX_TEXT_READ_BYTES = 1024 * 1024`; `MAX_LISTED_ENTRIES = 1000`; `MAX_KEY_BYTES = 1024`.
- Default bucket `ea-catalogue`. Upload onto an existing key → 409 unless `overwrite=true`.
- Download always `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`.
- Every public `FileService` coroutine starts with `require_caller()` (read) or `require_editor()` (`upload`, `delete`).
- No test leaves the machine (autouse socket guard). The throwaway MinIO is `127.0.0.1:9100`, image `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` (the Infra's).
- Backend: `ruff`, `mypy --strict`, coverage ≥ 90% (`make test`). Frontend: ESLint type-aware, `vue-tsc`, Vitest.
- Never hand-write a TS type mirroring a Pydantic model: `make openapi` regenerates `frontend/src/api/`.
- User-facing SPA copy is French; code, docstrings and backend messages are English (as in the rest of the repo).
- **Do not `git commit`.** Several agents share this worktree; the controller commits each task after review. Touch only the files your task lists.

## File map

| File | Task | Responsibility |
|---|---|---|
| `backend/src/ea/domain/files.py` (new) | 1 | `StoredFile`, `FileListing`, key/prefix rules, content-type guess, limits |
| `backend/src/ea/domain/errors.py` | 1 | six file errors |
| `backend/src/ea/domain/ports.py` | 1 | `ObjectStore` protocol |
| `backend/src/ea/api/errors.py` | 1 | status mapping of the six errors |
| `backend/tests/unit/test_files.py` (new) | 1 | domain rules |
| `backend/src/ea/core/config.py` | 2 | `s3_*` settings + validator |
| `backend/pyproject.toml`, `backend/uv.lock` | 2 | `minio` dependency |
| `backend/src/ea/repositories/object_store.py` (new) | 2 | `minio_client`, `MinioObjectStore` |
| `docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml` | 2 | throwaway MinIO |
| `backend/tests/integration/{throwaway.py,conftest.py,test_object_store.py}` | 2 | guard + real-S3 tests |
| `backend/tests/unit/test_config.py`, `test_throwaway_guards.py` (integration dir) | 2 | settings + guard tests |
| `backend/src/ea/services/files.py` (new) | 3 | `FileService` |
| `backend/tests/conftest.py` | 3 | `InMemoryObjectStore`, `files_store`, `file_service` fixtures |
| `backend/tests/unit/{test_file_service.py (new),test_service_guards.py,test_write_audit.py}` | 3 | service tests |
| `backend/src/ea/api/{schemas.py,dependencies.py,files.py (new)}`, `backend/src/ea/main.py` | 4 | HTTP adapter + wiring |
| `backend/tests/e2e/test_file_api.py` (new), `backend/openapi.json`, `frontend/src/api/schema.d.ts` | 4 | API tests + regenerated contract |
| `backend/src/ea/mcp/server.py`, `backend/src/ea/main.py` (`_mount_mcp` only) | 5 | four tools |
| `backend/tests/unit/test_mcp_server.py` (+ every other `build_mcp_server(` call site in tests) | 5 | tool tests |
| `frontend/src/router/sections.ts`, `frontend/src/features/files/{useFiles.ts,FilesSection.vue}` | 6 | SPA screen |
| `frontend/tests/{useFiles.spec.ts,FilesSection.spec.ts,support/api.ts}` (+ nav/router specs if they enumerate) | 6 | SPA tests |
| `deploy/ea.stack.yml`, `deploy/ea.env.example`, `backend/.env.example`, `backend/tests/unit/test_deploy_stack.py` | 7 | deployment |
| `docs/adr/0036-fichiers-dans-minio.md` (new), `CLAUDE.md` | 7 | docs |

**Waves** (tasks in one wave touch disjoint files and may run in parallel): 1 → {2, 3} → 4 → {5, 6, 7}.

---

### Task 1: Domain — what a stored file is

**Files:**
- Create: `backend/src/ea/domain/files.py`
- Modify: `backend/src/ea/domain/errors.py` (append), `backend/src/ea/domain/ports.py` (append), `backend/src/ea/api/errors.py` (imports + `_STATUS`)
- Test: `backend/tests/unit/test_files.py`

**Interfaces:**
- Produces: `ea.domain.files` → `MAX_FILE_BYTES`, `MAX_TEXT_READ_BYTES`, `MAX_LISTED_ENTRIES`, `MAX_KEY_BYTES`, `DEFAULT_CONTENT_TYPE`, `StoredFile(key, size, last_modified, content_type)` with `.name`, `FileListing(prefix, folders: tuple[str, ...], files: tuple[StoredFile, ...], truncated: bool)`, `clean_key(key: str) -> str`, `clean_prefix(prefix: str) -> str`, `join_key(prefix: str, name: str) -> str`, `guess_content_type(key: str) -> str`.
- Produces: `ea.domain.errors` → `InvalidFileKeyError`, `FileTooLargeError`, `FileNotTextError`, `StoredFileNotFoundError`, `StoredFileExistsError`, `FileStorageUnavailableError` (all `DomainError`).
- Produces: `ea.domain.ports.ObjectStore` protocol:
  - `async list_folder(prefix: str, *, limit: int) -> FileListing`
  - `async stat(key: str) -> StoredFile | None`
  - `async put(key: str, data: bytes, *, content_type: str) -> StoredFile`
  - `async open(key: str) -> tuple[StoredFile, AsyncIterator[bytes]]` (raises `StoredFileNotFoundError`)
  - `async delete(key: str) -> None`

- [ ] **Step 1: Write the failing tests** — `backend/tests/unit/test_files.py`

```python
"""Which paths a file may be stored under, and what a stored file says about itself."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ea.domain.errors import InvalidFileKeyError
from ea.domain.files import (
    DEFAULT_CONTENT_TYPE,
    MAX_KEY_BYTES,
    StoredFile,
    clean_key,
    clean_prefix,
    guess_content_type,
    join_key,
)


class TestAKey:
    @pytest.mark.parametrize("key", ["notes.md", "inbox/notes.md", "a/b/c/rapport 2026.pdf", "é/ü.txt"])
    def test_an_ordinary_path_is_kept_as_it_is(self, key: str) -> None:
        assert clean_key(key) == key

    @pytest.mark.parametrize(
        "key",
        ["", "/notes.md", "inbox/", "inbox//notes.md", "./notes.md", "inbox/../secret", "..", "a\x00b", "a\nb"],
    )
    def test_a_path_that_could_mean_something_else_is_refused(self, key: str) -> None:
        with pytest.raises(InvalidFileKeyError):
            clean_key(key)

    def test_the_length_is_counted_in_bytes_like_s3_does(self) -> None:
        clean_key("é" * (MAX_KEY_BYTES // 2))
        with pytest.raises(InvalidFileKeyError):
            clean_key("é" * (MAX_KEY_BYTES // 2 + 1))


class TestAPrefix:
    def test_empty_is_the_top_of_the_bucket(self) -> None:
        assert clean_prefix("") == ""

    @pytest.mark.parametrize(("given", "expected"), [("inbox", "inbox/"), ("inbox/", "inbox/"), ("a/b", "a/b/")])
    def test_a_folder_always_ends_with_a_slash(self, given: str, expected: str) -> None:
        assert clean_prefix(given) == expected

    @pytest.mark.parametrize("prefix", ["/", "/inbox", "a//b", "../", "inbox/./"])
    def test_a_folder_that_could_escape_is_refused(self, prefix: str) -> None:
        with pytest.raises(InvalidFileKeyError):
            clean_prefix(prefix)

    def test_a_name_is_joined_under_its_folder(self) -> None:
        assert join_key("inbox", "notes.md") == "inbox/notes.md"
        assert join_key("", "notes.md") == "notes.md"

    def test_a_name_that_is_not_a_name_is_refused_once_joined(self) -> None:
        with pytest.raises(InvalidFileKeyError):
            join_key("inbox/", "")


def test_a_file_is_named_by_the_last_segment_of_its_key() -> None:
    stored = StoredFile(
        key="inbox/sub/notes.md", size=3, last_modified=datetime(2026, 9, 15, tzinfo=UTC), content_type="text/markdown"
    )
    assert stored.name == "notes.md"


@pytest.mark.parametrize(("key", "expected"), [("a.pdf", "application/pdf"), ("a.png", "image/png")])
def test_the_content_type_is_guessed_from_the_extension(key: str, expected: str) -> None:
    assert guess_content_type(key) == expected


def test_an_unknown_extension_is_plain_bytes() -> None:
    assert guess_content_type("dump.zzz-unknown") == DEFAULT_CONTENT_TYPE
```

- [ ] **Step 2: Run it — expect an ImportError on `ea.domain.files`**

Run: `cd backend && uv run pytest tests/unit/test_files.py -q`

- [ ] **Step 3: Append the errors** — end of `backend/src/ea/domain/errors.py`

```python
class InvalidFileKeyError(DomainError):
    """A file path S3 would accept but that could mean another file: `..`, `//`, a NUL.

    Refused rather than normalised — a path quietly rewritten is a file stored
    somewhere its author did not look for it. See docs/adr/0036.
    """


class FileTooLargeError(DomainError):
    """A file beyond what the adapter may hold in memory, or read back as text."""


class FileNotTextError(DomainError):
    """A file asked for as text that is not UTF-8 — a PDF, an image."""


class StoredFileNotFoundError(DomainError):
    """No file at that path in the bucket."""


class StoredFileExistsError(DomainError):
    """A file is already stored at that path, and overwriting it was not asked for."""


class FileStorageUnavailableError(DomainError):
    """File storage was asked for on a deployment that has none (`EA_S3_ENABLED` off).

    A configuration, not a bug — like `SearchUnavailableError` — so the caller is
    told what is missing instead of receiving a 500.
    """
```

- [ ] **Step 4: Create `backend/src/ea/domain/files.py`**

```python
"""What a stored file is, and which paths a file may be stored under.

Pure: the bucket itself is `repositories/object_store.py`'s business. A folder
is not a stored thing in S3 — it is the shared start of some keys — so a
listing *computes* folders from keys and nothing here ever writes one. See
docs/adr/0036.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ea.domain.errors import InvalidFileKeyError

#: The largest upload. The HTTP adapter reads a body under this cap, so it is
#: also the most one request makes this process hold.
MAX_FILE_BYTES: Final = 50 * 1024 * 1024
#: The largest file an agent reads as text: an answer, not a download.
MAX_TEXT_READ_BYTES: Final = 1024 * 1024
#: The most entries one listing returns; beyond it `truncated` says so.
MAX_LISTED_ENTRIES: Final = 1000
#: S3's own limit on a key, in UTF-8 bytes.
MAX_KEY_BYTES: Final = 1024
DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class StoredFile:
    key: str
    size: int
    last_modified: datetime
    content_type: str

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class FileListing:
    prefix: str
    folders: tuple[str, ...]
    files: tuple[StoredFile, ...]
    truncated: bool


def _refuse_ambiguous(path: str) -> None:
    if len(path.encode()) > MAX_KEY_BYTES:
        msg = f"a file path is at most {MAX_KEY_BYTES} bytes"
        raise InvalidFileKeyError(msg)
    if any(unicodedata.category(character) == "Cc" for character in path):
        msg = "a file path cannot hold a control character"
        raise InvalidFileKeyError(msg)
    if path.startswith("/"):
        msg = "a file path cannot start with '/'"
        raise InvalidFileKeyError(msg)
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        msg = f"a file path cannot hold an empty, '.' or '..' segment: {path!r}"
        raise InvalidFileKeyError(msg)


def clean_key(key: str) -> str:
    """The key of one file, refused when it could name another one."""
    if not key:
        msg = "a file path cannot be empty"
        raise InvalidFileKeyError(msg)
    _refuse_ambiguous(key)
    return key


def clean_prefix(prefix: str) -> str:
    """A folder: empty for the top of the bucket, otherwise ending in `/`."""
    if not prefix:
        return ""
    folder = prefix if prefix.endswith("/") else f"{prefix}/"
    _refuse_ambiguous(folder[:-1])
    return folder


def join_key(prefix: str, name: str) -> str:
    """The key of a file called `name` inside the folder `prefix`."""
    return clean_key(clean_prefix(prefix) + name)


def guess_content_type(key: str) -> str:
    guessed, _ = mimetypes.guess_type(key, strict=False)
    return guessed or DEFAULT_CONTENT_TYPE
```

- [ ] **Step 5: Append the port** — end of `backend/src/ea/domain/ports.py` (add `from collections.abc import AsyncIterator` and `from ea.domain.files import FileListing, StoredFile` to its imports, keeping the module's existing import style)

```python
class ObjectStore(Protocol):
    """Where files are kept — MinIO in a deployment, a dict in the unit tests.

    `stat` answers `None` for a missing key rather than raising, because
    "is something already there?" is a question, not a failure. `open`
    raises `StoredFileNotFoundError`, because there it is one. See docs/adr/0036.
    """

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing: ...

    async def stat(self, key: str) -> StoredFile | None: ...

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile: ...

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]: ...

    async def delete(self, key: str) -> None: ...
```

- [ ] **Step 6: Map the errors** — `backend/src/ea/api/errors.py`: import the six errors and add to `_STATUS`, before the `# --- Authentication` block:

```python
    # --- Files in MinIO (docs/adr/0036) ---
    StoredFileNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    StoredFileExistsError: (status.HTTP_409_CONFLICT, "duplicate"),
    InvalidFileKeyError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_file_path"),
    FileTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "file_too_large"),
    FileNotTextError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_text"),
    FileStorageUnavailableError: (status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable"),
```

- [ ] **Step 7: Run tests, lint, types**

Run: `cd backend && uv run pytest tests/unit/test_files.py -q && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src migrations`
Expected: all pass. (If `ports.py` would import-cycle with `domain/files.py` — it will not; `files.py` imports only `errors`.)

---

### Task 2: Settings, the MinIO repository and the throwaway MinIO

**Files:**
- Modify: `backend/src/ea/core/config.py`, `backend/pyproject.toml` + `backend/uv.lock` (via `uv add`), `docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml`, `backend/tests/integration/throwaway.py`, `backend/tests/integration/conftest.py`, `backend/tests/integration/test_throwaway_guards.py`, `backend/tests/unit/test_config.py`
- Create: `backend/src/ea/repositories/object_store.py`, `backend/tests/integration/test_object_store.py`

**Interfaces:**
- Consumes: Task 1 (`ObjectStore`, `StoredFile`, `FileListing`, `guess_content_type`, `StoredFileNotFoundError`).
- Produces: `Settings.s3_enabled: bool = False`, `s3_endpoint: str = "minio.famillelallier.net"`, `s3_secure: bool = True`, `s3_access_key: SecretStr`, `s3_secret_key: SecretStr`, `s3_bucket: str = "ea-catalogue"`, `s3_ca_cert: str | None = None`.
- Produces: `ea.repositories.object_store.minio_client(settings: Settings) -> Minio`; `MinioObjectStore(client: Minio, bucket: str)` implementing `ObjectStore` plus `async probe() -> None` (raises `RuntimeError` when the bucket is missing).
- Produces: `tests.integration.throwaway.THROWAWAY_MINIO_PORT = 9100`, `refuse_a_shared_minio(endpoint: str) -> str | None`.

- [ ] **Step 1: Failing settings tests** — append to `backend/tests/unit/test_config.py` (reuse whatever helper that file uses to build `Settings`; the shape below builds it directly):

```python
class TestFileStorage:
    def test_it_is_off_by_default_so_nothing_needs_minio_to_boot(self) -> None:
        assert Settings(debug=True).s3_enabled is False

    def test_the_default_bucket_is_the_one_the_pipeline_reads(self) -> None:
        assert Settings(debug=True).s3_bucket == "ea-catalogue"

    def test_turning_it_on_without_a_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="EA_S3_ACCESS_KEY"):
            Settings(debug=True, s3_enabled=True)

    def test_turning_it_on_with_both_keys_is_accepted(self) -> None:
        settings = Settings(debug=True, s3_enabled=True, s3_access_key="ea-api", s3_secret_key="secret")
        assert settings.s3_secret_key.get_secret_value() == "secret"
```

Run: `cd backend && uv run pytest tests/unit/test_config.py -q -k FileStorage` → FAIL (unknown field).

- [ ] **Step 2: Add the settings** — `backend/src/ea/core/config.py`, a new block after the authentication block, and a validator beside the PostgreSQL one:

```python
    # --- Files, in the Infra's MinIO — see docs/adr/0036 --------------------
    # Off by default: a machine without the keys still boots, and the file
    # endpoints answer 503 instead of the whole API refusing to start.
    s3_enabled: bool = False
    s3_endpoint: str = "minio.famillelallier.net"
    s3_secure: bool = True
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    #: The bucket `pipelines/` reads: a file put under `inbox/` feeds it.
    s3_bucket: str = "ea-catalogue"
    #: The Infra CA, when the endpoint is reached through the Infra NGINX.
    s3_ca_cert: str | None = None
```

```python
    @model_validator(mode="after")
    def _require_s3_keys_when_the_store_is_used(self) -> "Settings":
        """Anonymous access to a bucket is never what is meant."""
        if self.s3_enabled and not (
            self.s3_access_key.get_secret_value() and self.s3_secret_key.get_secret_value()
        ):
            msg = "EA_S3_ACCESS_KEY and EA_S3_SECRET_KEY are required while EA_S3_ENABLED is on"
            raise ValueError(msg)
        return self
```

Run the Step 1 tests → PASS.

- [ ] **Step 3: Add the dependency**

Run: `cd backend && uv add "minio>=7.2.20"` then, in `pyproject.toml`, put a comment above the new line in the style of its neighbours: `# Files in the Infra's MinIO — see docs/adr/0036. Same SDK as pipelines/.`

- [ ] **Step 4: The throwaway guard — failing tests** — append to `backend/tests/integration/test_throwaway_guards.py`:

```python
class TestTheMinioGuard:
    @pytest.mark.parametrize("endpoint", ["127.0.0.1:9100", "localhost:9100", "[::1]:9100"])
    def test_the_throwaway_one_is_accepted(self, endpoint: str) -> None:
        assert refuse_a_shared_minio(endpoint) is None

    @pytest.mark.parametrize(
        "endpoint", ["minio.famillelallier.net", "minio:9000", "127.0.0.1:9000", "127.0.0.1", "192.168.2.10:9100"]
    )
    def test_anything_else_may_be_a_bucket_somebody_uses(self, endpoint: str) -> None:
        assert "9100" in (refuse_a_shared_minio(endpoint) or "")
```

(import `refuse_a_shared_minio` from `tests.integration.throwaway`). Run: `uv run pytest tests/integration/test_throwaway_guards.py -q` → FAIL (ImportError).

- [ ] **Step 5: Implement the guard** — append to `backend/tests/integration/throwaway.py`:

```python
#: Where `make minio-up` publishes the throwaway MinIO. Unlike PostgreSQL, the
#: shared MinIO is reached by a name, not a port — so the rule is stricter:
#: loopback *and* this exact port, nothing else is ever emptied.
THROWAWAY_MINIO_PORT = 9100


def refuse_a_shared_minio(endpoint: str) -> str | None:
    """Why the MinIO at `endpoint` must not have its bucket emptied, or `None`."""
    host, _, port = endpoint.rpartition(":")
    if host and is_loopback(host) and port == str(THROWAWAY_MINIO_PORT):
        return None
    return (
        f"refusing to empty a bucket on MinIO at {endpoint}: only the throwaway one on "
        f"127.0.0.1:{THROWAWAY_MINIO_PORT} is. Start it with `make minio-up` — "
        "`make test-integration` does it and sets EA_S3_*"
    )
```

Run → PASS.

- [ ] **Step 6: The throwaway container** — in `docker-compose.yml`, add a service and its volume (update the file's header comment to say it now holds two throwaway stores):

```yaml
  minio:
    # L'image de la stack Infra (~/OpenCode/Infra/docker-compose.yml) : un test
    # qui passe ici parle le même S3 que la production. MinIO ne publie plus
    # d'images, d'où quay.io et une release figée.
    image: quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z
    container_name: ea-minio
    command: ["server", "/data"]
    # 9100 : les fixtures refusent tout autre MinIO (tests/integration/throwaway.py).
    ports:
      - "127.0.0.1:${MINIO_TEST_PORT:-9100}:9000"
    environment:
      MINIO_ROOT_USER: ea-test
      MINIO_ROOT_PASSWORD: "${MINIO_TEST_PASSWORD:-developmentonly}"
    volumes:
      - minio-data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 20
```

and under `volumes:` add `minio-data:`.

- [ ] **Step 7: Makefile** — next to `POSTGRES_TEST_PASSWORD ?= developmentonly` (line ~163) add `MINIO_TEST_PORT ?= 9100` and `MINIO_TEST_PASSWORD ?= developmentonly`; add both to `COMPOSE_TEST` the way `POSTGRES_TEST_PORT` is passed. Add a target beside `pg-up`:

```make
minio-up: ## Démarre le MinIO jetable local (pour les tests)
	$(COMPOSE_TEST) up -d --wait minio
```

Change `test-integration` to start both and pass the S3 variables (**not** `EA_S3_ENABLED`: `test_application_boot` must still boot without a bucket):

```make
test-integration: | $(VENV_STAMP) ## Tests d'intégration contre PostgreSQL et MinIO jetables locaux (les démarre au besoin)
	$(COMPOSE_TEST) up -d --wait postgres minio
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		EA_EMBEDDINGS_ENABLED=false \
		EA_S3_ENDPOINT=127.0.0.1:$(MINIO_TEST_PORT) EA_S3_SECURE=false \
		EA_S3_ACCESS_KEY=ea-test EA_S3_SECRET_KEY='$(MINIO_TEST_PASSWORD)' EA_S3_BUCKET=ea-test \
		uv run pytest tests/integration -q
```

- [ ] **Step 8: CI** — `.github/workflows/ci.yml`, job `integration`: rename it `Intégration — PostgreSQL et MinIO jetables`; add a step after `make install-be` (GitHub `services:` cannot pass MinIO its `server /data` command, so compose starts it from the same file as locally):

```yaml
      - name: MinIO jetable
        run: docker compose up -d --wait minio
```

and add to the pytest step's `env:`:

```yaml
          EA_S3_ENDPOINT: 127.0.0.1:9100
          EA_S3_SECURE: "false"
          EA_S3_ACCESS_KEY: ea-test
          EA_S3_SECRET_KEY: developmentonly
          EA_S3_BUCKET: ea-test
```

- [ ] **Step 9: Integration fixture** — append to `backend/tests/integration/conftest.py`:

```python
@pytest_asyncio.fixture
async def minio_store() -> AsyncIterator[MinioObjectStore]:
    """The bucket of the throwaway MinIO, emptied afterwards — or a skip.

    Same rule as `postgres_engine`: the endpoint is checked before anything
    connects, because the settings default to the Infra's MinIO, where the
    pipeline reads real sources.
    """
    try:
        settings = Settings(debug=True, s3_enabled=True)
    except ValidationError:
        pytest.skip("EA_S3_ACCESS_KEY/EA_S3_SECRET_KEY unset — `make test-integration` sets them")
    refusal = refuse_a_shared_minio(settings.s3_endpoint)
    if refusal is not None:
        pytest.skip(refusal)
    client = minio_client(settings)
    bucket = settings.s3_bucket
    try:
        if not await asyncio.to_thread(client.bucket_exists, bucket):
            await asyncio.to_thread(client.make_bucket, bucket)
    except (S3Error, urllib3.exceptions.HTTPError) as error:
        pytest.skip(f"the throwaway MinIO at {settings.s3_endpoint} does not answer: {error}")
    yield MinioObjectStore(client, bucket)

    def empty() -> None:
        for found in client.list_objects(bucket, recursive=True):
            if found.object_name is not None:
                client.remove_object(bucket, found.object_name)

    await asyncio.to_thread(empty)
```

Imports to add: `import urllib3`, `from minio.error import S3Error`, `from pydantic import ValidationError`, `from ea.repositories.object_store import MinioObjectStore, minio_client`, `from tests.integration.throwaway import refuse_a_postgres..., refuse_a_shared_minio`.

- [ ] **Step 10: Failing integration tests** — `backend/tests/integration/test_object_store.py`:

```python
"""`MinioObjectStore` against a real S3 — the throwaway MinIO, never the Infra's.

No mocked driver: what is under test is what MinIO answers, including the error
codes `stat` translates and the way a delimiter turns keys into folders.
"""

from __future__ import annotations

import pytest

from ea.domain.errors import StoredFileNotFoundError
from ea.repositories.object_store import MinioObjectStore

pytestmark = pytest.mark.asyncio


async def test_a_stored_file_is_described_by_what_was_stored(minio_store: MinioObjectStore) -> None:
    stored = await minio_store.put("inbox/notes.md", b"# Notes\n", content_type="text/markdown")

    assert (stored.key, stored.size, stored.content_type) == ("inbox/notes.md", 8, "text/markdown")
    assert await minio_store.stat("inbox/notes.md") == stored


async def test_a_missing_key_is_none_not_an_error(minio_store: MinioObjectStore) -> None:
    assert await minio_store.stat("nothing/here.txt") is None


async def test_the_bytes_come_back_exactly_across_several_chunks(minio_store: MinioObjectStore) -> None:
    payload = bytes(range(256)) * 1024  # 256 KiB, several 64 KiB chunks
    await minio_store.put("big.bin", payload, content_type="application/octet-stream")

    stored, chunks = await minio_store.open("big.bin")

    assert stored.size == len(payload)
    assert b"".join([chunk async for chunk in chunks]) == payload


async def test_a_listing_shows_one_level_folders_first(minio_store: MinioObjectStore) -> None:
    for key in ("top.txt", "inbox/a.md", "inbox/sub/b.md"):
        await minio_store.put(key, b"x", content_type="text/plain")

    top = await minio_store.list_folder("", limit=1000)
    inbox = await minio_store.list_folder("inbox/", limit=1000)

    assert (top.folders, [f.key for f in top.files], top.truncated) == (("inbox/",), ["top.txt"], False)
    assert (inbox.folders, [f.key for f in inbox.files]) == (("inbox/sub/",), ["inbox/a.md"])


async def test_a_listing_beyond_the_limit_says_it_is_truncated(minio_store: MinioObjectStore) -> None:
    for index in range(3):
        await minio_store.put(f"many/{index}.txt", b"x", content_type="text/plain")

    listing = await minio_store.list_folder("many/", limit=2)

    assert (len(listing.files), listing.truncated) == (2, True)


async def test_a_deleted_file_cannot_be_opened(minio_store: MinioObjectStore) -> None:
    await minio_store.put("gone.txt", b"x", content_type="text/plain")

    await minio_store.delete("gone.txt")

    with pytest.raises(StoredFileNotFoundError):
        await minio_store.open("gone.txt")


async def test_the_probe_refuses_a_bucket_that_does_not_exist(minio_store: MinioObjectStore) -> None:
    other = MinioObjectStore(minio_store.client, "no-such-bucket-ea")
    with pytest.raises(RuntimeError, match="no-such-bucket-ea"):
        await other.probe()
```

Run: `make test-integration` (from the repo root; needs Docker) → FAIL (ImportError on `ea.repositories.object_store`).

- [ ] **Step 11: Implement** — `backend/src/ea/repositories/object_store.py`:

```python
"""The `ObjectStore` port over MinIO — see docs/adr/0036.

The `minio` SDK is synchronous, so every call runs in a worker thread: done on
the event loop, a 50 MB upload would stall every other request, `/mcp` included.
A download is streamed in chunks, each read in a thread too, so a large file is
never held whole by this process.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final

import urllib3
from minio import Minio
from minio.error import S3Error

from ea.core.config import Settings
from ea.domain.errors import StoredFileNotFoundError
from ea.domain.files import FileListing, StoredFile, guess_content_type

CHUNK_BYTES: Final = 64 * 1024
#: What MinIO calls a key that is not there, depending on the verb.
_MISSING: Final = frozenset({"NoSuchKey", "NoSuchObject"})
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def minio_client(settings: Settings) -> Minio:
    """A client for `s3_endpoint`, trusting `s3_ca_cert` when there is one.

    Same pool as `pipelines/storage.py`, with the CA swapped: a bare
    `PoolManager` would drop the SDK's timeouts and its retries on a 5xx.
    """
    http_client = None
    if settings.s3_ca_cert:
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=10, read=300),
            maxsize=10,
            cert_reqs="CERT_REQUIRED",
            ca_certs=settings.s3_ca_cert,
            retries=urllib3.Retry(total=5, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]),
        )
    return Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        secure=settings.s3_secure,
        http_client=http_client,
    )


class MinioObjectStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    async def probe(self) -> None:
        """Refuse to boot on a bucket that is not there, rather than 500 on the first upload."""
        if not await asyncio.to_thread(self.client.bucket_exists, self.bucket):
            msg = f"the bucket {self.bucket!r} does not exist on this MinIO"
            raise RuntimeError(msg)

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing:
        return await asyncio.to_thread(self._list_folder, prefix, limit)

    def _list_folder(self, prefix: str, limit: int) -> FileListing:
        folders: list[str] = []
        files: list[StoredFile] = []
        for found in self.client.list_objects(self.bucket, prefix=prefix or None, recursive=False):
            if len(folders) + len(files) == limit:
                return FileListing(prefix, tuple(folders), tuple(files), truncated=True)
            name = found.object_name or ""
            if found.is_dir:
                folders.append(name)
            else:
                files.append(
                    StoredFile(
                        key=name,
                        size=found.size or 0,
                        last_modified=found.last_modified or _EPOCH,
                        content_type=guess_content_type(name),
                    )
                )
        return FileListing(prefix, tuple(folders), tuple(files), truncated=False)

    async def stat(self, key: str) -> StoredFile | None:
        try:
            found = await asyncio.to_thread(self.client.stat_object, self.bucket, key)
        except S3Error as error:
            if error.code in _MISSING:
                return None
            raise
        return StoredFile(
            key=key,
            size=found.size or 0,
            last_modified=found.last_modified or _EPOCH,
            content_type=found.content_type or guess_content_type(key),
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile:
        await asyncio.to_thread(
            self.client.put_object, self.bucket, key, io.BytesIO(data), len(data), content_type=content_type
        )
        stored = await self.stat(key)
        if stored is None:  # pragma: no cover - deleted between the two calls
            msg = f"no file at {key!r}"
            raise StoredFileNotFoundError(msg)
        return stored

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        missing = f"no file at {key!r}"
        stored = await self.stat(key)
        if stored is None:
            raise StoredFileNotFoundError(missing)
        try:
            response = await asyncio.to_thread(self.client.get_object, self.bucket, key)
        except S3Error as error:
            # Deleted between `stat` and `get_object`.
            if error.code in _MISSING:
                raise StoredFileNotFoundError(missing) from None
            raise
        return stored, _chunks(response)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)


async def _chunks(response: urllib3.BaseHTTPResponse) -> AsyncIterator[bytes]:
    try:
        while chunk := await asyncio.to_thread(response.read, CHUNK_BYTES):
            yield chunk
    finally:
        response.close()
        response.release_conn()
```

If constructing `S3Error` by hand fights `mypy`, replace that branch with a direct `raise StoredFileNotFoundError(f"no file at {key!r}")` before the `try` — the behaviour is the same, and the `try` then only wraps `get_object`.

Run: `make test-integration` → the new tests PASS, the older ones still PASS.

- [ ] **Step 12: Gate**

Run: `cd backend && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src migrations && uv run bandit -c pyproject.toml -r src`
Then `make test` from the root. If coverage falls under 90% because `object_store.py` is only exercised by integration tests, add `backend/tests/unit/test_object_store_translation.py` driving `MinioObjectStore` with a small hand-written stub client (an object with `stat_object`, `list_objects`, `get_object`, `put_object`, `remove_object`, `bucket_exists`) covering: `NoSuchKey` → `stat` is `None`; another `S3Error` code re-raised; `is_dir` entries become folders; truncation at `limit`; missing bucket → `probe` raises.

---

### Task 3: `FileService` — who may do what to a file

**Files:**
- Create: `backend/src/ea/services/files.py`, `backend/tests/unit/test_file_service.py`
- Modify: `backend/tests/conftest.py`, `backend/tests/unit/test_service_guards.py`, `backend/tests/unit/test_write_audit.py`

**Interfaces:**
- Consumes: Task 1 in full.
- Produces: `ea.services.files.FileService(store: ObjectStore | None)` with
  - `async list_folder(prefix: str = "") -> FileListing` (reader)
  - `async open(key: str) -> tuple[StoredFile, AsyncIterator[bytes]]` (reader)
  - `async read_text(key: str) -> tuple[StoredFile, str]` (reader)
  - `async upload(key: str, raw: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE, overwrite: bool = False) -> StoredFile` (editor)
  - `async delete(key: str) -> None` (editor)
- Produces (tests): `tests.conftest.InMemoryObjectStore`, fixtures `files_store() -> InMemoryObjectStore`, `file_service(files_store) -> FileService`.

- [ ] **Step 1: The in-memory double** — add to `backend/tests/conftest.py`, beside `InMemoryDiagrams`, plus the two fixtures beside `diagrams`/`diagram_service`:

```python
class InMemoryObjectStore:
    """The `ObjectStore` port over a dict — folders computed from keys, like S3."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def _stored(self, key: str) -> StoredFile:
        data, content_type = self.objects[key]
        return StoredFile(key=key, size=len(data), last_modified=FIXED_NOW, content_type=content_type)

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing:
        folders: dict[str, None] = {}
        files: list[StoredFile] = []
        for key in sorted(self.objects):
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "/" in rest:
                folders[f"{prefix}{rest.split('/', 1)[0]}/"] = None
            else:
                files.append(self._stored(key))
        kept_folders = tuple(folders)[:limit]
        kept_files = tuple(files)[: limit - len(kept_folders)]
        truncated = len(folders) + len(files) > limit
        return FileListing(prefix, kept_folders, kept_files, truncated)

    async def stat(self, key: str) -> StoredFile | None:
        return self._stored(key) if key in self.objects else None

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile:
        self.objects[key] = (data, content_type)
        return self._stored(key)

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        if key not in self.objects:
            msg = f"no file at {key!r}"
            raise StoredFileNotFoundError(msg)
        data = self.objects[key][0]

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        return self._stored(key), chunks()

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def files_store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def file_service(files_store: InMemoryObjectStore) -> FileService:
    return FileService(files_store)
```

(imports: `AsyncIterator` from `collections.abc`, `StoredFileNotFoundError`, `FileListing`, `StoredFile` from `ea.domain.files`, `FileService` from `ea.services.files`.)

- [ ] **Step 2: Failing tests** — `backend/tests/unit/test_file_service.py`:

```python
"""The file use cases, over the in-memory store: the rules, not MinIO."""

from __future__ import annotations

import pytest

from ea.domain.errors import (
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    InvalidFileKeyError,
    NotAuthorisedError,
    StoredFileExistsError,
    StoredFileNotFoundError,
)
from ea.domain.files import MAX_FILE_BYTES, MAX_TEXT_READ_BYTES
from ea.services.caller import acting_as
from ea.services.files import FileService
from tests.conftest import InMemoryObjectStore, a_reader

pytestmark = pytest.mark.asyncio


class TestUploading:
    async def test_a_file_is_stored_under_its_key(self, file_service: FileService, files_store: InMemoryObjectStore) -> None:
        stored = await file_service.upload("inbox/a.md", b"# A", content_type="text/markdown")

        assert (stored.key, stored.size) == ("inbox/a.md", 3)
        assert files_store.objects["inbox/a.md"] == (b"# A", "text/markdown")

    async def test_a_second_upload_on_the_same_key_is_refused(self, file_service: FileService) -> None:
        await file_service.upload("a.md", b"one")
        with pytest.raises(StoredFileExistsError):
            await file_service.upload("a.md", b"two")

    async def test_overwrite_replaces_it(self, file_service: FileService, files_store: InMemoryObjectStore) -> None:
        await file_service.upload("a.md", b"one")
        await file_service.upload("a.md", b"two", overwrite=True)
        assert files_store.objects["a.md"][0] == b"two"

    async def test_a_file_over_the_limit_is_refused(self, file_service: FileService) -> None:
        with pytest.raises(FileTooLargeError):
            await file_service.upload("big.bin", b"\0" * (MAX_FILE_BYTES + 1))

    async def test_an_ambiguous_path_is_refused(self, file_service: FileService) -> None:
        with pytest.raises(InvalidFileKeyError):
            await file_service.upload("inbox/../a.md", b"x")

    async def test_a_reader_cannot_upload(self, file_service: FileService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.upload("a.md", b"x")


class TestReading:
    async def test_a_reader_lists_a_folder(self, file_service: FileService) -> None:
        await file_service.upload("inbox/a.md", b"x")
        with acting_as(a_reader()):
            listing = await file_service.list_folder("inbox")
        assert [f.key for f in listing.files] == ["inbox/a.md"]
        assert listing.prefix == "inbox/"

    async def test_text_is_read_as_text(self, file_service: FileService) -> None:
        await file_service.upload("a.md", "é".encode())
        _, text = await file_service.read_text("a.md")
        assert text == "é"

    async def test_bytes_that_are_not_utf8_are_not_text(self, file_service: FileService) -> None:
        await file_service.upload("a.pdf", b"%PDF\xff\xfe")
        with pytest.raises(FileNotTextError):
            await file_service.read_text("a.pdf")

    async def test_a_text_too_long_to_be_an_answer_is_refused(self, file_service: FileService) -> None:
        await file_service.upload("long.txt", b"a" * (MAX_TEXT_READ_BYTES + 1))
        with pytest.raises(FileTooLargeError):
            await file_service.read_text("long.txt")

    async def test_reading_a_missing_file_is_not_found(self, file_service: FileService) -> None:
        with pytest.raises(StoredFileNotFoundError):
            await file_service.read_text("nothing.md")


class TestDeleting:
    async def test_a_deleted_file_is_gone(self, file_service: FileService, files_store: InMemoryObjectStore) -> None:
        await file_service.upload("a.md", b"x")
        await file_service.delete("a.md")
        assert files_store.objects == {}

    async def test_deleting_what_is_not_there_says_so(self, file_service: FileService) -> None:
        """S3 answers a delete of nothing with success; a person deserves better."""
        with pytest.raises(StoredFileNotFoundError):
            await file_service.delete("nothing.md")

    async def test_a_reader_cannot_delete(self, file_service: FileService) -> None:
        await file_service.upload("a.md", b"x")
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.delete("a.md")


async def test_without_a_store_every_use_case_says_storage_is_off() -> None:
    service = FileService(None)
    with pytest.raises(FileStorageUnavailableError):
        await service.list_folder()
```

Run: `cd backend && uv run pytest tests/unit/test_file_service.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement** — `backend/src/ea/services/files.py`:

```python
"""Use cases over the files kept in MinIO — see docs/adr/0036.

The HTTP upload and the MCP tool both land here, so the two rules no key can
check alone are stated once: a file already at a path is not replaced unless
that was asked for, and a file read as text must be text.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from ea.domain.errors import (
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    StoredFileExistsError,
    StoredFileNotFoundError,
)
from ea.domain.files import (
    DEFAULT_CONTENT_TYPE,
    MAX_FILE_BYTES,
    MAX_LISTED_ENTRIES,
    MAX_TEXT_READ_BYTES,
    FileListing,
    StoredFile,
    clean_key,
    clean_prefix,
)
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import ObjectStore

logger = logging.getLogger(__name__)


class FileService:
    """The single entry point `api/` and `mcp/` use for the bucket.

    `store` is `None` on a deployment with `EA_S3_ENABLED` off: the service
    still exists, so every route and tool stays declared and answers 503.
    """

    def __init__(self, store: ObjectStore | None) -> None:
        self._configured = store

    def _store(self) -> ObjectStore:
        if self._configured is None:
            msg = "file storage is not configured on this deployment (EA_S3_ENABLED is off)"
            raise FileStorageUnavailableError(msg)
        return self._configured

    async def list_folder(self, prefix: str = "") -> FileListing:
        require_caller()
        return await self._store().list_folder(clean_prefix(prefix), limit=MAX_LISTED_ENTRIES)

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        require_caller()
        return await self._store().open(clean_key(key))

    async def read_text(self, key: str) -> tuple[StoredFile, str]:
        """The file as UTF-8 text — for an agent, which has no use for a PDF's bytes."""
        require_caller()
        store = self._store()
        path = clean_key(key)
        found = await store.stat(path)
        if found is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        if found.size > MAX_TEXT_READ_BYTES:
            msg = f"{path!r} is {found.size} bytes; at most {MAX_TEXT_READ_BYTES} are read as text"
            raise FileTooLargeError(msg)
        stored, chunks = await store.open(path)
        raw = b"".join([chunk async for chunk in chunks])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            msg = f"{path!r} is not UTF-8 text; a person can download it from the web interface"
            raise FileNotTextError(msg) from None
        return stored, text

    async def upload(
        self,
        key: str,
        raw: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
        overwrite: bool = False,
    ) -> StoredFile:
        require_editor()
        store = self._store()
        path = clean_key(key)
        if len(raw) > MAX_FILE_BYTES:
            msg = f"a file is at most {MAX_FILE_BYTES // (1024 * 1024)} MB"
            raise FileTooLargeError(msg)
        # ponytail: stat-then-put — two editors writing one path in the same
        # instant both succeed, the last one wins. minio's put_object exposes no
        # If-None-Match; switch to a conditional write if that race ever matters.
        if not overwrite and await store.stat(path) is not None:
            msg = f"a file already exists at {path!r}; upload it with overwrite to replace it"
            raise StoredFileExistsError(msg)
        stored = await store.put(path, raw, content_type=content_type)
        logger.info(
            "file %r stored (%d bytes)",
            stored.key,
            stored.size,
            extra={"action": "file_stored", "key": stored.key},
        )
        return stored

    async def delete(self, key: str) -> None:
        require_editor()
        store = self._store()
        path = clean_key(key)
        if await store.stat(path) is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        await store.delete(path)
        logger.info("file %r deleted", path, extra={"action": "file_deleted", "key": path})
```

Run → PASS.

- [ ] **Step 4: The guard and the audit** — in `backend/tests/unit/test_service_guards.py` add `from ea.services.files import FileService` and `FileService: {"upload", "delete"},` to `WRITES`. In `backend/tests/unit/test_write_audit.py` add, in the style of its classes:

```python
class TestTheFiles:
    async def test_a_stored_file_is_named_by_its_key(
        self, file_service: FileService, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await file_service.upload("inbox/a.md", b"x")

        assert only(caplog, "file_stored").key == "inbox/a.md"  # type: ignore[attr-defined]

    async def test_a_deleted_file_is_named_by_its_key(
        self, file_service: FileService, caplog: pytest.LogCaptureFixture
    ) -> None:
        await file_service.upload("inbox/a.md", b"x")
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await file_service.delete("inbox/a.md")

        assert only(caplog, "file_deleted").key == "inbox/a.md"  # type: ignore[attr-defined]
```

Run: `uv run pytest tests/unit/test_service_guards.py tests/unit/test_write_audit.py tests/unit/test_file_service.py tests/unit/test_logging.py -q` → PASS. (If `key` collides with a `LogRecord` attribute at emit time, `test_logging.py` or these tests fail loudly — rename the field to `file_key` everywhere in this task.)

- [ ] **Step 5: Gate** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src migrations`.

---

### Task 4: The HTTP adapter and the wiring

**Files:**
- Create: `backend/src/ea/api/files.py`, `backend/tests/e2e/test_file_api.py`
- Modify: `backend/src/ea/api/schemas.py`, `backend/src/ea/api/dependencies.py`, `backend/src/ea/main.py`, `backend/openapi.json` + `frontend/src/api/schema.d.ts` (regenerated)

**Interfaces:**
- Consumes: Tasks 1–3 (`FileService`, `MinioObjectStore`, `minio_client`, `Settings.s3_*`, `InMemoryObjectStore`).
- Produces: `ea.api.schemas` → `FileKey`, `FilePrefix` (Annotated bounds), `FileRead.of(StoredFile)`, `FileListingRead.of(FileListing)`.
- Produces: `ea.api.dependencies` → `file_service_of(app) -> FileService`, `Files` alias.
- Produces: `create_app(..., files: ObjectStore | None = None)`; `main.build_object_store(settings) -> MinioObjectStore`; `app.state.file_service` always set after lifespan start.
- Produces HTTP: `GET /files?prefix=` → `FileListingRead`; `POST /files` multipart (`file`, `prefix`, `overwrite`) → 201 `FileRead`; `GET /files/content?key=` → bytes; `DELETE /files?key=` → 204.

- [ ] **Step 1: Failing API tests** — `backend/tests/e2e/test_file_api.py`:

```python
"""The file endpoints end to end, over the in-memory store."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.files import MAX_FILE_BYTES
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.files import FileService
from tests.conftest import InMemoryObjectStore, StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(service: ArchitectureService, files_store: InMemoryObjectStore) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service, files=files_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def upload(client: httpx.AsyncClient, name: str = "notes.md", raw: bytes = b"# Notes", **form: str) -> httpx.Response:
    return await client.post("/files", files={"file": (name, raw, "text/markdown")}, data=form)


class TestUploading:
    async def test_a_file_lands_in_the_folder_it_was_sent_to(self, client: httpx.AsyncClient) -> None:
        response = await upload(client, prefix="inbox")

        assert response.status_code == 201, response.text
        assert response.json() | {"last_modified": None} == {
            "key": "inbox/notes.md",
            "name": "notes.md",
            "size": 7,
            "content_type": "text/markdown",
            "last_modified": None,
        }

    async def test_the_same_name_twice_is_a_conflict(self, client: httpx.AsyncClient) -> None:
        await upload(client)
        response = await upload(client)
        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_overwrite_replaces_it(self, client: httpx.AsyncClient) -> None:
        await upload(client)
        assert (await upload(client, raw=b"v2", overwrite="true")).status_code == 201

    async def test_a_folder_that_escapes_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await upload(client, prefix="../etc")
        assert (response.status_code, response.json()["error"]) == (422, "invalid_file_path")

    async def test_a_file_over_the_limit_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await upload(client, name="big.bin", raw=b"\0" * (MAX_FILE_BYTES + 1))
        assert (response.status_code, response.json()["error"]) == (422, "file_too_large")


class TestReading:
    async def test_a_folder_lists_its_folders_and_its_files(self, client: httpx.AsyncClient) -> None:
        await upload(client, prefix="inbox")
        await upload(client, prefix="inbox/sub")

        body = (await client.get("/files", params={"prefix": "inbox/"})).json()

        assert body["folders"] == ["inbox/sub/"]
        assert [f["key"] for f in body["files"]] == ["inbox/notes.md"]
        assert body["truncated"] is False

    async def test_a_download_is_always_an_attachment_never_rendered(self, client: httpx.AsyncClient) -> None:
        await client.post("/files", files={"file": ("page.html", b"<script>1</script>", "text/html")})

        response = await client.get("/files/content", params={"key": "page.html"})

        assert response.status_code == 200
        assert response.content == b"<script>1</script>"
        assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''page.html"
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_a_name_with_spaces_and_accents_is_quoted(self, client: httpx.AsyncClient) -> None:
        await upload(client, name="rapport été.md")
        response = await client.get("/files/content", params={"key": "rapport été.md"})
        assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''rapport%20%C3%A9t%C3%A9.md"

    async def test_downloading_a_missing_file_is_not_found(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/files/content", params={"key": "nothing.md"})).status_code == 404


class TestDeleting:
    async def test_a_deleted_file_is_gone(self, client: httpx.AsyncClient) -> None:
        await upload(client)
        assert (await client.delete("/files", params={"key": "notes.md"})).status_code == 204
        assert (await client.delete("/files", params={"key": "notes.md"})).status_code == 404


class TestWhoMay:
    @pytest_asyncio.fixture
    async def guarded(self, service: ArchitectureService, files_store: InMemoryObjectStore) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(
            Settings(debug=True),
            architecture_service=service,
            files=files_store,
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client

    async def test_nobody_lists_nothing(self, guarded: httpx.AsyncClient) -> None:
        assert (await guarded.get("/files")).status_code == 401

    async def test_a_reader_lists_but_does_not_upload(self, guarded: httpx.AsyncClient) -> None:
        reader = {"Authorization": "Bearer reader"}
        assert (await guarded.get("/files", headers=reader)).status_code == 200
        response = await guarded.post("/files", headers=reader, files={"file": ("a.md", b"x", "text/markdown")})
        assert response.status_code == 403


async def test_without_storage_the_routes_answer_503(service: ArchitectureService) -> None:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    app.state.file_service = FileService(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/files")
    assert (response.status_code, response.json()["error"]) == (503, "storage_unavailable")
```

Check `tests/e2e/test_auth_api.py` for how an authenticated API test is assembled; if it differs from `TestWhoMay` (e.g. `auth_enabled` or the tokens), follow it.

Run: `cd backend && uv run pytest tests/e2e/test_file_api.py -q` → FAIL.

- [ ] **Step 2: Schemas** — `backend/src/ea/api/schemas.py`: add bounds beside the others (lines ~47–90) and the two read models near `DocumentRead`:

```python
# --- Files in MinIO (docs/adr/0036) ---
FileKey = Annotated[str, Field(min_length=1, max_length=MAX_KEY_BYTES)]
FilePrefix = Annotated[str, Field(max_length=MAX_KEY_BYTES)]
```

```python
class FileRead(BaseModel):
    """One file of the bucket, as a listing or an upload describes it — never its bytes."""

    key: str = Field(description="The full path in the bucket, folders included.")
    name: str = Field(description="The last segment of the key.")
    size: int = Field(description="Size of the stored file, in bytes.")
    last_modified: datetime
    content_type: str

    @classmethod
    def of(cls, stored: StoredFile) -> FileRead:
        return cls(
            key=stored.key,
            name=stored.name,
            size=stored.size,
            last_modified=stored.last_modified,
            content_type=stored.content_type,
        )


class FileListingRead(BaseModel):
    """One folder: its sub-folders, then its files."""

    prefix: str
    folders: list[str] = Field(description="Each sub-folder as a full prefix ending in `/`.")
    files: list[FileRead]
    truncated: bool = Field(
        description=f"True when only the first {MAX_LISTED_ENTRIES} entries are listed."
    )

    @classmethod
    def of(cls, listing: FileListing) -> FileListingRead:
        return cls(
            prefix=listing.prefix,
            folders=list(listing.folders),
            files=[FileRead.of(stored) for stored in listing.files],
            truncated=listing.truncated,
        )
```

(imports from `ea.domain.files`: `MAX_KEY_BYTES`, `MAX_LISTED_ENTRIES`, `FileListing`, `StoredFile`.)

- [ ] **Step 3: Dependency** — `backend/src/ea/api/dependencies.py`, after the IPAM block:

```python
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
```

- [ ] **Step 4: The router** — `backend/src/ea/api/files.py`:

```python
"""The endpoints over the file bucket — see docs/adr/0036.

Keys hold `/`, so they travel in the query string rather than the path. An
upload is read under a cap, one byte past the limit, like `api/documents.py`.

A download is always an attachment, with `nosniff` and a sandboxing CSP: the
bucket takes any file, and an HTML or SVG file served inline on this origin
would run with the session of whoever opened it.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ea.api.dependencies import Files
from ea.api.schemas import ErrorResponse, FileKey, FileListingRead, FilePrefix, FileRead
from ea.domain.files import MAX_FILE_BYTES, guess_content_type, join_key

router = APIRouter(tags=["files"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **NOT_FOUND,
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}

Upload = Annotated[UploadFile, File(description="Any file, at most 50 MB.")]
KeyQuery = Annotated[FileKey, Query()]
PrefixQuery = Annotated[FilePrefix, Query()]

_READ_LIMIT = MAX_FILE_BYTES + 1


def attachment(name: str) -> str:
    """RFC 6266 with RFC 5987 encoding: any name, no header injection."""
    return f"attachment; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/files", response_model=FileListingRead, responses=NOT_FOUND)
async def list_files(files: Files, prefix: PrefixQuery = "") -> FileListingRead:
    """One folder of the bucket: its sub-folders, then its files."""
    return FileListingRead.of(await files.list_folder(prefix))


@router.post(
    "/files", response_model=FileRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def upload_file(
    files: Files,
    file: Upload,
    prefix: Annotated[FilePrefix, Form()] = "",
    overwrite: Annotated[bool, Form()] = False,
) -> FileRead:
    """Store a file in the folder `prefix`, under its own name.

    A file already there is a 409 unless `overwrite` is true.
    """
    key = join_key(prefix, file.filename or "")
    raw = await file.read(_READ_LIMIT)
    stored = await files.upload(
        key,
        raw,
        content_type=file.content_type or guess_content_type(key),
        overwrite=overwrite,
    )
    return FileRead.of(stored)


@router.get(
    "/files/content",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            "description": "The file's bytes, as an attachment.",
        },
        **NOT_FOUND,
    },
)
async def download_file(files: Files, key: KeyQuery) -> StreamingResponse:
    """The bytes of one file."""
    stored, chunks = await files.open(key)
    return StreamingResponse(
        chunks,
        media_type=stored.content_type,
        headers={
            "Content-Disposition": attachment(stored.name),
            "Content-Length": str(stored.size),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.delete("/files", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
async def delete_file(files: Files, key: KeyQuery) -> Response:
    await files.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 5: Wiring** — `backend/src/ea/main.py`:
  1. Import `FileService`, `MinioObjectStore`, `minio_client`, `ObjectStore` (from `ea.domain.ports`), and `router as files_router` from `ea.api.files` in the style of the other routers.
  2. Beside `build_verifier`:

```python
def build_object_store(settings: Settings) -> MinioObjectStore:
    """The bucket of `s3_bucket` on `s3_endpoint` — see docs/adr/0036."""
    return MinioObjectStore(minio_client(settings), settings.s3_bucket)
```

  3. `create_app(..., files: ObjectStore | None = None, ...)`; document it in the docstring in one sentence like `diagrams`; after the `architecture_service` block: `if files is not None: app.state.file_service = FileService(files)`.
  4. In `lifespan`, after the `diagram_store` block and before the MCP sessions:

```python
            # Files live in MinIO, beside every other store (docs/adr/0036). The
            # service is attached either way, so a deployment without it answers
            # 503 on every file route and tool instead of 500.
            if getattr(app.state, "file_service", None) is None:
                store: ObjectStore | None = None
                if settings.s3_enabled:
                    minio = build_object_store(settings)
                    await minio.probe()
                    store = minio
                    logger.info("file storage ready: bucket %s at %s", settings.s3_bucket, settings.s3_endpoint)
                app.state.file_service = FileService(store)
```

  5. Add `"s3": settings.s3_enabled` to the `extra` of the `starting %s` log line.
  6. `app.include_router(files_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)` after `ipam_router`.

Run: `uv run pytest tests/e2e/test_file_api.py -q` → PASS.

- [ ] **Step 6: Regenerate the contract**

Run from the repo root: `make openapi && make openapi-check`. Then `cd frontend && npm run typecheck`.
Expected: `backend/openapi.json` and `frontend/src/api/schema.d.ts` gain `/files` and `/files/content`; check passes.

- [ ] **Step 7: Gate** — from the root: `make lint-check typecheck-be test`. All green, coverage ≥ 90%.

---

### Task 5: The MCP tools

**Files:**
- Modify: `backend/src/ea/mcp/server.py`, `backend/src/ea/main.py` (`_mount_mcp` only), `backend/tests/unit/test_mcp_server.py`, and every other test that calls `build_mcp_server(` (`grep -rn "build_mcp_server(" backend/tests`)

**Interfaces:**
- Consumes: `FileService` (Task 3), `FileKey`/`FilePrefix`/`FileRead`/`FileListingRead` (Task 4), `file_service_of` (Task 4), `file_service` fixture (Task 3), `MAX_FILE_BYTES`/`DEFAULT_CONTENT_TYPE`/`guess_content_type` (Task 1).
- Produces: `build_mcp_server(get_service, get_documents, get_ipam, get_files, *, ...)`; `FileProvider = Callable[[], FileService]`; tools `list_files`, `read_file`, `upload_file`, `delete_file`.

- [ ] **Step 1: Failing tests** — in `backend/tests/unit/test_mcp_server.py`:
  - give both `build_mcp_server(` fixtures a fourth provider `lambda: file_service` (add `file_service: FileService` to the fixture's parameters); do the same in every other test file that builds the server;
  - add `"list_files", "read_file", "upload_file", "delete_file"` to the whole-list assertion;
  - in `TestTheSameBoundsAsTheHttpAdapter`, add cases pairing `FilePath` with `FileKey` and `Folder` with `FilePrefix`, the way the existing pairs are written;
  - add a class using **the same tool-calling helper the document tool tests in this file use**:

```python
class TestTheFileTools:
    async def test_text_goes_in_as_text_and_comes_back(self, server, files_store) -> None:
        await call(server, "upload_file", {"key": "inbox/notes.md", "content": "# Notes"})

        assert files_store.objects["inbox/notes.md"] == (b"# Notes", "text/markdown")
        assert await call(server, "read_file", {"key": "inbox/notes.md"}) == "# Notes"

    async def test_a_binary_goes_in_as_base64(self, server, files_store) -> None:
        await call(server, "upload_file", {"key": "a.png", "content": "iVBORw0K", "encoding": "base64"})

        assert files_store.objects["a.png"] == (b"\x89PNG\r\n", "image/png")

    async def test_text_under_an_unknown_extension_is_plain_text(self, server, files_store) -> None:
        await call(server, "upload_file", {"key": "notes.zzz-unknown", "content": "x"})
        assert files_store.objects["notes.zzz-unknown"][1] == "text/plain; charset=utf-8"

    async def test_broken_base64_is_explained_not_crashed(self, server) -> None:
        with pytest.raises(ToolError, match="base64"):
            await call(server, "upload_file", {"key": "a.png", "content": "not base64!", "encoding": "base64"})

    async def test_an_existing_file_is_not_replaced_unless_asked(self, server) -> None:
        await call(server, "upload_file", {"key": "a.md", "content": "one"})
        with pytest.raises(ToolError, match="overwrite"):
            await call(server, "upload_file", {"key": "a.md", "content": "two"})
        await call(server, "upload_file", {"key": "a.md", "content": "two", "overwrite": True})

    async def test_a_binary_is_not_read_as_text(self, server) -> None:
        await call(server, "upload_file", {"key": "a.bin", "content": "//79", "encoding": "base64"})
        with pytest.raises(ToolError, match="UTF-8"):
            await call(server, "read_file", {"key": "a.bin"})

    async def test_a_folder_is_listed_one_level_deep(self, server) -> None:
        await call(server, "upload_file", {"key": "inbox/sub/b.md", "content": "b"})
        listing = await call(server, "list_files", {"prefix": "inbox/"})
        assert listing["folders"] == ["inbox/sub/"]

    async def test_delete_removes_and_is_announced_as_destructive(self, server, files_store) -> None:
        await call(server, "upload_file", {"key": "a.md", "content": "x"})
        await call(server, "delete_file", {"key": "a.md"})
        assert files_store.objects == {}
        assert tool(server, "delete_file").annotations.destructive_hint is True

    async def test_a_reader_cannot_upload(self, server) -> None:
        with acting_as(a_reader()), pytest.raises(ToolError):
            await call(server, "upload_file", {"key": "a.md", "content": "x"})
```

Adapt `call`, `tool`, the fixture names and how a structured answer is read (`listing["folders"]` vs an attribute) to what that file already does; keep every assertion.

Run: `uv run pytest tests/unit/test_mcp_server.py -q` → FAIL.

- [ ] **Step 2: Implement** — `backend/src/ea/mcp/server.py`:
  1. Imports: `import base64`, `import binascii`, `Literal` from `typing`; `FileKey`, `FileListingRead`, `FilePrefix`, `FileRead` from `ea.api.schemas`; `DEFAULT_CONTENT_TYPE`, `MAX_FILE_BYTES`, `guess_content_type` from `ea.domain.files`; `FileService` from `ea.services.files`.
  2. Module docstring: say the adapter now holds four service providers, the fourth being the files of docs/adr/0036.
  3. Append to `INSTRUCTIONS` (keep the trailing `\` before the closing quotes on the last line):

```text
The server also keeps files of any kind — PDFs, spreadsheets, notes — in one
bucket organised in folders. `list_files` browses a folder, `read_file` reads a
text file, `upload_file` stores one (base64 for anything that is not text) and
`delete_file` removes one. A file put under `inbox/` is read by the pipeline
that turns source documents into ArchiMate elements, so a source to be
modelled goes there.
```

  4. `FileProvider = Callable[[], FileService]` beside `IpamProvider`.
  5. Argument types, after `ReservedAddresses`:

```python
FilePath = Annotated[
    FileKey,
    Field(description="The full path of a file in the bucket, folders included, e.g. `inbox/crm.md`."),
]
Folder = Annotated[
    FilePrefix, Field(description="A folder of the bucket, e.g. `inbox/`. Empty for the top.")
]
#: Base64 carries three bytes in four characters; the domain still decides on
#: the decoded size, this only stops a runaway argument.
FileContent = Annotated[
    str,
    Field(
        max_length=MAX_FILE_BYTES * 4 // 3 + 4,
        description="The file itself: plain text, or base64 when `encoding` is `base64`.",
    ),
]
```

  6. `build_mcp_server(get_service, get_documents, get_ipam, get_files: FileProvider, *, ...)`; one docstring sentence: `get_files` is required for the same reason as `get_documents`.
  7. After the IP addressing tools:

```python
    # --- Files (docs/adr/0036) ----------------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_files(prefix: Folder = "") -> FileListingRead:
        """List one folder of the file bucket: its sub-folders, then its files.

        A folder is not a stored thing — it is the shared start of some paths —
        so pass a sub-folder back as `prefix` to go one level down. `truncated`
        means only the first 1000 entries came back.
        """
        return FileListingRead.of(await get_files().list_folder(prefix))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def read_file(key: FilePath) -> str:
        """The content of one text file of the bucket.

        Text only — UTF-8, at most 1 MB. A PDF or an image is refused: its bytes
        mean nothing here, and a person downloads it from the web interface.
        """
        _, text = await get_files().read_text(key)
        return text

    @server.tool(annotations=EDITS)
    @speaking_plainly
    async def upload_file(
        key: FilePath,
        content: FileContent,
        encoding: Literal["text", "base64"] = "text",
        overwrite: bool = False,
    ) -> FileRead:
        """Store a file in the bucket at `key`.

        Text goes as it is (`encoding="text"`). Anything else — a PDF, an image,
        a spreadsheet — goes base64-encoded with `encoding="base64"`. Either way
        the file is at most 50 MB once decoded.

        A file already at `key` is refused unless `overwrite` is true, and then
        its previous content is gone. A file under `inbox/` is read by the
        catalogue pipeline the next time it runs.
        """
        if encoding == "base64":
            try:
                raw = base64.b64decode(content, validate=True)
            except binascii.Error:
                msg = "`content` is not valid base64; send text with encoding='text'"
                raise ValueError(msg) from None
            fallback = DEFAULT_CONTENT_TYPE
        else:
            raw = content.encode()
            fallback = "text/plain; charset=utf-8"
        guessed = guess_content_type(key)
        return FileRead.of(
            await get_files().upload(
                key,
                raw,
                content_type=fallback if guessed == DEFAULT_CONTENT_TYPE else guessed,
                overwrite=overwrite,
            )
        )

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def delete_file(key: FilePath) -> str:
        """Delete one file from the bucket.

        There is no undo and no version history: the file is gone. Confirm with
        the person you are working for before calling it.
        """
        await get_files().delete(key)
        return f"file {key} was deleted"
```

  8. `backend/src/ea/main.py`, `_mount_mcp`: add `lambda: file_service_of(app),` after the IPAM provider; import `file_service_of`. Update its docstring's "All three services" to "All four services".

Note `"iVBORw0K"` decodes to `b"\x89PNG\r\n"` and `"//79"` to `b"\xff\xfe\xfd"` — the tests rely on those two facts.

Run: `uv run pytest tests/unit -q` → PASS.

- [ ] **Step 3: Gate** — from the root: `make lint-check typecheck-be test`. `/mcp` is not in OpenAPI, so no regeneration.

---

### Task 6: The SPA section

**Files:**
- Modify: `frontend/src/router/sections.ts`, `frontend/tests/support/api.ts`, plus `frontend/tests/AppNav.spec.ts` / `frontend/tests/router.spec.ts` only if they enumerate sections or groups
- Create: `frontend/src/features/files/useFiles.ts`, `frontend/src/features/files/FilesSection.vue`, `frontend/tests/useFiles.spec.ts`, `frontend/tests/FilesSection.spec.ts`

**Interfaces:**
- Consumes: the regenerated `components['schemas']['FileRead' | 'FileListingRead']` and paths `/files`, `/files/content` (Task 4); `api`, `unwrap`, `messageOf`, `ApiError` (`lib/api.ts`); `useLatestRequest` (`lib/latest.ts`); `useMe().canWrite` (`lib/me.ts`).
- Produces: `useFiles()` → `{ listing, status, error, load(prefix), upload(prefix, file, overwrite?) → 'stored' | 'exists', download(key) → Blob, remove(key) }`; pure `uploadForm`, `parentOf`, `breadcrumbs`, `saveAs(blob, name)`.

(`FilesSection.vue`, not the spec's `FilesScreen.vue`: every screen here is `*Section.vue`.)

- [ ] **Step 1: Test helpers** — `frontend/tests/support/api.ts`, beside the others:

```ts
type FileRead = components['schemas']['FileRead']
type FileListingRead = components['schemas']['FileListingRead']

export function aStoredFile(overrides: Partial<FileRead> = {}): FileRead {
  return {
    key: 'inbox/notes.md',
    name: 'notes.md',
    size: 2048,
    last_modified: '2026-09-15T12:00:00Z',
    content_type: 'text/markdown',
    ...overrides,
  }
}

export function aListing(overrides: Partial<FileListingRead> = {}): FileListingRead {
  return { prefix: '', folders: [], files: [], truncated: false, ...overrides }
}
```

- [ ] **Step 2: Failing composable spec** — `frontend/tests/useFiles.spec.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { breadcrumbs, parentOf, uploadForm, useFiles } from '../src/features/files/useFiles'
import { MULTIPART, aFile, aListing, aStoredFile, stubApi } from './support/api'

afterEach(() => vi.unstubAllGlobals())

describe('the pure helpers', () => {
  it('sends the file, its folder and the overwrite flag under the names the backend reads', () => {
    const file = aFile('notes.md')
    const form = uploadForm(file, 'inbox/', true)

    expect(form.get('file')).toBe(file)
    expect(form.get('prefix')).toBe('inbox/')
    expect(form.get('overwrite')).toBe('true')
  })

  it('finds the folder above', () => {
    expect(parentOf('a/b/')).toBe('a/')
    expect(parentOf('a/')).toBe('')
    expect(parentOf('')).toBe('')
  })

  it('cuts a folder into the crumbs that lead to it', () => {
    expect(breadcrumbs('a/b/')).toEqual([
      { label: 'a', prefix: 'a/' },
      { label: 'b', prefix: 'a/b/' },
    ])
    expect(breadcrumbs('')).toEqual([])
  })
})

describe('useFiles', () => {
  it('lists the folder it is asked for', async () => {
    const calls = stubApi([{ path: '/files', body: aListing({ prefix: 'inbox/', files: [aStoredFile()] }) }])
    const files = useFiles()

    await files.load('inbox/')

    expect(calls[0]?.url.searchParams.get('prefix')).toBe('inbox/')
    expect(files.listing.value?.files.map((file) => file.name)).toEqual(['notes.md'])
  })

  it('uploads as multipart and says it was stored', async () => {
    const calls = stubApi([{ method: 'POST', path: '/files', status: 201, body: aStoredFile() }])

    expect(await useFiles().upload('inbox/', aFile('notes.md'))).toBe('stored')
    expect(calls[0]?.body).toBe(MULTIPART)
  })

  it('answers "exists" on a 409 instead of throwing, so the screen can ask', async () => {
    stubApi([{ method: 'POST', path: '/files', status: 409, body: { error: 'duplicate', detail: 'exists' } }])

    expect(await useFiles().upload('', aFile('notes.md'))).toBe('exists')
  })

  it('still throws any other refusal', async () => {
    stubApi([{ method: 'POST', path: '/files', status: 422, body: { error: 'file_too_large', detail: 'trop gros' } }])

    await expect(useFiles().upload('', aFile('big.bin'))).rejects.toThrow('trop gros')
  })

  it('deletes by key', async () => {
    const calls = stubApi([{ method: 'DELETE', path: '/files', status: 204 }])

    await useFiles().remove('inbox/notes.md')

    expect(calls[0]?.url.searchParams.get('key')).toBe('inbox/notes.md')
  })
})
```

Run: `cd frontend && npm test -- --run tests/useFiles.spec.ts` → FAIL.

- [ ] **Step 3: Implement** — `frontend/src/features/files/useFiles.ts`:

```ts
// The files of the bucket (docs/adr/0036): one folder at a time, and the writes.
//
// A folder is not stored anywhere — it is the shared start of some keys — so
// the screen navigates by prefix and the API computes what is below it. A
// download goes through the client rather than a link: the token lives in
// memory, and a plain `<a href>` would reach the API without it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type StoredFile = components['schemas']['FileRead']
export type FileListing = components['schemas']['FileListingRead']

/** The multipart body of `POST /files` — the one hand-written payload here. */
export function uploadForm(file: File, prefix: string, overwrite: boolean): FormData {
  const form = new FormData()
  form.append('file', file)
  form.append('prefix', prefix)
  form.append('overwrite', String(overwrite))
  return form
}

/** The folder above `prefix`: `a/b/` → `a/`, `a/` → the top. */
export function parentOf(prefix: string): string {
  const trimmed = prefix.replace(/\/$/, '')
  const cut = trimmed.lastIndexOf('/')
  return cut < 0 ? '' : trimmed.slice(0, cut + 1)
}

/** `a/b/` → the crumbs `a` and `b`, each with the prefix it opens. */
export function breadcrumbs(prefix: string): { label: string; prefix: string }[] {
  const parts = prefix.split('/').filter(Boolean)
  return parts.map((label, index) => ({ label, prefix: `${parts.slice(0, index + 1).join('/')}/` }))
}

/** Hand a downloaded blob to the browser under its own name. */
export function saveAs(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

export function useFiles() {
  const listing = ref<FileListing | null>(null)
  const request = useLatestRequest()
  const { status, error } = request

  async function load(prefix: string): Promise<void> {
    await request.run(
      async (signal) => unwrap(await api.GET('/files', { params: { query: { prefix } }, signal })),
      (answer) => {
        listing.value = answer
      },
    )
  }

  /** `'exists'` rather than a throw on a 409: replacing a file is a question for the user. */
  async function upload(prefix: string, file: File, overwrite = false): Promise<'stored' | 'exists'> {
    try {
      unwrap(
        await api.POST('/files', {
          // The generated type says `string` for a binary part; the value sent is
          // the `File`, serialised by `uploadForm` — see `useElementDocuments`.
          body: { file: file as unknown as string, prefix, overwrite },
          bodySerializer: () => uploadForm(file, prefix, overwrite),
        }),
      )
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        return 'exists'
      }
      throw failure
    }
    return 'stored'
  }

  async function download(key: string): Promise<Blob> {
    return unwrap(await api.GET('/files/content', { params: { query: { key } }, parseAs: 'blob' }))
  }

  async function remove(key: string): Promise<void> {
    unwrap(await api.DELETE('/files', { params: { query: { key } } }))
  }

  return { listing, status, error, load, upload, download, remove }
}
```

If `unwrap`'s return type does not narrow to `Blob` with `parseAs: 'blob'`, cast at that one call (`as Blob`) with a one-line comment. Run → PASS.

- [ ] **Step 4: Register the section** — `frontend/src/router/sections.ts`: add `{ id: 'storage', label: 'Stockage' }` to the end of `GROUPS`, and to the end of `SECTIONS`:

```ts
  {
    path: '/fichiers',
    name: 'files',
    label: 'Fichiers',
    summary: 'Déposer, parcourir, télécharger et supprimer des fichiers dans MinIO.',
    group: 'storage',
    view: () => import('../features/files/FilesSection.vue'),
  },
```

Run `npm test -- --run tests/AppNav.spec.ts tests/router.spec.ts`; update any assertion that enumerates groups or sections.

- [ ] **Step 5: Failing screen spec** — `frontend/tests/FilesSection.spec.ts`. Mount it like `tests/IpamSection.spec.ts` does (router from `createAppRouter(createMemoryHistory())`, and `useMe` mocked the same way that spec mocks it for a reader/editor):

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import FilesSection from '../src/features/files/FilesSection.vue'
import { createAppRouter } from '../src/router'
import { aFile, aListing, aStoredFile, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const TOP: Route = { path: '/files', body: aListing({ folders: ['inbox/'], files: [aStoredFile({ key: 'readme.md', name: 'readme.md' })] }) }

async function open(query = '', routes: Route[] = [TOP]) {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/fichiers${query}`)
  await router.isReady()
  render(FilesSection, { global: { plugins: [router] } })
  return { calls, router }
}

describe('FilesSection', () => {
  it('lists the folders, then the files, of the folder in the URL', async () => {
    const { calls } = await open('?prefix=inbox/')

    expect(await screen.findByRole('button', { name: 'inbox/' })).toBeTruthy()
    expect(screen.getByText('readme.md')).toBeTruthy()
    expect(calls[0]?.url.searchParams.get('prefix')).toBe('inbox/')
  })

  it('opens a folder by writing it into the URL', async () => {
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'inbox/' }))

    await waitFor(() => expect(router.currentRoute.value.query.prefix).toBe('inbox/'))
  })

  it('asks before replacing a file that exists, then sends it again with overwrite', async () => {
    // mock useMe as an editor here, as IpamSection.spec.ts does
    const { calls } = await open('', [
      TOP,
      { method: 'POST', path: '/files', status: 409, body: { error: 'duplicate', detail: 'exists' } },
    ])
    const input = await screen.findByLabelText('Déposer des fichiers')
    await fireEvent.update(input, [aFile('readme.md')] as never)

    expect(await screen.findByText(/existe déjà/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Remplacer' }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2))
  })

  it('offers no upload and no delete to a reader', async () => {
    // mock useMe as a reader
    await open()

    await screen.findByText('readme.md')
    expect(screen.queryByLabelText('Déposer des fichiers')).toBeNull()
    expect(screen.queryByRole('button', { name: /Supprimer/ })).toBeNull()
  })

  it('downloads through the client, never through a bare link', async () => {
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const { calls } = await open('', [TOP, { path: '/files/content', body: 'bytes' }])

    await fireEvent.click(await screen.findByRole('button', { name: 'Télécharger readme.md' }))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(calls.at(-1)?.url.searchParams.get('key')).toBe('readme.md')
  })
})
```

If `fireEvent.update` on a file input does not set `files` in jsdom, set it the way `tests/DocumentPanel.spec.ts` does. Replace the two `// mock useMe` comments with the exact mocking `IpamSection.spec.ts` uses. Run → FAIL.

- [ ] **Step 6: Implement** — `frontend/src/features/files/FilesSection.vue`:

```vue
<script setup lang="ts">
// Les fichiers du bucket MinIO (docs/adr/0036), un dossier à la fois.
//
// Le dossier ouvert est dans l'URL (`?prefix=`) : c'est une question qu'on
// partage et qu'on remonte avec « précédent ». L'API décide qui écrit ; ici on
// cache seulement ce qu'un lecteur ne peut pas faire.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { breadcrumbs, saveAs, useFiles, type StoredFile } from './useFiles'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const files = useFiles()

const failure = ref('')
const busy = ref(false)
/** La clé dont la suppression attend une confirmation. */
const confirming = ref<string | null>(null)
/** Les fichiers que l'API a refusés parce qu'ils existent déjà. */
const clashes = ref<File[]>([])

const prefix = computed(() => (typeof route.query.prefix === 'string' ? route.query.prefix : ''))
const crumbs = computed(() => breadcrumbs(prefix.value))

watch(
  prefix,
  (value) => {
    confirming.value = null
    clashes.value = []
    void files.load(value)
  },
  { immediate: true },
)

function openFolder(next: string): void {
  void router.push({ query: { ...route.query, prefix: next || undefined } })
}

async function onPicked(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const picked = [...(input.files ?? [])]
  busy.value = true
  failure.value = ''
  try {
    const refused: File[] = []
    for (const file of picked) {
      if ((await files.upload(prefix.value, file)) === 'exists') {
        refused.push(file)
      }
    }
    clashes.value = refused
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function replaceClashes(): Promise<void> {
  busy.value = true
  try {
    for (const file of clashes.value) {
      await files.upload(prefix.value, file, true)
    }
    clashes.value = []
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function onDownload(file: StoredFile): Promise<void> {
  failure.value = ''
  try {
    saveAs(await files.download(file.key), file.name)
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

async function confirmRemove(): Promise<void> {
  const key = confirming.value
  if (!key) {
    return
  }
  busy.value = true
  try {
    await files.remove(key)
    confirming.value = null
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

function weight(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} ko`
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`
}
</script>

<template>
  <section class="files" aria-label="Fichiers">
    <h2>Fichiers</h2>
    <p class="hint">
      Tout type de fichier, au plus 50 Mo. Un fichier déposé dans <code>inbox/</code> est lu par le
      pipeline qui alimente le catalogue.
    </p>

    <nav class="crumbs" aria-label="Dossier courant">
      <button type="button" class="link" @click="openFolder('')">Racine</button>
      <template v-for="crumb in crumbs" :key="crumb.prefix">
        <span aria-hidden="true">/</span>
        <button type="button" class="link" @click="openFolder(crumb.prefix)">{{ crumb.label }}</button>
      </template>
    </nav>

    <p v-if="canWrite" class="upload">
      <label for="files-input">Déposer des fichiers</label>
      <input id="files-input" type="file" multiple :disabled="busy" @change="onPicked" />
    </p>

    <div v-if="clashes.length > 0" class="banner" role="alert">
      <p>{{ clashes.map((file) => file.name).join(', ') }} existe déjà dans ce dossier.</p>
      <button type="button" :disabled="busy" @click="replaceClashes">Remplacer</button>
      <button type="button" class="secondary" @click="clashes = []">Annuler</button>
    </div>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="files.status.value === 'error'" class="banner banner--error" role="alert">
      {{ files.error.value }}
    </p>

    <template v-else-if="files.listing.value">
      <p
        v-if="files.listing.value.folders.length === 0 && files.listing.value.files.length === 0"
        class="hint"
      >
        Ce dossier est vide.
      </p>
      <table v-else>
        <caption class="sr-only">Contenu du dossier</caption>
        <thead>
          <tr>
            <th scope="col">Nom</th>
            <th scope="col">Taille</th>
            <th scope="col">Modifié</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="folder in files.listing.value.folders" :key="folder">
            <td>
              <button type="button" class="link" @click="openFolder(folder)">
                {{ folder.slice(prefix.length) }}
              </button>
            </td>
            <td></td>
            <td></td>
            <td></td>
          </tr>
          <tr v-for="file in files.listing.value.files" :key="file.key">
            <td>{{ file.name }}</td>
            <td>{{ weight(file.size) }}</td>
            <td>{{ file.last_modified.slice(0, 10) }}</td>
            <td>
              <button type="button" class="secondary" :aria-label="`Télécharger ${file.name}`" @click="onDownload(file)">
                Télécharger
              </button>
              <template v-if="canWrite">
                <template v-if="confirming === file.key">
                  <button type="button" :disabled="busy" @click="confirmRemove">Confirmer la suppression</button>
                  <button type="button" class="secondary" @click="confirming = null">Annuler</button>
                </template>
                <button
                  v-else
                  type="button"
                  class="secondary"
                  :aria-label="`Supprimer ${file.name}`"
                  @click="confirming = file.key"
                >
                  Supprimer
                </button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="files.listing.value.truncated" class="hint">
        Seules les 1000 premières entrées de ce dossier sont affichées.
      </p>
    </template>
  </section>
</template>

<style scoped>
.crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: center;
  margin-block: 0.5rem;
}
</style>
```

The folder button's accessible name is `folder.slice(prefix.length)` — at the top that is `inbox/`, which the spec relies on. Reuse the class names `DocumentPanel.vue` already styles (`hint`, `banner`, `banner--error`, `secondary`, `link`, `sr-only`); add no global CSS. Run → PASS.

- [ ] **Step 7: Gate** — `cd frontend && npm run lint && npm run typecheck && npm test -- --run`. All green.

---

### Task 7: Deployment and documentation

**Files:**
- Modify: `deploy/ea.stack.yml`, `deploy/ea.env.example`, `backend/.env.example`, `backend/tests/unit/test_deploy_stack.py`, `CLAUDE.md`
- Create: `docs/adr/0036-fichiers-dans-minio.md`

**Interfaces:**
- Consumes: the setting names of Task 2, the routes of Task 4, the tool names of Task 5, the section of Task 6.

- [ ] **Step 1: Failing deploy test** — append to `backend/tests/unit/test_deploy_stack.py`, reading the stack with the helper that file already uses:

```python
def test_the_deployed_api_keeps_its_files_in_the_infra_minio_on_infra_net() -> None:
    """Inside `infra-net` MinIO is the service `minio`, in plain HTTP like `postgres`.

    `minio.famillelallier.net` has no alias there (only Keycloak's name does),
    so the public name would not resolve from the container. See docs/adr/0036.
    """
    env = api_environment()  # whatever helper the file uses for the api service's environment
    assert env["EA_S3_ENABLED"] == "true"
    assert env["EA_S3_ENDPOINT"] == "${EA_S3_ENDPOINT:-minio:9000}"
    assert env["EA_S3_SECURE"] == "false"
    assert env["EA_S3_BUCKET"] == "${EA_S3_BUCKET:-ea-catalogue}"
    assert env["EA_S3_ACCESS_KEY"].startswith("${EA_S3_ACCESS_KEY:?")
    assert env["EA_S3_SECRET_KEY"].startswith("${EA_S3_SECRET_KEY:?")
```

Run → FAIL.

- [ ] **Step 2: The stack** — `deploy/ea.stack.yml`, `api` environment, after the embeddings block:

```yaml
      # Les fichiers (docs/adr/0036), dans le MinIO de l'Infra. Sur infra-net
      # c'est le service `minio`, en HTTP comme `postgres` : le nom public n'y a
      # pas d'alias. La clé est celle de l'utilisateur MinIO `ea-api`, en
      # lecture-écriture sur ce seul bucket — celui que lit le pipeline.
      EA_S3_ENABLED: "true"
      EA_S3_ENDPOINT: ${EA_S3_ENDPOINT:-minio:9000}
      EA_S3_SECURE: "false"
      EA_S3_BUCKET: ${EA_S3_BUCKET:-ea-catalogue}
      EA_S3_ACCESS_KEY: ${EA_S3_ACCESS_KEY:?EA_S3_ACCESS_KEY est obligatoire (utilisateur MinIO ea-api)}
      EA_S3_SECRET_KEY: ${EA_S3_SECRET_KEY:?EA_S3_SECRET_KEY est obligatoire (utilisateur MinIO ea-api)}
```

`deploy/ea.env.example`: add `EA_S3_ACCESS_KEY=` and `EA_S3_SECRET_KEY=` with a comment naming the MinIO user `ea-api`, and `# EA_S3_ENDPOINT=minio:9000` / `# EA_S3_BUCKET=ea-catalogue` among the commented overrides.
`backend/.env.example`: a block with `EA_S3_ENABLED=false`, `EA_S3_ENDPOINT=minio.famillelallier.net`, `EA_S3_SECURE=true`, `EA_S3_ACCESS_KEY=`, `EA_S3_SECRET_KEY=`, `EA_S3_BUCKET=ea-catalogue`, `EA_S3_CA_CERT=` — secrets blank. (The Mac reaches MinIO through the Infra NGINX, hence the CA.)

Run: `uv run pytest tests/unit/test_deploy_stack.py -q` → PASS. `make app-up` refuses to leave while a `:?` variable is empty, so the two keys must be in the real `deploy/ea.env` before the next deploy — say so in the ADR.

- [ ] **Step 3: The ADR** — `docs/adr/0036-fichiers-dans-minio.md`, following `docs/adr/TEMPLATE.md` (read it and `0034` for tone and headings; French like its neighbours). Status *Proposition* (nothing deployed). It must state:
  - **Contexte** : déposer des fichiers de tout type depuis le SPA et un agent ; seul `pipelines/` parlait à MinIO, en lecture.
  - **Décision** : l'API relaie les octets (`FileService` sous `require_caller`/`require_editor`), un seul bucket `ea-catalogue` partagé avec le pipeline (`inbox/` l'alimente), 50 Mo, 409 sauf `overwrite`, téléchargement `attachment` + `nosniff` + CSP `sandbox`, MCP en texte ou base64, `read_file` texte seulement.
  - **Options écartées** : URLs pré-signées (CORS, CA dans le navigateur, un agent ne fait pas le PUT) ; console MinIO + OIDC (pas de MCP, second modèle d'autorisation).
  - **Conséquences** : dépendance `minio` côté backend ; SDK synchrone derrière `asyncio.to_thread` ; course `stat`→`put` assumée ; MinIO jetable sur 127.0.0.1:9100 pour l'intégration ; `EA_S3_ENABLED` éteint par défaut → 503.
  - **À faire hors de ce repo** : (1) sur MinIO, un utilisateur `ea-api` et une politique lecture-écriture limitée à `arn:aws:s3:::ea-catalogue` et `arn:aws:s3:::ea-catalogue/*` (`s3:ListBucket`, `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`) ; (2) dans `~/OpenCode/Infra/nginx/conf.d/ea.conf`, `client_max_body_size 50m;` sur la location `/api/` — sinon NGINX applique 1 Mo et répond 413 ; (3) les deux clés dans `deploy/ea.env`.

- [ ] **Step 4: `CLAUDE.md`** — same change set as the code:
  - *Project status*: "eight sections built", add the files section (upload, browse, download, delete in MinIO, `?prefix=`, `docs/adr/0036`); the MCP tool count becomes **thirty-two** and the list gains "the files of the bucket".
  - *Locked stack decisions*: a row `File storage | MinIO of the Infra, one bucket (ea-catalogue), through the API — never presigned | One door for the SPA and an agent, authorisation in services/ — see docs/adr/0036`.
  - *Repository layout*: `domain/files.py` ("what a stored file is, which paths are allowed"), `services/files.py`, `repositories/object_store.py` (in the list of outbound clients), `features/files`.
  - *The MCP adapter…*: "three service providers" → "four", naming `get_files`, and one sentence on `upload_file` taking text or base64.
  - *TDD / integration*: the throwaway MinIO on 127.0.0.1:9100, refused otherwise (`refuse_a_shared_minio`), started by `make test-integration`.
  - *Commands*: `make minio-up`.
  - *Deployed…*: `EA_S3_*` on `minio:9000` inside `infra-net`, and the `client_max_body_size 50m` the vhost needs.

- [ ] **Step 5: Gate** — from the root: `make check`.

---

## Final verification (controller, after all tasks)

- [ ] `make check` green.
- [ ] `make test-integration` green (Docker running).
- [ ] `make audit` clean (network).
- [ ] Manual: `make run` with `EA_S3_ENABLED=true` and a key on the throwaway MinIO (`EA_S3_ENDPOINT=127.0.0.1:9100 EA_S3_SECURE=false EA_S3_BUCKET=ea-test`, bucket created by the integration run) → upload into `inbox/`, see the 409 prompt, download, delete, as an editor; the controls are gone as a reader.
