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
from tests.conftest import (
    InMemoryFileMetadata,
    InMemoryObjectStore,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    files_store: InMemoryObjectStore,
    files_metadata: InMemoryFileMetadata,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        files=files_store,
        file_metadata=files_metadata,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def upload(
    client: httpx.AsyncClient, name: str = "notes.md", raw: bytes = b"# Notes", **form: str
) -> httpx.Response:
    return await client.post("/files", files={"file": (name, raw, "text/markdown")}, data=form)


class TestUploading:
    async def test_a_file_lands_in_the_folder_it_was_sent_to(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await upload(client, prefix="inbox")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body | {"last_modified": None, "metadata": None} == {
            "key": "inbox/notes.md",
            "name": "notes.md",
            "size": 7,
            "content_type": "text/markdown",
            "last_modified": None,
            "metadata": None,
        }
        # The catalogue beside the bucket answers in the same payload (ADR 0039).
        assert body["metadata"]["uploaded_by"] == "local-developer"

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
    async def test_a_folder_lists_its_folders_and_its_files(
        self, client: httpx.AsyncClient
    ) -> None:
        await upload(client, prefix="inbox")
        await upload(client, prefix="inbox/sub")

        body = (await client.get("/files", params={"prefix": "inbox/"})).json()

        assert body["folders"] == ["inbox/sub/"]
        assert [f["key"] for f in body["files"]] == ["inbox/notes.md"]
        assert body["truncated"] is False

    async def test_a_download_is_always_an_attachment_never_rendered(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post(
            "/files", files={"file": ("page.html", b"<script>1</script>", "text/html")}
        )

        response = await client.get("/files/content", params={"key": "page.html"})

        assert response.status_code == 200
        assert response.content == b"<script>1</script>"
        assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''page.html"
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_a_name_with_spaces_and_accents_is_quoted(
        self, client: httpx.AsyncClient
    ) -> None:
        await upload(client, name="rapport été.md")
        response = await client.get("/files/content", params={"key": "rapport été.md"})
        assert (
            response.headers["content-disposition"]
            == "attachment; filename*=UTF-8''rapport%20%C3%A9t%C3%A9.md"
        )

    async def test_downloading_a_missing_file_is_not_found(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/files/content", params={"key": "nothing.md"})).status_code == 404


class TestDeleting:
    async def test_a_deleted_file_is_gone(self, client: httpx.AsyncClient) -> None:
        await upload(client)
        assert (await client.delete("/files", params={"key": "notes.md"})).status_code == 204
        assert (await client.delete("/files", params={"key": "notes.md"})).status_code == 404


class TestTheRecordBesideTheFile:
    """What PostgreSQL keeps about a file, over HTTP — see docs/adr/0039."""

    async def test_an_upload_carries_a_title_a_description_and_tags(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/files",
            files={"file": ("rapport.pdf", b"%PDF", "application/pdf")},
            # A multipart body has no arrays: `tags` is one field per tag,
            # which httpx writes from a list.
            data={"title": "Rapport 2026", "description": "Le bilan.", "tags": ["Budget"]},
        )

        assert response.status_code == 201, response.text
        recorded = response.json()["metadata"]
        assert (recorded["title"], recorded["description"]) == ("Rapport 2026", "Le bilan.")
        assert recorded["tags"] == ["budget"]
        assert recorded["sha256"] is not None

    async def test_one_file_is_read_with_its_record(self, client: httpx.AsyncClient) -> None:
        await upload(client, prefix="inbox")

        response = await client.get("/files/metadata", params={"key": "inbox/notes.md"})

        assert response.status_code == 200, response.text
        assert response.json()["metadata"]["uploaded_by"] == "local-developer"

    async def test_a_listing_carries_the_record_of_each_file(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post(
            "/files",
            files={"file": ("a.md", b"x", "text/markdown")},
            data={"title": "Le contrat"},
        )

        listed = (await client.get("/files")).json()["files"]

        assert [found["metadata"]["title"] for found in listed] == ["Le contrat"]

    async def test_the_record_is_replaced_whole(self, client: httpx.AsyncClient) -> None:
        await client.post(
            "/files",
            files={"file": ("a.md", b"x", "text/markdown")},
            data={"title": "A", "tags": ["budget"]},
        )

        response = await client.put(
            "/files/metadata", params={"key": "a.md"}, json={"title": "B", "tags": ["réseau"]}
        )

        assert response.status_code == 200, response.text
        recorded = response.json()["metadata"]
        assert (recorded["title"], recorded["tags"]) == ("B", ["réseau"])

    async def test_a_description_past_the_bound_is_refused(self, client: httpx.AsyncClient) -> None:
        await upload(client)

        response = await client.put(
            "/files/metadata", params={"key": "notes.md"}, json={"title": "a" * 500}
        )

        assert response.status_code == 422

    async def test_describing_a_file_that_is_not_in_the_bucket_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.put(
            "/files/metadata", params={"key": "nothing.md"}, json={"title": "B"}
        )
        assert response.status_code == 404

    async def test_a_file_dropped_straight_into_the_bucket_is_listed_undescribed(
        self, client: httpx.AsyncClient, files_store: InMemoryObjectStore
    ) -> None:
        """The pipeline's own door: the bytes are what exist."""
        files_store.objects["inbox/dropped.csv"] = (b"a,b\n", "text/csv")

        listed = (await client.get("/files", params={"prefix": "inbox"})).json()["files"]

        assert [(f["key"], f["metadata"]) for f in listed] == [("inbox/dropped.csv", None)]

    async def test_the_reconcile_gives_it_one(
        self, client: httpx.AsyncClient, files_store: InMemoryObjectStore
    ) -> None:
        files_store.objects["inbox/dropped.csv"] = (b"a,b\n", "text/csv")

        response = await client.post("/files/reconcile")

        assert response.json() == {"recorded": 1, "forgotten": 0}
        listed = (await client.get("/files", params={"prefix": "inbox"})).json()["files"]
        assert listed[0]["metadata"]["uploaded_by"] == ""


class TestWhoMay:
    @pytest_asyncio.fixture
    async def guarded(
        self,
        service: ArchitectureService,
        files_store: InMemoryObjectStore,
        files_metadata: InMemoryFileMetadata,
    ) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(
            Settings(debug=True),
            architecture_service=service,
            files=files_store,
            file_metadata=files_metadata,
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

    async def test_nobody_lists_nothing(self, guarded: httpx.AsyncClient) -> None:
        assert (await guarded.get("/files")).status_code == 401

    async def test_a_reader_lists_but_does_not_upload(self, guarded: httpx.AsyncClient) -> None:
        reader = {"Authorization": "Bearer reader"}
        assert (await guarded.get("/files", headers=reader)).status_code == 200
        response = await guarded.post(
            "/files", headers=reader, files={"file": ("a.md", b"x", "text/markdown")}
        )
        assert response.status_code == 403

    async def test_a_reader_does_not_describe_or_reconcile(
        self, guarded: httpx.AsyncClient
    ) -> None:
        reader = {"Authorization": "Bearer reader"}
        described = await guarded.put(
            "/files/metadata", headers=reader, params={"key": "a.md"}, json={"title": "B"}
        )
        assert described.status_code == 403
        assert (await guarded.post("/files/reconcile", headers=reader)).status_code == 403


async def test_without_storage_the_routes_answer_503(service: ArchitectureService) -> None:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    app.state.file_service = FileService(None, metadata=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/files")
    assert (response.status_code, response.json()["error"]) == (503, "storage_unavailable")
