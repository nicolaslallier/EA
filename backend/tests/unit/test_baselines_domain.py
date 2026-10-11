"""A snapshot of the model, and what separates two of them (docs/adr/0047)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, EntityKind, FieldChange
from ea.domain.baselines import (
    MAX_BASELINE_DESCRIPTION_LENGTH,
    MAX_BASELINE_NAME_LENGTH,
    Baseline,
    BaselineLimits,
    ModelSize,
    ModelSnapshot,
    diff_snapshots,
    refuse_oversize,
)
from ea.domain.errors import BaselineTooLargeError
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(name: str, **overrides: object) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW, **overrides)  # type: ignore[arg-type]


def a_baseline(**overrides: str) -> Baseline:
    fields = {"name": "v1", "description": "", "created_by_sub": "s", "created_by": "n"}
    return Baseline.create(now=NOW, **{**fields, **overrides})


class TestABaseline:
    def test_a_name_is_trimmed_and_cannot_be_blank(self) -> None:
        assert a_baseline(name="  Avant migration ").name == "Avant migration"
        with pytest.raises(ValueError, match="blank"):
            a_baseline(name="  ")

    def test_text_is_bounded_and_refuses_a_nul(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            a_baseline(name="x" * (MAX_BASELINE_NAME_LENGTH + 1))
        with pytest.raises(ValueError, match="at most"):
            a_baseline(description="x" * (MAX_BASELINE_DESCRIPTION_LENGTH + 1))
        with pytest.raises(ValueError, match="NUL"):
            a_baseline(name="a\x00b")
        with pytest.raises(ValueError, match="NUL"):
            a_baseline(description="a\x00b")

    def test_the_actions_on_baselines_are_about_baselines(self) -> None:
        assert AuditAction.BASELINE_RESTORED.kind is EntityKind.BASELINE
        assert AuditAction.BASELINE_CREATED.value == "baseline.created"
        assert AuditAction.BASELINE_DELETED.kind is EntityKind.BASELINE


class TestTheDiff:
    def test_what_appeared_vanished_or_changed(self) -> None:
        kept, gone, changed = an_element("kept"), an_element("gone"), an_element("billing")
        new = an_element("new")
        older = ModelSnapshot(elements=(kept, gone, changed), relationships=())
        renamed = replace(changed, name="invoicing", properties={"owner": "ops"})
        newer = ModelSnapshot(elements=(kept, renamed, new), relationships=())

        diff = diff_snapshots(older, newer)

        assert [e.label for e in diff.elements_added] == ["new"]
        assert [e.label for e in diff.elements_removed] == ["gone"]
        (modified,) = diff.elements_modified
        assert modified.id == changed.id
        assert modified.changes == (
            FieldChange("name", "billing", "invoicing"),
            FieldChange("properties.owner", None, "ops"),
        )

    def test_a_timestamp_alone_is_not_a_modification(self) -> None:
        element = an_element("a")
        touched = replace(element, updated_at=NOW + timedelta(days=1))

        diff = diff_snapshots(ModelSnapshot((element,), ()), ModelSnapshot((touched,), ()))

        assert diff.elements_modified == ()

    def test_relationships_are_labelled_with_the_names_of_their_ends(self) -> None:
        left, right = an_element("a"), an_element("b")
        link = Relationship.between(R.ASSOCIATION, left, right, now=NOW)
        older = ModelSnapshot((left, right), ())
        newer = ModelSnapshot((left, right), (link,))

        diff = diff_snapshots(older, newer)

        (added,) = diff.relationships_added
        assert (added.kind, added.type) == ("relationship", "association")
        assert "a" in added.label and "b" in added.label
        assert diff_snapshots(newer, older).relationships_removed[0].id == link.id

    def test_a_link_renamed_or_redirected_is_a_modification_with_its_fields(self) -> None:
        left, right = an_element("a"), an_element("b")
        link = Relationship.between(R.ASSOCIATION, left, right, now=NOW)
        edited = replace(link, name="uses", directed=True)

        diff = diff_snapshots(
            ModelSnapshot((left, right), (link,)), ModelSnapshot((left, right), (edited,))
        )

        (modified,) = diff.relationships_modified
        assert modified.id == link.id
        assert modified.changes == (
            FieldChange("directed", False, True),
            FieldChange("name", "", "uses"),
        )
        assert not diff.is_empty

    def test_two_equal_snapshots_have_an_empty_diff(self) -> None:
        snapshot = ModelSnapshot((an_element("a"),), ())

        assert diff_snapshots(snapshot, snapshot).is_empty

    def test_the_order_of_the_input_changes_nothing(self) -> None:
        names = ["delta", "alpha", "charlie", "bravo"]
        elements = tuple(an_element(name) for name in names)
        empty = ModelSnapshot((), ())

        forward = diff_snapshots(empty, ModelSnapshot(elements, ()))
        backward = diff_snapshots(empty, ModelSnapshot(tuple(reversed(elements)), ()))

        assert forward == backward
        assert [e.label for e in forward.elements_added] == sorted(names)

    def test_a_move_between_packages_is_read_with_the_path_not_the_id(self) -> None:
        element = an_element("a")
        old_package, new_package = uuid4(), uuid4()
        older = ModelSnapshot(
            (replace(element, package_id=old_package),),
            (),
            package_paths={old_package: "Core / Apps"},
        )
        newer = ModelSnapshot(
            (replace(element, package_id=new_package),), (), package_paths={new_package: "Edge"}
        )
        at_root = ModelSnapshot((element,), ())

        (moved,) = diff_snapshots(older, newer).elements_modified
        (rooted,) = diff_snapshots(older, at_root).elements_modified

        assert moved.changes == (FieldChange("package", "Core / Apps", "Edge"),)
        assert rooted.changes == (FieldChange("package", "Core / Apps", "(root)"),)

    def test_renaming_a_package_is_not_a_change_of_its_elements(self) -> None:
        package = uuid4()
        element = replace(an_element("a"), package_id=package)
        older = ModelSnapshot((element,), (), package_paths={package: "Old"})
        newer = ModelSnapshot((element,), (), package_paths={package: "New"})

        assert diff_snapshots(older, newer).is_empty


class TestTheBound:
    """One bound for every kind of row a baseline holds, declared once."""

    LIMITS = BaselineLimits(elements=10, relationships=20, boxes=30, diagrams=40, bytes=1000)

    @pytest.mark.parametrize("field", ["elements", "relationships", "boxes", "diagrams", "bytes"])
    def test_each_kind_is_refused_one_row_past_its_own_limit(self, field: str) -> None:
        at_the_limit = ModelSize(10, 20, 30, 40, 1000)
        refuse_oversize(at_the_limit, self.LIMITS)

        with pytest.raises(BaselineTooLargeError, match=field):
            refuse_oversize(
                replace(at_the_limit, **{field: getattr(at_the_limit, field) + 1}), self.LIMITS
            )
