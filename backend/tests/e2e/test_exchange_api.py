"""The exchange endpoints, end to end against doubled stores."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.archimate import ElementType as E
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryExchange,
    InMemoryPackages,
    InMemoryRepository,
    InMemorySavedQueries,
    StaticVerifier,
    a_reader,
    an_editor,
)
from tests.exchange_files import DATA, el, exchange_xml

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    diagrams: InMemoryDiagrams,
    packages: InMemoryPackages,
    saved_queries: InMemorySavedQueries,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        exchanges=exchange_store,  # type: ignore[arg-type]
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=packages,  # type: ignore[arg-type]
        queries=saved_queries,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestExport:
    async def test_the_download_headers_can_be_read_by_the_spa(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/exchanges/export", headers={"Origin": "http://localhost:5173"}
        )

        exposed = response.headers["access-control-expose-headers"].lower()
        assert "content-disposition" in exposed and "x-export-skipped-views" in exposed

    async def test_it_streams_the_model_as_an_xml_attachment(
        self, client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv-1")

        response = await client.get("/exchanges/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert response.headers["content-disposition"].startswith(
            "attachment; filename*=UTF-8''ea-model-"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "srv-1" in response.text

    async def test_an_unknown_scope_is_a_typed_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/exchanges/export", params={"scope": "galaxy"})

        assert response.status_code == 422
        assert response.json()["error"] == "unknown_scope"

    async def test_the_file_name_ignores_anything_the_caller_sends(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/exchanges/export", params={"filename": "../../etc/passwd", "format": "archimate-xml"}
        )

        assert "passwd" not in response.headers["content-disposition"]

    async def test_an_unknown_format_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/exchanges/export", params={"format": "xmi"})

        assert response.status_code == 422


class TestAuthentication:
    async def test_401_without_a_token_and_200_for_a_reader(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        verifier = StaticVerifier({"reader": a_reader(), "editor": an_editor()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
            verifier=verifier,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/exchanges/export")).status_code == 401
            reader = await client.get(
                "/exchanges/export", headers={"Authorization": "Bearer reader"}
            )
            assert reader.status_code == 200


def upload(data: bytes, **form: str) -> dict[str, object]:
    return {"files": {"file": ("model.xml", data, "application/xml")}, "data": form}


async def app_client(
    service: ArchitectureService, exchange_store: InMemoryExchange, **settings: object
) -> httpx.AsyncClient:
    app = create_app(
        Settings(debug=True, auth_enabled=False, **settings),  # type: ignore[arg-type]
        architecture_service=service,
        exchanges=exchange_store,  # type: ignore[arg-type]
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestImport:
    async def test_the_default_is_a_dry_run_that_writes_nothing(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        data = (DATA / "minimal-model.xml").read_bytes()

        response = await client.post("/exchanges/imports", **upload(data))

        body = response.json()
        assert response.status_code == 200
        assert body["dry_run"] is True and body["applied"] is False
        assert body["counts"]["elements"] == {
            "created": 4,
            "reused": 0,
            "rejected": 0,
            "ignored": 0,
        }
        assert body["sha256"] == hashlib.sha256(data).hexdigest()
        assert isinstance(body["duration_ms"], int) and body["duration_ms"] >= 0
        assert body["issue_count"] == len(body["issues"]) and body["truncated"] is False
        assert repository.elements == {}

    async def test_dry_run_false_applies_and_the_same_file_again_creates_nothing(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        data = (DATA / "minimal-model.xml").read_bytes()

        first = await client.post("/exchanges/imports", **upload(data, dry_run="false"))
        again = await client.post("/exchanges/imports", **upload(data, dry_run="false"))

        assert first.json()["applied"] is True and len(repository.elements) == 4
        assert again.json()["counts"]["elements"]["created"] == 0
        assert again.json()["counts"]["relationships"]["created"] == 0
        assert len(repository.elements) == 4 and len(repository.relationships) == 3

    async def test_strict_with_a_refused_row_writes_nothing_and_says_so(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        data = (DATA / "refused-relations.xml").read_bytes()

        response = await client.post(
            "/exchanges/imports", **upload(data, dry_run="false", strict="true")
        )

        body = response.json()
        assert response.status_code == 200 and body["applied"] is False
        assert body["issues"][-1]["code"] == "strict_refused"
        assert repository.elements == {} and repository.relationships == {}

    async def test_a_malformed_file_is_a_typed_422_without_echoing_it(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/exchanges/imports", **upload(b"<model><leaky-name>"))

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_exchange_file"
        assert "leaky-name" not in response.text

    @pytest.mark.parametrize("name", ["billion-laughs.xml", "xxe-file.xml"])
    async def test_an_entity_bomb_or_xxe_is_a_422(
        self, client: httpx.AsyncClient, name: str
    ) -> None:
        response = await client.post("/exchanges/imports", **upload((DATA / name).read_bytes()))

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_exchange_file"
        assert "lol" not in response.text and "hostname" not in response.text

    async def test_an_empty_file_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/exchanges/imports", **upload(b""))

        assert response.status_code == 422

    async def test_a_held_lock_is_a_409(
        self, client: httpx.AsyncClient, exchange_store: InMemoryExchange
    ) -> None:
        exchange_store.lock_held = True

        response = await client.post(
            "/exchanges/imports", **upload(exchange_xml(el("a", "Node", "A")))
        )

        assert response.status_code == 409
        assert response.json()["error"] == "import_in_progress"

    async def test_the_report_is_bounded_whatever_the_file_refuses(
        self, client: httpx.AsyncClient
    ) -> None:
        rows = "".join(el(f"e{i}", "Node", " ") for i in range(1_200))  # all blank names

        response = await client.post("/exchanges/imports", **upload(exchange_xml(rows)))

        body = response.json()
        assert body["counts"]["elements"]["rejected"] == 1_200  # exact
        assert len(body["issues"]) == 1_000 and body["truncated"] is True  # cut
        assert body["issue_count"] == 1_200


class TestAHostileMultipart:
    """Whatever arrives, the answer is typed: never a 500, never an echo."""

    async def test_no_file_at_all(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/exchanges/imports", data={"dry_run": "true"})

        assert response.status_code == 422

    async def test_a_field_nobody_declared(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports", **upload(exchange_xml(), on_conflict_now="overwrite")
        )

        assert response.status_code == 422

    async def test_an_unknown_option_value(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports", **upload(exchange_xml(), on_conflict="overwrite")
        )

        assert response.status_code == 422

    async def test_two_files_under_the_expected_name(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            files=[
                ("file", ("a.xml", exchange_xml(), "application/xml")),
                ("file", ("b.xml", exchange_xml(), "application/xml")),
            ],
        )

        assert response.status_code == 422

    async def test_a_second_file_under_another_name(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            files=[
                ("file", ("a.xml", exchange_xml(), "application/xml")),
                ("other", ("b.xml", exchange_xml(), "application/xml")),
            ],
        )

        assert response.status_code == 422

    async def test_the_file_name_is_neither_used_nor_echoed(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        evil = "../../etc/passwd\r\n[ERROR] forged\x00.xml"

        response = await client.post(
            "/exchanges/imports",
            files={"file": (evil, exchange_xml(el("a", "Node", "A")), "text/x-wrong")},
            data={"dry_run": "false"},
        )

        assert response.status_code == 200  # the content type and the name decide nothing
        assert "passwd" not in response.text and "forged" not in response.text
        assert len(repository.elements) == 1

    async def test_a_file_sent_as_a_plain_text_field(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/exchanges/imports", data={"file": "<model/>"})

        assert response.status_code == 422

    async def test_a_body_that_is_not_multipart(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports", content=b"<model/>", headers={"content-type": "application/xml"}
        )

        assert response.status_code == 422


class TestTheCeiling:
    async def test_a_file_over_the_ceiling_is_a_413_cut_during_reception(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        async with await app_client(service, exchange_store, exchange_max_bytes=2048) as client:
            response = await client.post(
                "/exchanges/imports", **upload(b"<model>" + b"x" * 200_000)
            )

        assert response.status_code == 413
        assert response.json()["error"] == "file_too_large"
        assert "2048" in response.json()["detail"]

    async def test_the_413_carries_the_request_id_and_the_cors_headers(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        async with await app_client(service, exchange_store, exchange_max_bytes=2048) as client:
            response = await client.post(
                "/exchanges/imports",
                headers={"Origin": "http://localhost:5173"},
                **upload(b"x" * 200_000),
            )

        assert response.status_code == 413
        assert response.headers["x-request-id"]
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    async def test_a_body_sent_in_chunks_with_no_length_is_cut_too(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            yield (
                b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.xml"\r\n'
                b"Content-Type: application/xml\r\n\r\n"
            )
            for _ in range(200):
                yield b" " * 1024

        async with await app_client(service, exchange_store, exchange_max_bytes=2048) as client:
            response = await client.post(
                "/exchanges/imports",
                content=chunks(),
                headers={"content-type": "multipart/form-data; boundary=x"},
            )

        assert response.status_code == 413

    async def test_the_ceiling_is_the_setting_not_a_copy_of_it(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        base = exchange_xml(el("a", "Node", "A"))
        small = base + b" " * (
            1024 - len(base)
        )  # exactly the smallest ceiling a deployment can set
        async with await app_client(service, exchange_store, exchange_max_bytes=1024) as client:
            exact = await client.post("/exchanges/imports", **upload(small))
            over = await client.post("/exchanges/imports", **upload(small + b" "))

        assert exact.status_code == 200
        assert over.status_code == 413  # by the service's own check, whatever the envelope allows


class TestWhoMayImport:
    async def test_401_403_and_200(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            data = exchange_xml()
            assert (await client.post("/exchanges/imports", **upload(data))).status_code == 401
            as_reader = await client.post(
                "/exchanges/imports", headers={"Authorization": "Bearer reader"}, **upload(data)
            )
            assert as_reader.status_code == 403  # even a dry run
            as_editor = await client.post(
                "/exchanges/imports", headers={"Authorization": "Bearer editor"}, **upload(data)
            )
            assert as_editor.status_code == 200


async def test_the_parts_the_form_accepts_are_the_ones_the_contract_declares(
    client: httpx.AsyncClient,
) -> None:
    from ea.api.exchanges import IMPORT_FIELDS

    schema = (await client.get("/openapi.json")).json()
    body = schema["paths"]["/exchanges/imports"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    name = body["$ref"].rsplit("/", 1)[1]

    assert set(schema["components"]["schemas"][name]["properties"]) == IMPORT_FIELDS


class TestAuthenticationPrecedesTheBody:
    """FastAPI parses a multipart body before it resolves any dependency: so the order
    authentication, then role, then size, then reading is the middleware's, and is proved here
    by counting what the application pulled from the wire."""

    MEGABYTE = 1024 * 1024

    @pytest.fixture
    def app(self, service: ArchitectureService, exchange_store: InMemoryExchange):  # type: ignore[no-untyped-def]
        return create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )

    @pytest.fixture(autouse=True)
    def parser_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        from starlette.formparsers import MultiPartParser

        calls: list[int] = []
        original = MultiPartParser.parse

        async def counting(self: MultiPartParser) -> object:
            calls.append(1)
            return await original(self)

        monkeypatch.setattr(MultiPartParser, "parse", counting)
        return calls

    async def big_post(
        self,
        app: object,
        token: str | None,
        megabytes: int = 19,
        declare_length: bool = False,
    ) -> tuple[httpx.Response, int]:
        pulled = 0

        async def body() -> AsyncIterator[bytes]:
            nonlocal pulled
            yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.xml"\r\n\r\n'
            for _ in range(megabytes):
                pulled += 1
                yield b" " * self.MEGABYTE
            yield b"\r\n--x--\r\n"

        headers = {"content-type": "multipart/form-data; boundary=x"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if declare_length:
            headers["content-length"] = str((megabytes + 1) * self.MEGABYTE)
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/exchanges/imports", content=body(), headers=headers)
        return response, pulled

    @pytest.mark.parametrize("token", [None, "not-a-token"])
    async def test_an_anonymous_caller_gets_a_401_and_the_body_is_not_read(
        self, app: object, parser_calls: list[int], token: str | None
    ) -> None:
        response, pulled = await self.big_post(app, token)

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["x-request-id"]
        assert pulled <= 1 and parser_calls == []  # no multipart parser, no spooled file

    async def test_a_reader_gets_a_403_and_the_body_is_not_read(
        self, app: object, parser_calls: list[int]
    ) -> None:
        response, pulled = await self.big_post(app, "reader")

        assert response.status_code == 403
        assert pulled <= 1 and parser_calls == []

    async def test_authentication_comes_before_size(self, app: object) -> None:
        response, _ = await self.big_post(app, None, megabytes=25, declare_length=True)

        assert response.status_code == 401  # not 413: an anonymous caller learns nothing of limits

    async def test_the_role_comes_before_size(self, app: object) -> None:
        response, _ = await self.big_post(app, "reader", megabytes=25, declare_length=True)

        assert response.status_code == 403

    async def test_an_editor_over_the_ceiling_is_cut_at_the_ceiling(
        self, app: object, parser_calls: list[int]
    ) -> None:
        response, pulled = await self.big_post(app, "editor", megabytes=40)

        assert response.status_code == 413
        assert pulled < 40  # stopped where the ceiling was crossed, not at the end

    async def test_many_anonymous_big_requests_at_once_read_nothing(
        self, app: object, parser_calls: list[int]
    ) -> None:
        import asyncio

        results = await asyncio.gather(*(self.big_post(app, None) for _ in range(5)))

        assert {response.status_code for response, _ in results} == {401}
        assert sum(pulled for _, pulled in results) <= 5
        assert parser_calls == []

    async def test_an_editor_is_let_through_to_the_route(self, app: object) -> None:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/exchanges/imports",
                headers={"Authorization": "Bearer editor"},
                **upload(exchange_xml()),
            )

        assert response.status_code == 200

    async def test_with_authentication_off_the_local_developer_passes(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        async with await app_client(service, exchange_store) as client:
            response = await client.post("/exchanges/imports", **upload(exchange_xml()))

        assert response.status_code == 200


CSV_HEADER = b'"ID","Type","Name"\r\n'


def csv_upload(**form: str) -> dict[str, object]:
    return {"data": {"format": "csv", **form}}


class TestCsvOverHttp:
    async def test_export_is_a_zip_attachment(
        self, client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv-1")

        response = await client.get("/exchanges/export", params={"format": "csv"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["content-disposition"].startswith(
            "attachment; filename*=UTF-8''ea-model-"
        )
        assert response.headers["content-disposition"].endswith(".zip")
        assert response.headers["x-content-type-options"] == "nosniff"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        assert archive.namelist() == ["elements.csv", "relations.csv", "properties.csv"]
        assert "srv-1" in archive.read("elements.csv").decode()

    async def test_the_archive_is_the_same_whoever_names_what(
        self, client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv-1")

        response = await client.get(
            "/exchanges/export", params={"format": "csv", "filename": "../../x.zip"}
        )

        assert "x.zip" not in response.headers["content-disposition"]

    async def test_a_formula_is_neutralised_unless_the_query_says_otherwise(
        self, client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="=1+1")

        safe = await client.get("/exchanges/export", params={"format": "csv"})
        raw = await client.get("/exchanges/export", params={"format": "csv", "safe_cells": "false"})

        assert safe.headers["x-export-cells"] == "safe"
        assert raw.headers["x-export-cells"] == "raw"
        assert raw.headers["content-disposition"].endswith("-raw.zip")
        assert "raw" not in safe.headers["content-disposition"]
        assert "'=1+1" in zipfile.ZipFile(io.BytesIO(safe.content)).read("elements.csv").decode()
        assert '"=1+1"' in zipfile.ZipFile(io.BytesIO(raw.content)).read("elements.csv").decode()

    async def test_a_csv_import_takes_three_parts(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        files = {
            "elements": ("elements.csv", CSV_HEADER + b'"a","Node","srv"\r\n', "text/csv"),
            "relations": ("relations.csv", b'"ID","Type","Source","Target"\r\n', "text/csv"),
        }

        response = await client.post(
            "/exchanges/imports", files=files, **csv_upload(dry_run="false")
        )

        assert response.status_code == 200
        assert response.json()["format"] == "csv"
        assert [e.name for e in repository.elements.values()] == ["srv"]

    async def test_a_csv_import_is_a_dry_run_unless_said_otherwise(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        files = {"elements": ("e.csv", CSV_HEADER + b'"a","Node","srv"\r\n', "text/csv")}

        response = await client.post("/exchanges/imports", files=files, **csv_upload())

        assert response.json()["dry_run"] is True and repository.elements == {}

    async def test_the_digest_is_the_one_of_the_single_file_when_there_is_only_one(
        self, client: httpx.AsyncClient
    ) -> None:
        data = CSV_HEADER + b'"a","Node","srv"\r\n'

        response = await client.post(
            "/exchanges/imports", files={"elements": ("e.csv", data, "text/csv")}, **csv_upload()
        )

        assert response.json()["sha256"] == hashlib.sha256(data).hexdigest()

    async def test_the_safe_cells_part_restores_or_keeps_the_guard(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        files = {"elements": ("e.csv", CSV_HEADER + b'"a","Node","\'=1"\r\n', "text/csv")}

        await client.post(
            "/exchanges/imports", files=files, **csv_upload(dry_run="false", safe_cells="false")
        )

        assert [e.name for e in repository.elements.values()] == ["'=1"]

    async def test_without_the_safe_cells_part_the_guard_is_given_back(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        files = {"elements": ("e.csv", CSV_HEADER + b'"a","Node","\'=x"\r\n', "text/csv")}

        await client.post("/exchanges/imports", files=files, **csv_upload(dry_run="false"))

        assert [e.name for e in repository.elements.values()] == ["=x"]

    async def test_a_csv_import_without_elements_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            files={"relations": ("r.csv", b'"ID","Type","Source","Target"\r\n', "text/csv")},
            **csv_upload(),
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_exchange_file"

    async def test_an_xml_import_without_its_file_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            files={"elements": ("e.csv", CSV_HEADER, "text/csv")},
            data={"format": "archimate-xml"},
        )

        assert response.status_code == 422

    async def test_each_format_takes_only_its_own_parts(self, client: httpx.AsyncClient) -> None:
        xml_part = ("file", ("a.xml", exchange_xml(), "application/xml"))
        csv_part = ("elements", ("e.csv", CSV_HEADER, "text/csv"))

        csv_with_xml = await client.post(
            "/exchanges/imports", files=[csv_part, xml_part], **csv_upload()
        )
        xml_with_csv = await client.post(
            "/exchanges/imports", files=[xml_part, csv_part], data={"format": "archimate-xml"}
        )
        xml_with_cells = await client.post(
            "/exchanges/imports",
            files=[xml_part],
            data={"format": "archimate-xml", "safe_cells": "false"},
        )

        assert (csv_with_xml.status_code, xml_with_csv.status_code) == (422, 422)
        assert xml_with_cells.status_code == 422

    @pytest.mark.parametrize("name", ["elements", "relations", "properties"])
    async def test_a_part_sent_twice_is_refused(self, client: httpx.AsyncClient, name: str) -> None:
        files = [
            ("elements", ("e.csv", CSV_HEADER, "text/csv")),
            (name, ("again.csv", CSV_HEADER, "text/csv")),
        ]

        response = await client.post("/exchanges/imports", files=files, **csv_upload())

        assert response.status_code == 422

    async def test_a_part_nobody_declared_is_refused(self, client: httpx.AsyncClient) -> None:
        files = [
            ("elements", ("e.csv", CSV_HEADER, "text/csv")),
            ("views", ("v.csv", CSV_HEADER, "text/csv")),
        ]

        response = await client.post("/exchanges/imports", files=files, **csv_upload())

        assert response.status_code == 422

    async def test_a_bad_file_is_a_typed_422_that_never_quotes_it(
        self, client: httpx.AsyncClient
    ) -> None:
        for data in (CSV_HEADER + b'"a","Node","LEAK\x00"\r\n', b"LEAK\xff\xfe", b'"LEAK'):
            response = await client.post(
                "/exchanges/imports",
                files={"elements": ("e.csv", data, "text/csv")},
                **csv_upload(),
            )

            assert response.status_code == 422
            assert response.json()["error"] == "invalid_exchange_file"
            assert "LEAK" not in response.text

    async def test_the_names_of_the_files_are_neither_used_nor_echoed(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        evil = "../../etc/passwd\r\n[ERROR] forged.csv"

        response = await client.post(
            "/exchanges/imports",
            files={"elements": (evil, CSV_HEADER + b'"a","Node","A"\r\n', "text/x-wrong")},
            **csv_upload(dry_run="false"),
        )

        assert response.status_code == 200
        assert "passwd" not in response.text and "forged" not in response.text
        assert len(repository.elements) == 1

    async def test_the_ceiling_counts_the_three_files_together(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        part = CSV_HEADER + b" " * 1000  # under the 2048 ceiling alone, over it together
        files = {
            "elements": ("e.csv", part, "text/csv"),
            "relations": ("r.csv", part, "text/csv"),
            "properties": ("p.csv", part, "text/csv"),
        }
        async with await app_client(service, exchange_store, exchange_max_bytes=2048) as client:
            response = await client.post("/exchanges/imports", files=files, **csv_upload())

        assert response.status_code == 413
        assert response.json()["error"] == "file_too_large"

    async def test_a_reader_is_refused_before_a_csv_is_read(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
            verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
        )
        files = {"elements": ("e.csv", CSV_HEADER, "text/csv")}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            anonymous = await client.post("/exchanges/imports", files=files, **csv_upload())
            reader = await client.post(
                "/exchanges/imports",
                headers={"Authorization": "Bearer reader"},
                files=files,
                **csv_upload(),
            )
            editor = await client.post(
                "/exchanges/imports",
                headers={"Authorization": "Bearer editor"},
                files=files,
                **csv_upload(),
            )

        assert (anonymous.status_code, reader.status_code, editor.status_code) == (401, 403, 200)


class TestViewsOverHttp:
    async def test_with_views_false_counts_the_views_as_ignored(
        self, client: httpx.AsyncClient
    ) -> None:
        data = (DATA / "views-model.xml").read_bytes()

        kept = await client.post("/exchanges/imports", **upload(data, dry_run="false"))
        left = await client.post(
            "/exchanges/imports", **upload(data, dry_run="false", with_views="false")
        )

        assert kept.json()["counts"]["views"]["created"] == 1
        assert left.json()["counts"]["views"]["ignored"] == 1

    async def test_with_views_is_not_a_csv_part(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            files={"elements": ("e.csv", CSV_HEADER, "text/csv")},
            **csv_upload(with_views="false"),
        )

        assert response.status_code == 422


class TestPackagesOverHttp:
    ORGANIZED = exchange_xml(
        el("a", "Node", "A"),
        extra="<organizations><item><label>Infra</label>"
        '<item identifierRef="a"/></item></organizations>',
    )

    async def test_folders_become_packages_and_the_target_is_a_form_part(
        self, client: httpx.AsyncClient, packages: InMemoryPackages, repository: InMemoryRepository
    ) -> None:
        landing = (await client.post("/packages", json={"name": "Landing"})).json()["id"]

        response = await client.post(
            "/exchanges/imports",
            **upload(self.ORGANIZED, dry_run="false", target_package_id=landing),
        )

        assert response.status_code == 200
        assert response.json()["counts"]["packages"]["created"] == 1
        infra = next(p for p in packages.packages.values() if p.name == "Infra")
        assert str(infra.parent_id) == landing

    async def test_an_unknown_target_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports",
            **upload(self.ORGANIZED, dry_run="false", target_package_id=str(uuid4())),
        )

        assert response.status_code == 404

    async def test_a_target_that_is_not_an_id_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/exchanges/imports", **upload(self.ORGANIZED, target_package_id="root")
        )

        assert response.status_code == 422


class TestScopesOverHttp:
    async def test_the_model_export_declares_no_skipped_view(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/exchanges/export")

        assert response.headers["x-export-skipped-views"] == "0"

    async def test_a_package_scope_exports_the_package_and_declares_the_views_it_left(
        self,
        client: httpx.AsyncClient,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        import dataclasses

        top = (await client.post("/packages", json={"name": "Top"})).json()["id"]
        inside = await service.create_element(element_type=E.NODE, name="in")
        outside = await service.create_element(element_type=E.NODE, name="out")
        repository.elements[inside.id] = dataclasses.replace(inside, package_id=UUID(top))
        view = (await client.post("/diagrams", json={"name": "straddling"})).json()["id"]
        nodes = [
            {
                "element_id": str(e.id),
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 50,
                "parent_object_id": None,
                "z": 0,
            }
            for e in (inside, outside)
        ]
        await client.put(
            f"/diagrams/{view}/layout",
            json={"revision": 0, "nodes": nodes, "objects": [], "edges": []},
        )

        response = await client.get(
            "/exchanges/export", params={"scope": "package", "package_id": top}
        )

        assert response.status_code == 200
        assert response.headers["x-export-skipped-views"] == "1"
        assert "in" in response.text and ">out<" not in response.text
        assert "-package-" in response.headers["content-disposition"]

    @pytest.mark.parametrize(
        "params",
        [
            {"scope": "package"},
            {"scope": "query"},
            {"scope": "model", "package_id": "00000000-0000-0000-0000-000000000001"},
            {"scope": "package", "package_id": "00000000-0000-0000-0000-000000000001"},
            {"scope": "query", "query_id": "00000000-0000-0000-0000-000000000001"},
            {
                "scope": "package",
                "package_id": "00000000-0000-0000-0000-000000000001",
                "query_id": "00000000-0000-0000-0000-000000000002",
            },
        ],
    )
    async def test_an_ill_formed_or_unknown_scope_is_a_typed_422(
        self, client: httpx.AsyncClient, params: dict[str, str]
    ) -> None:
        response = await client.get("/exchanges/export", params=params)

        assert response.status_code == 422
        assert response.json()["error"] == "unknown_scope"

    async def test_an_id_that_is_not_a_uuid_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/exchanges/export", params={"scope": "package", "package_id": "not-a-uuid"}
        )

        assert response.status_code == 422

    async def test_a_saved_query_that_cannot_be_read_is_a_422_not_a_500(
        self, client: httpx.AsyncClient, saved_queries: InMemorySavedQueries
    ) -> None:
        saved = (
            await client.post(
                "/queries",
                json={
                    "name": "q",
                    "query": {
                        "version": 1,
                        "element_types": ["node"],
                        "layers": [],
                        "text": None,
                        "package_id": None,
                        "include_subpackages": True,
                        "root_only": False,
                        "properties": [],
                        "relations": [],
                        "order": "name",
                        "limit": 50,
                        "offset": 0,
                    },
                },
            )
        ).json()
        saved_queries.corrupt.add(UUID(saved["id"]))

        response = await client.get(
            "/exchanges/export", params={"scope": "query", "query_id": saved["id"]}
        )

        assert response.status_code == 422
        assert response.json()["error"] == "unknown_scope"
        assert "version" not in response.text


class TestADatabaseFailureNeverQuotesTheFile:
    async def test_an_integrity_error_that_escapes_an_import_is_logged_without_its_detail(
        self,
        service: ArchitectureService,
        exchange_store: InMemoryExchange,
        diagrams: InMemoryDiagrams,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The detail of a unique violation is `Key (name)=(<the value>)`, and the statement
        carries its parameters: both are the file's content, and neither belongs in a log."""
        import logging

        from sqlalchemy.exc import IntegrityError

        marker = "SECRET-MARKER-ROW"

        async def failing() -> object:
            try:
                raise ValueError(f"DETAIL: Key (name)=({marker}) already exists.")
            except ValueError as detail:
                raise IntegrityError(
                    "INSERT INTO elements (name) VALUES ($1)", {"name": marker}, detail
                ) from detail

        monkeypatch.setattr(exchange_store, "known_elements", failing)
        app = create_app(
            Settings(debug=True, auth_enabled=False),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with caplog.at_level(logging.DEBUG, logger="ea"):
                response = await client.post(
                    "/exchanges/imports", **upload(exchange_xml(el("a", "Node", marker)))
                )

        assert response.status_code == 500
        assert marker not in response.text
        assert marker not in caplog.text
        for record in caplog.records:
            assert marker not in str(vars(record))
            if record.exc_info:
                import traceback

                assert marker not in "".join(traceback.format_exception(*record.exc_info))
        assert "IntegrityError" in caplog.text  # the kind of failure is still said
