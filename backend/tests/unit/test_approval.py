"""Who may move an element through review, and what review forbids (docs/adr/0046)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.approval import Transition as Tr
from ea.domain.approval import (
    allowed_transitions,
    apply_transition,
    check_modifiable,
    check_untouched,
    reviewed_after_change,
)
from ea.domain.archimate import ElementType as E
from ea.domain.auth import APPROVER_ROLE, LOCAL_DEVELOPER, SYSTEM, Caller
from ea.domain.errors import ElementLockedError, InvalidTransitionError, NotAuthorisedError
from ea.domain.model import Element
from tests.conftest import a_reader, an_approver, an_editor

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def an_element(state: S = S.DRAFT, locked: bool = False) -> Element:
    element = Element.create(element_type=E.NODE, name="db-01", now=NOW)
    approved = state is S.APPROVED
    return replace(
        element,
        approval_state=state,
        locked=locked,
        approved_by_sub="approver-1" if approved else None,
        approved_at=NOW if approved else None,
    )


def an_approver_alone() -> Caller:
    return Caller("approver-2", "approver-only", frozenset({APPROVER_ROLE}))


class TestTheRoles:
    def test_the_approver_role_is_its_own_and_cumulates_with_the_editor_role(self) -> None:
        assert APPROVER_ROLE == "ea-approver"
        assert an_approver().can_write and an_approver().can_approve
        assert not an_editor().can_approve
        assert an_approver_alone().can_approve
        assert not an_approver_alone().can_write  # not implied
        assert LOCAL_DEVELOPER.can_approve and SYSTEM.can_approve

    def test_a_new_element_is_a_draft_nobody_has_approved(self) -> None:
        element = an_element()

        assert (element.approval_state, element.locked) == (S.DRAFT, False)
        assert element.approved_by_sub is None and element.approved_at is None
        assert element.rename("other", now=LATER).approval_state is S.DRAFT


#: What each state offers, and what each role may take — written out, not derived.
OFFERED = {
    (S.DRAFT, False): {Tr.SUBMIT},
    (S.IN_REVIEW, False): {Tr.APPROVE, Tr.REJECT},
    (S.APPROVED, False): {Tr.REOPEN, Tr.LOCK},
    (S.APPROVED, True): {Tr.UNLOCK},
}
ROLES = {
    "reader": (a_reader, set()),
    "editor": (an_editor, {Tr.SUBMIT}),
    "approver_alone": (an_approver_alone, set(Tr) - {Tr.SUBMIT}),
    "editor_and_approver": (an_approver, set(Tr)),
}


class TestEveryStateEveryTransitionEveryRole:
    @pytest.mark.parametrize("role", list(ROLES))
    @pytest.mark.parametrize("transition", list(Tr))
    @pytest.mark.parametrize(("state", "locked"), list(OFFERED))
    def test_the_whole_table(self, state: S, locked: bool, transition: Tr, role: str) -> None:
        make_caller, may = ROLES[role]
        caller = make_caller()
        element = an_element(state, locked)

        if transition not in may:
            with pytest.raises(NotAuthorisedError):  # the role first, whatever the state
                apply_transition(element, transition, caller, now=LATER)
        elif transition not in OFFERED[(state, locked)]:
            with pytest.raises(InvalidTransitionError):
                apply_transition(element, transition, caller, now=LATER)
        else:
            assert (
                apply_transition(element, transition, caller, now=LATER, last_author="editor-1")
                != element
            )

    @pytest.mark.parametrize("role", list(ROLES))
    @pytest.mark.parametrize(("state", "locked"), list(OFFERED))
    def test_what_is_offered_is_what_is_applicable(self, state: S, locked: bool, role: str) -> None:
        make_caller, may = ROLES[role]
        offered = allowed_transitions(state, locked, make_caller())

        assert set(offered) == OFFERED[(state, locked)] & may


class TestApplyingATransition:
    def test_submit_then_approve_records_who_and_when(self) -> None:
        submitted = apply_transition(an_element(), Tr.SUBMIT, an_editor(), now=LATER)
        approved = apply_transition(
            submitted, Tr.APPROVE, an_approver(), now=LATER, last_author="editor-1"
        )

        assert submitted.approval_state is S.IN_REVIEW
        assert approved.approval_state is S.APPROVED
        assert (approved.approved_by_sub, approved.approved_at) == ("approver-1", LATER)

    def test_reopening_forgets_the_approval_and_the_lock(self) -> None:
        unlocked = apply_transition(
            an_element(S.APPROVED, locked=True), Tr.UNLOCK, an_approver(), now=LATER
        )
        draft = apply_transition(unlocked, Tr.REOPEN, an_approver(), now=LATER)

        assert unlocked.locked is False and unlocked.approval_state is S.APPROVED
        assert draft.approval_state is S.DRAFT
        assert draft.approved_by_sub is None and draft.approved_at is None

    def test_lock_and_reject(self) -> None:
        locked = apply_transition(an_element(S.APPROVED), Tr.LOCK, an_approver(), now=LATER)
        rejected = apply_transition(an_element(S.IN_REVIEW), Tr.REJECT, an_approver(), now=LATER)

        assert locked.locked is True
        assert rejected.approval_state is S.DRAFT


class TestSeparationOfDuties:
    """Fail-closed: an unknown last author never lets the rule be skipped."""

    @pytest.mark.parametrize("last_author", [None, "", "approver-1"])
    def test_an_unknown_or_identical_last_author_refuses_the_approval(
        self, last_author: str | None
    ) -> None:
        with pytest.raises(NotAuthorisedError, match="separation"):
            apply_transition(
                an_element(S.IN_REVIEW),
                Tr.APPROVE,
                an_approver(),
                now=LATER,
                last_author=last_author,
            )

    def test_forgetting_the_argument_is_a_refusal_not_a_pass(self) -> None:
        with pytest.raises(NotAuthorisedError, match="separation"):
            apply_transition(an_element(S.IN_REVIEW), Tr.APPROVE, an_approver(), now=LATER)

    def test_a_different_subject_approves(self) -> None:
        approved = apply_transition(
            an_element(S.IN_REVIEW), Tr.APPROVE, an_approver(), now=LATER, last_author="editor-1"
        )

        assert approved.approval_state is S.APPROVED

    def test_only_the_subject_counts_not_the_name_shown(self) -> None:
        other_name_same_subject = an_approver(username="somebody else")
        with pytest.raises(NotAuthorisedError, match="separation"):
            apply_transition(
                an_element(S.IN_REVIEW),
                Tr.APPROVE,
                other_name_same_subject,
                now=LATER,
                last_author="approver-1",
            )
        same_name_other_subject = an_approver(subject="approver-9")
        apply_transition(
            an_element(S.IN_REVIEW),
            Tr.APPROVE,
            same_name_other_subject,
            now=LATER,
            last_author="approver-1",
        )

    def test_switching_the_rule_off_is_explicit_and_even_an_unknown_author_passes(self) -> None:
        for last_author in (None, "approver-1"):
            approved = apply_transition(
                an_element(S.IN_REVIEW),
                Tr.APPROVE,
                an_approver(),
                now=LATER,
                last_author=last_author,
                separate_duties=False,
            )
            assert approved.approval_state is S.APPROVED

    def test_it_binds_approval_only(self) -> None:
        rejected = apply_transition(
            an_element(S.IN_REVIEW), Tr.REJECT, an_approver(), now=LATER, last_author=None
        )

        assert rejected.approval_state is S.DRAFT


class TestWhatReviewForbids:
    def test_a_draft_and_an_element_in_review_stay_editable(self) -> None:
        check_modifiable(an_element(S.DRAFT), an_editor())
        check_modifiable(an_element(S.IN_REVIEW), an_editor())

    def test_an_approved_element_is_for_approvers_only(self) -> None:
        with pytest.raises(ElementLockedError, match="approved"):
            check_modifiable(an_element(S.APPROVED), an_editor())
        check_modifiable(an_element(S.APPROVED), an_approver())

    def test_a_locked_element_is_for_nobody(self) -> None:
        with pytest.raises(ElementLockedError, match="locked"):
            check_modifiable(an_element(S.APPROVED, locked=True), an_approver())

    def test_a_bulk_operation_leaves_approved_and_locked_elements_alone_whoever_asks(self) -> None:
        check_untouched(an_element(S.IN_REVIEW))
        for element in (an_element(S.APPROVED), an_element(S.APPROVED, locked=True)):
            with pytest.raises(ElementLockedError, match="reopen"):
                check_untouched(element)

    def test_changing_an_approved_element_sends_it_back_to_review(self) -> None:
        back = reviewed_after_change(an_element(S.APPROVED))

        assert back.approval_state is S.IN_REVIEW
        assert back.approved_by_sub is None and back.approved_at is None
        draft = an_element(S.DRAFT)
        assert reviewed_after_change(draft) is draft
