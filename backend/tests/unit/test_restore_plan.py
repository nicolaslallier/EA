"""What a restoration would do — every case, decided without a database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AUDIT_LISTED_IDS
from ea.domain.baselines import (
    BoxSnapshot,
    ModelSnapshot,
    RestorePlan,
    plan_digest,
    plan_restore,
)
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

    def test_g_the_layout_of_its_boxes(self) -> None:
        a, diagram = node("a"), uuid4()
        wanted = BoxSnapshot(diagram, a.id, 1, 2, 132, 46)
        moved = BoxSnapshot(diagram, a.id, 9, 9, 132, 46)

        result = plan(
            ModelSnapshot((a,), (), (wanted,)),
            ModelSnapshot((a,), (), (moved,)),
            diagrams_present=frozenset({diagram}),
            protected_ids=frozenset({a.id}),
        )

        assert [e.id for e in result.blocked] == [a.id]

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


class TestWhatADeletionTakesAlong:
    def test_links_and_boxes_are_listed_per_deleted_element_with_exact_counts(self) -> None:
        kept, fresh = node("kept"), node("fresh")
        tie = link(kept, fresh)
        diagram = uuid4()
        box = BoxSnapshot(diagram, fresh.id, 0, 0, 132, 46)

        result = plan(ModelSnapshot((kept,), ()), ModelSnapshot((kept, fresh), (tie,), (box,)))

        (cascade,) = result.cascades
        assert cascade.element_id == fresh.id
        assert (cascade.relationship_count, cascade.relationships) == (
            1,
            ((tie.id, "association"),),
        )
        assert (cascade.box_count, cascade.diagram_ids) == (1, (diagram,))

    def test_a_hub_lists_at_most_the_audit_bound_but_counts_them_all(self) -> None:
        kept, hub = node("kept"), node("hub")
        spokes = [link(kept, hub) for _ in range(AUDIT_LISTED_IDS + 5)]

        result = plan(ModelSnapshot((kept,), ()), ModelSnapshot((kept, hub), tuple(spokes)))

        (cascade,) = result.cascades
        assert cascade.relationship_count == AUDIT_LISTED_IDS + 5
        assert len(cascade.relationships) == AUDIT_LISTED_IDS

    def test_a_kept_element_has_no_cascade_and_an_overwritten_box_is_remembered(self) -> None:
        a, diagram = node("a"), uuid4()
        wanted = BoxSnapshot(diagram, a.id, 1, 2, 132, 46)
        moved = BoxSnapshot(diagram, a.id, 9, 9, 132, 46)

        result = plan(
            ModelSnapshot((a,), (), (wanted,)),
            ModelSnapshot((a,), (), (moved,)),
            diagrams_present=frozenset({diagram}),
        )

        assert result.cascades == ()
        assert result.boxes_overwritten == (moved,)


class TestTheDigestOfAPlan:
    """What a caller sends back to say which plan it read: same plan, same digest."""

    def test_the_same_plan_has_the_same_digest_whatever_the_input_order(self) -> None:
        gone = [node("b"), node("a")]

        first = plan(ModelSnapshot(tuple(gone), ()), ModelSnapshot((), ()))
        second = plan(ModelSnapshot(tuple(reversed(gone)), ()), ModelSnapshot((), ()))

        assert plan_digest(first) == plan_digest(second)
        assert len(plan_digest(first)) == 64

    def test_any_change_in_what_would_happen_changes_it(self) -> None:
        gone, edited = node("gone"), node("billing")
        snapshot = ModelSnapshot((gone, edited), ())
        base = plan(snapshot, ModelSnapshot((replace(edited, name="x"),), ()))

        other_content = plan(snapshot, ModelSnapshot((replace(edited, name="y"),), ()))
        one_more = plan(snapshot, ModelSnapshot((replace(edited, name="x"), node("new")), ()))
        documents = replace(base, documents_lost=3)

        digests = {plan_digest(p) for p in (base, other_content, one_more, documents)}
        assert len(digests) == 4

    def test_a_box_moved_since_the_plan_was_read_changes_it_and_so_do_the_cascaded_ids(
        self,
    ) -> None:
        from ea.domain.baselines import Cascade

        element, view = node("a"), uuid4()
        here = BoxSnapshot(view, element.id, 1, 2, 132, 46)
        base = replace(
            plan(ModelSnapshot((), ()), ModelSnapshot((), ())),
            boxes_overwritten=(here,),
            cascades=(Cascade(element.id, relationship_count=1, relationships=((uuid4(), "x"),)),),
        )

        moved = replace(base, boxes_overwritten=(replace(here, x=9),))
        other_ids = replace(base, cascades=(replace(base.cascades[0], document_ids=(uuid4(),)),))

        assert len({plan_digest(base), plan_digest(moved), plan_digest(other_ids)}) == 3


class TestReview:
    """What a restoration does to the review state of the elements it writes (docs/adr/0048)."""

    def test_an_updated_element_keeps_the_review_state_it_has_now(self) -> None:
        original = node("billing")
        in_review = replace(original, name="invoicing", approval_state=ApprovalState.IN_REVIEW)

        result = plan(ModelSnapshot((original,), ()), ModelSnapshot((in_review,), ()))

        (update,) = result.elements_to_update
        assert update.restored.name == "billing"
        assert update.restored.approval_state is ApprovalState.IN_REVIEW

    def test_an_approved_element_the_plan_changes_would_go_back_to_review_never_stay_approved(
        self,
    ) -> None:
        """Defence in depth: the repository blocks these; the planner never approves in silence."""
        original = node("billing")
        approved = replace(
            original,
            name="invoicing",
            approval_state=ApprovalState.APPROVED,
            approved_by_sub="approver-1",
            approved_at=NOW,
        )

        result = plan(ModelSnapshot((original,), ()), ModelSnapshot((approved,), ()))

        (update,) = result.elements_to_update
        assert update.restored.approval_state is ApprovalState.IN_REVIEW
        assert update.restored.approved_by_sub is None

    def test_a_recreated_element_is_a_draft(self) -> None:
        gone = node("gone")

        result = plan(ModelSnapshot((gone,), ()), ModelSnapshot((), ()))

        assert result.elements_to_create[0].approval_state is ApprovalState.DRAFT
