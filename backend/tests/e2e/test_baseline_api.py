"""The baseline endpoints against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.packages import Package
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemoryBaselines,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"
STRANGER = "00000000-0000-4000-8000-000000000000"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    baseline_store: InMemoryBaselines,
    audit_log: InMemoryAuditLog,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        baselines=baseline_store,
        audit=audit_log,
        verifier=verifier,
        packages=an_empty_package_tree(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def element(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": "node", "name": name}, headers=as_(EDITOR)
    )
    return dict(response.json())


async def capture(client: httpx.AsyncClient, name: str = "v1") -> dict[str, Any]:
    response = await client.post("/baselines", json={"name": name}, headers=as_(EDITOR))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def dry_run(client: httpx.AsyncClient, baseline_id: str, **more: Any) -> dict[str, Any]:
    response = await client.post(
        f"/baselines/{baseline_id}/restore", json=more, headers=as_(EDITOR)
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestBaselines:
    async def test_capture_list_read_delete(self, client: httpx.AsyncClient) -> None:
        await element(client, "a")
        baseline = await capture(client)

        listed = (await client.get("/baselines", headers=as_(READER))).json()
        one = (await client.get(f"/baselines/{baseline['id']}", headers=as_(READER))).json()
        deleted = await client.delete(f"/baselines/{baseline['id']}", headers=as_(EDITOR))

        assert baseline["element_count"] == 1
        assert [b["name"] for b in listed] == ["v1"] and one["name"] == "v1"
        assert deleted.status_code == 204
        assert (await client.get("/baselines", headers=as_(READER))).json() == []

    async def test_a_duplicate_name_is_a_409_a_reader_a_403_a_stranger_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        baseline = await capture(client)

        assert (
            await client.post("/baselines", json={"name": "v1"}, headers=as_(EDITOR))
        ).status_code == 409
        assert (
            await client.post("/baselines", json={"name": "x"}, headers=as_(READER))
        ).status_code == 403
        assert (
            await client.delete(f"/baselines/{baseline['id']}", headers=as_(READER))
        ).status_code == 403
        assert (await client.get(f"/baselines/{STRANGER}", headers=as_(READER))).status_code == 404
        assert (
            await client.delete(f"/baselines/{STRANGER}", headers=as_(EDITOR))
        ).status_code == 404

    async def test_nothing_is_served_without_a_token(self, client: httpx.AsyncClient) -> None:
        baseline = await capture(client)
        base = f"/baselines/{baseline['id']}"

        for response in (
            await client.get("/baselines"),
            await client.post("/baselines", json={"name": "x"}),
            await client.get(base),
            await client.delete(base),
            await client.get(f"{base}/diff"),
            await client.post(f"{base}/restore", json={}),
        ):
            assert response.status_code == 401

    async def test_bad_names_are_a_422_never_a_500(self, client: httpx.AsyncClient) -> None:
        for body in (
            {"name": "bad\x00name"},
            {"name": "x", "description": "bad\x00"},
            {"name": "   "},
            {"name": ""},
            {"name": "n" * 201},
            {"name": "x", "description": "d" * 2001},
            {"name": "x", "surprise": 1},
        ):
            response = await client.post("/baselines", json=body, headers=as_(EDITOR))
            assert response.status_code == 422, body

    async def test_the_diff_defaults_to_the_current_model(self, client: httpx.AsyncClient) -> None:
        baseline = await capture(client)
        await element(client, "new")

        diff = (await client.get(f"/baselines/{baseline['id']}/diff", headers=as_(READER))).json()
        explicit = (
            await client.get(
                f"/baselines/{baseline['id']}/diff",
                params={"against": "current"},
                headers=as_(READER),
            )
        ).json()
        bad = await client.get(
            f"/baselines/{baseline['id']}/diff", params={"against": "nonsense"}, headers=as_(READER)
        )
        long = await client.get(
            f"/baselines/{baseline['id']}/diff", params={"against": "x" * 37}, headers=as_(READER)
        )

        assert [e["label"] for e in diff["elements_added"]] == ["new"]
        assert explicit == diff
        assert bad.status_code == 422 and long.status_code == 422

    async def test_two_baselines_are_compared(self, client: httpx.AsyncClient) -> None:
        first = await capture(client, "v1")
        await element(client, "new")
        second = await capture(client, "v2")

        diff = (
            await client.get(
                f"/baselines/{first['id']}/diff",
                params={"against": second["id"]},
                headers=as_(READER),
            )
        ).json()
        missing = await client.get(
            f"/baselines/{first['id']}/diff", params={"against": STRANGER}, headers=as_(READER)
        )

        assert [e["label"] for e in diff["elements_added"]] == ["new"]
        assert missing.status_code == 404


class TestRestoring:
    async def test_a_dry_run_is_the_default_and_writes_nothing(
        self, client: httpx.AsyncClient, audit_log: InMemoryAuditLog
    ) -> None:
        gone = await element(client, "gone")
        baseline = await capture(client)
        await client.delete(f"/elements/{gone['id']}", headers=as_(EDITOR))
        events = len(audit_log.events)

        response = await client.post(
            f"/baselines/{baseline['id']}/restore", json={}, headers=as_(EDITOR)
        )

        assert response.status_code == 200
        assert response.json()["applied"] is False
        assert response.json()["plan"]["elements_to_create"] == 1
        assert len(response.json()["digest"]) == 64
        assert (await client.get(f"/elements/{gone['id']}", headers=as_(READER))).status_code == 404
        assert len(audit_log.events) == events

    async def test_the_plan_is_exact_in_counts_and_bounded_in_what_it_lists(
        self, client: httpx.AsyncClient
    ) -> None:
        gone = [await element(client, f"gone-{i:02d}") for i in range(25)]
        baseline = await capture(client)
        for one in gone:
            await client.delete(f"/elements/{one['id']}", headers=as_(EDITOR))

        plan = (await dry_run(client, baseline["id"]))["plan"]

        assert plan["elements_to_create"] == 25
        assert len(plan["created"]) == 20
        assert plan["created"][0]["label"] == "gone-00"
        assert plan["blocked_count"] == 0 and plan["warning_count"] == 1  # the documents

    async def test_applying_needs_dry_run_false_and_the_digest_of_the_plan_read(
        self, client: httpx.AsyncClient
    ) -> None:
        gone = await element(client, "gone")
        baseline = await capture(client)
        await client.delete(f"/elements/{gone['id']}", headers=as_(EDITOR))
        url = f"/baselines/{baseline['id']}/restore"
        read = await dry_run(client, baseline["id"])

        no_digest = await client.post(url, json={"dry_run": False}, headers=as_(EDITOR))
        stale = await client.post(
            url, json={"dry_run": False, "expected_digest": "0" * 64}, headers=as_(EDITOR)
        )
        malformed = await client.post(
            url, json={"dry_run": False, "expected_digest": "nope"}, headers=as_(EDITOR)
        )
        applied = await client.post(
            url, json={"dry_run": False, "expected_digest": read["digest"]}, headers=as_(EDITOR)
        )

        assert no_digest.status_code == 422 and malformed.status_code == 422
        assert stale.status_code == 409 and stale.json()["error"] == "restore_conflict"
        assert applied.json()["applied"] is True
        assert (await client.get(f"/elements/{gone['id']}", headers=as_(READER))).status_code == 200

    async def test_a_model_that_moved_since_the_plan_was_read_is_a_retryable_409(
        self, client: httpx.AsyncClient
    ) -> None:
        gone = await element(client, "gone")
        baseline = await capture(client)
        await client.delete(f"/elements/{gone['id']}", headers=as_(EDITOR))
        read = await dry_run(client, baseline["id"])
        await element(client, "late")

        response = await client.post(
            f"/baselines/{baseline['id']}/restore",
            json={"dry_run": False, "expected_digest": read["digest"]},
            headers=as_(EDITOR),
        )

        assert response.status_code == 409
        assert response.json()["error"] == "restore_conflict"
        assert (await client.get(f"/elements/{gone['id']}", headers=as_(READER))).status_code == 404

    async def test_a_protected_element_is_listed_by_the_dry_run_and_refused_when_applied(
        self, client: httpx.AsyncClient, baseline_store: InMemoryBaselines
    ) -> None:
        edited = await element(client, "billing")
        baseline = await capture(client)
        await client.patch(f"/elements/{edited['id']}", json={"name": "late"}, headers=as_(EDITOR))
        baseline_store.protected.add(UUID(edited["id"]))

        read = await dry_run(client, baseline["id"])
        refused = await client.post(
            f"/baselines/{baseline['id']}/restore",
            json={"dry_run": False, "expected_digest": read["digest"]},
            headers=as_(EDITOR),
        )

        assert [b["label"] for b in read["plan"]["blocked"]] == ["late"]
        assert read["plan"]["blocked_count"] == 1
        assert refused.status_code == 409
        assert refused.json()["error"] == "restore_blocked"

    async def test_a_reader_cannot_restore_and_unknown_fields_are_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        baseline = await capture(client)
        url = f"/baselines/{baseline['id']}/restore"

        assert (await client.post(url, json={}, headers=as_(READER))).status_code == 403
        assert (
            await client.post(url, json={"force": True}, headers=as_(EDITOR))
        ).status_code == 422
        assert (
            await client.post(url, json={"confirm": True}, headers=as_(EDITOR))
        ).status_code == 422
        assert (
            await client.post(f"/baselines/{STRANGER}/restore", json={}, headers=as_(EDITOR))
        ).status_code == 404


class TestASubtreeBaseline:
    async def test_post_baselines_takes_a_package_and_the_list_shows_the_scope(
        self, client: httpx.AsyncClient, baseline_store: InMemoryBaselines
    ) -> None:
        assert baseline_store.packages is not None
        finance = Package.create(name="Finance", now=FIXED_NOW)
        await baseline_store.packages.add(finance, audit=lambda p: ())

        made = await client.post(
            "/baselines",
            json={"name": "Finance", "package_id": str(finance.id)},
            headers=as_(EDITOR),
        )

        assert made.status_code == 201 and made.json()["scope_path"] == "Finance"
        listed = (await client.get("/baselines", headers=as_(EDITOR))).json()
        assert {b["scope_package_id"] for b in listed} >= {str(finance.id)}

    async def test_an_unknown_package_is_a_typed_404(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/baselines", json={"name": "x", "package_id": str(uuid4())}, headers=as_(EDITOR)
        )
        assert (answer.status_code, answer.json()["error"]) == (404, "not_found")

    async def test_a_whole_model_baseline_has_no_scope(self, client: httpx.AsyncClient) -> None:
        made = await capture(client, "Tout")
        assert (made["scope_package_id"], made["scope_path"]) == (None, None)
