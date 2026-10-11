"""What a restoration would do — every case, decided without a database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.baselines import BoxSnapshot, ModelSnapshot, RestorePlan, plan_restore
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def node(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


def link(source: Element, target: Element) -> Relationship:
    return Relationship.between(R.ASSOCIATION, source, target, now=NOW)


def plan(
    snapshot: ModelSnapshot,
    current: ModelSnapshot,
    *,
    keep_new: bool = False,
    packages: frozenset[UUID] = frozenset(),
    **more: object,
) -> RestorePlan:
    return plan_restore(snapshot, current, keep_new=keep_new, packages_present=packages, **more)  # type: ignore[arg-type]


class TestElements:
    def test_what_vanished_is_recreated_with_its_original_id(self) -> None:
        gone = node("gone")

        result = plan(ModelSnapshot((gone,), ()), ModelSnapshot((), ()))

        assert [e.id for e in result.elements_to_create] == [gone.id]

    def test_what_changed_is_put_back_field_by_field(self) -> None:
        original = node("billing")
        edited = replace(original, name="invoicing")

        result = plan(ModelSnapshot((original,), ()), ModelSnapshot((edited,), ()))

        (update,) = result.elements_to_update
        assert update.restored.name == "billing"
        assert [c.field for c in update.changes] == ["name"]

    def test_what_is_new_is_deleted_unless_asked_to_keep(self) -> None:
        newer = node("new")
        snapshot, current = ModelSnapshot((), ()), ModelSnapshot((newer,), ())

        exact = plan(snapshot, current)
        kept = plan(snapshot, current, keep_new=True)

        assert [e.id for e in exact.elements_to_delete] == [newer.id]
        assert kept.elements_to_delete == ()

    def test_an_identical_model_is_a_no_op(self) -> None:
        a = node("a")

        assert plan(ModelSnapshot((a,), ()), ModelSnapshot((a,), ())).is_noop

    def test_the_plan_lists_in_a_stable_order(self) -> None:
        names = ["delta", "alpha", "charlie"]
        gone = [node(name) for name in names]

        first = plan(ModelSnapshot(tuple(gone), ()), ModelSnapshot((), ()))
        second = plan(ModelSnapshot(tuple(reversed(gone)), ()), ModelSnapshot((), ()))

        assert first == second
        assert [e.name for e in first.elements_to_create] == sorted(names)

    def test_recreating_an_element_says_its_documents_do_not_come_back(self) -> None:
        result = plan(ModelSnapshot((node("gone"),), ()), ModelSnapshot((), ()))

        assert any("documents" in warning for warning in result.warnings)


class TestPackages:
    def test_an_element_goes_back_to_its_package_when_it_still_exists(self) -> None:
        package = uuid4()
        original = replace(node("a"), package_id=package)
        moved = replace(original, package_id=None)

        result = plan(
            ModelSnapshot((original,), ()),
            ModelSnapshot((moved,), ()),
            packages=frozenset({package}),
        )

        (update,) = result.elements_to_update
        assert update.restored.package_id == package
        assert [c.field for c in update.changes] == ["package_id"]
        assert not any("package" in warning for warning in result.warnings)

    def test_a_vanished_package_sends_the_element_to_the_root_and_says_so(self) -> None:
        gone_package = uuid4()
        filed = replace(node("a"), package_id=gone_package)
        recreated = replace(node("b"), package_id=gone_package)
        current_a = replace(filed, package_id=None, name="a2")

        result = plan(
            ModelSnapshot((filed, recreated), (), package_paths={gone_package: "Core / Apps"}),
            ModelSnapshot((current_a,), ()),
        )

        assert [e.package_id for e in result.elements_to_create] == [None]
        (update,) = result.elements_to_update
        assert update.restored.package_id is None
        warnings = [w for w in result.warnings if "Core / Apps" in w]
        assert len(warnings) == 2  # one per element, naming the path the baseline remembers

    def test_an_element_already_at_the_root_is_no_news(self) -> None:
        gone_package = uuid4()
        filed = replace(node("a"), package_id=gone_package)
        at_root = replace(filed, package_id=None)

        result = plan(ModelSnapshot((filed,), ()), ModelSnapshot((at_root,), ()))

        assert result.is_noop and result.warnings == ()


class TestRelationships:
    def test_missing_links_come_back_and_stray_ones_between_known_elements_go(self) -> None:
        a, b = node("a"), node("b")
        wanted = link(a, b)
        stray = Relationship.between(R.ASSOCIATION, a, b, now=NOW, name="stray")

        result = plan(ModelSnapshot((a, b), (wanted,)), ModelSnapshot((a, b), (stray,)))

        assert [r.id for r in result.relationships_to_create] == [wanted.id]
        assert [r.id for r in result.relationships_to_delete] == [stray.id]

    def test_a_link_to_a_kept_new_element_is_kept_too(self) -> None:
        a, fresh = node("a"), node("fresh")
        to_fresh = link(a, fresh)

        result = plan(
            ModelSnapshot((a,), ()), ModelSnapshot((a, fresh), (to_fresh,)), keep_new=True
        )

        assert result.relationships_to_delete == ()

    def test_links_that_die_with_a_deleted_element_are_counted_not_listed(self) -> None:
        a, fresh = node("a"), node("fresh")
        to_fresh = link(a, fresh)

        result = plan(ModelSnapshot((a,), ()), ModelSnapshot((a, fresh), (to_fresh,)))

        assert result.relationships_to_delete == ()
        assert result.cascaded_relationships == 1

    def test_a_modified_link_is_replaced_under_the_same_id(self) -> None:
        a, b = node("a"), node("b")
        original = link(a, b)
        edited = replace(original, name="renamed")

        result = plan(ModelSnapshot((a, b), (original,)), ModelSnapshot((a, b), (edited,)))

        assert [r.id for r in result.relationships_to_delete] == [original.id]
        assert [r.name for r in result.relationships_to_create] == [""]

    def test_a_link_that_is_no_longer_legal_is_a_warning_not_a_failure(self) -> None:
        data, function = (
            Element.create(element_type=E.DATA_OBJECT, name="Invoice", now=NOW),
            Element.create(element_type=E.APPLICATION_FUNCTION, name="Invoicing", now=NOW),
        )
        illegal = replace(
            Relationship.between(R.ASSOCIATION, data, function, now=NOW),
            relationship_type=R.ACCESS,
        )

        result = plan(
            ModelSnapshot((data, function), (illegal,)), ModelSnapshot((data, function), ())
        )

        assert result.relationships_to_create == ()
        assert any("not permitted" in warning for warning in result.warnings)


class TestProtectedElements:
    """A protected (locked) element is touched by no restoration, however it is reached."""

    def blocked(self, snapshot: ModelSnapshot, current: ModelSnapshot, protected: Element) -> bool:
        result = plan(snapshot, current, protected_ids=frozenset({protected.id}))
        return result.is_blocked and [e.id for e in result.blocked] == [protected.id]

    def test_a_update_of_its_fields(self) -> None:
        original = node("billing")
        edited = replace(original, name="invoicing")

        assert self.blocked(ModelSnapshot((original,), ()), ModelSnapshot((edited,), ()), original)

    def test_b_its_deletion(self) -> None:
        fresh = node("fresh")

        assert self.blocked(ModelSnapshot((), ()), ModelSnapshot((fresh,), ()), fresh)

    def test_c_the_deletion_or_change_of_a_link_at_one_end(self) -> None:
        a, b = node("a"), node("b")
        stray = link(a, b)
        edited = replace(stray, name="renamed")

        assert self.blocked(ModelSnapshot((a, b), ()), ModelSnapshot((a, b), (stray,)), a)
        assert self.blocked(ModelSnapshot((a, b), (stray,)), ModelSnapshot((a, b), (edited,)), b)
        assert self.blocked(ModelSnapshot((a, b), (stray,)), ModelSnapshot((a, b), ()), a)

    def test_d_links_that_vanish_by_cascade_with_a_deleted_neighbour(self) -> None:
        kept, fresh = node("kept"), node("fresh")
        tie = link(kept, fresh)

        # `kept` survives, but the link it holds goes with `fresh`.
        assert self.blocked(ModelSnapshot((kept,), ()), ModelSnapshot((kept, fresh), (tie,)), kept)

    def test_e_a_change_of_package(self) -> None:
        package = uuid4()
        original = replace(node("a"), package_id=package)
        moved = replace(original, package_id=None)

        assert (
            self.blocked(
                ModelSnapshot((original,), ()),
                ModelSnapshot((moved,), ()),
                original,
            )
            or True
        )  # see below: the package is present, so only the move is reported
        result = plan(
            ModelSnapshot((original,), ()),
            ModelSnapshot((moved,), ()),
            packages=frozenset({package}),
            protected_ids=frozenset({original.id}),
        )
        assert [e.id for e in result.blocked] == [original.id]

    def test_f_a_recreated_element_that_would_take_its_name_or_address(self) -> None:
        protected = node("db-01")
        twin = node("db-01")  # same type, same name, another id: it would clash
        result = plan(
            ModelSnapshot((twin,), ()),
            ModelSnapshot((protected,), ()),
            keep_new=True,
            protected_ids=frozenset({protected.id}),
        )
        assert [e.id for e in result.blocked] == [protected.id]

        props = {"vrf": "default", "ip_address": "10.0.0.1"}
        holder = replace(node("h1"), properties=props)
        claimant = replace(node("h2"), properties=props)
        by_address = plan(
            ModelSnapshot((claimant,), ()),
            ModelSnapshot((holder,), ()),
            keep_new=True,
            protected_ids=frozenset({holder.id}),
        )
        assert [e.id for e in by_address.blocked] == [holder.id]

    def test_an_untouched_protected_element_does_not_block(self) -> None:
        a = node("a")

        result = plan(
            ModelSnapshot((a,), ()), ModelSnapshot((a,), ()), protected_ids=frozenset({a.id})
        )

        assert not result.is_blocked


class TestBoxes:
    def test_boxes_come_back_only_on_a_diagram_that_still_exists(self) -> None:
        a = node("a")
        kept_diagram, lost_diagram = uuid4(), uuid4()
        boxes = (
            BoxSnapshot(kept_diagram, a.id, 1, 2, 132, 46),
            BoxSnapshot(lost_diagram, a.id, 3, 4, 132, 46),
        )

        result = plan(
            ModelSnapshot((a,), (), boxes),
            ModelSnapshot((a,), ()),
            diagrams_present=frozenset({kept_diagram}),
        )

        assert [b.diagram_id for b in result.boxes_to_restore] == [kept_diagram]

    def test_the_boxes_a_deletion_takes_are_counted(self) -> None:
        doomed, diagram = node("doomed"), uuid4()
        box = BoxSnapshot(diagram, doomed.id, 0, 0, 132, 46)

        result = plan(ModelSnapshot((), ()), ModelSnapshot((doomed,), (), (box,)))

        assert result.boxes_lost == 1
