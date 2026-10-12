"""What the trail says of a publication: ids, counts and digests, never a text."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ea.domain.audit import AuditAction, EntityKind, package_version_view
from ea.domain.package_versions import Bump, PackageVersion, digest

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def a_version(**overrides: object) -> PackageVersion:
    fields: dict[str, object] = {
        "id": uuid4(),
        "package_id": uuid4(),
        "version": "v1.0.0",
        "bump": Bump.MAJOR,
        "notes": "Retrait de STD-APP-002 — voir l'ADR",
        "adr": "ADR-0042",
        "baseline_id": uuid4(),
        "element_count": 3,
        "relationship_count": 2,
        "box_count": 1,
        "published_by_sub": "approver-1",
        "published_by": "approver",
        "published_at": NOW,
    }
    return PackageVersion(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_three_actions_are_about_a_package() -> None:
    for action in (
        AuditAction.PACKAGE_PUBLISHED,
        AuditAction.PACKAGE_REOPENED,
        AuditAction.PACKAGE_WRITE_REFUSED_FROZEN,
    ):
        assert action.kind is EntityKind.PACKAGE


def test_the_view_keeps_digests_and_never_the_texts() -> None:
    version = a_version(reopen_reason="coquille dans le titre")
    view = package_version_view(version)
    assert view["notes_sha256"] == digest(version.notes)
    assert view["adr_sha256"] == digest("ADR-0042")
    assert view["reopen_reason_sha256"] == digest("coquille dans le titre")
    flat = repr(view)
    assert "STD-APP-002" not in flat
    assert "coquille" not in flat
    assert view["baseline_id"] == str(version.baseline_id)
    assert (view["elements"], view["relationships"], view["boxes"]) == (3, 2, 1)


def test_a_version_with_no_adr_or_reason_says_none() -> None:
    view = package_version_view(a_version(adr=None))
    assert view["adr_sha256"] is None
    assert view["reopen_reason_sha256"] is None
