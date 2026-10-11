"""Who may move an element through review, and what review forbids (docs/adr/0046)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.approval import Transition as Tr
from ea.domain.approval import (
    WriteKind,
    allowed_transitions,
    apply_transition,
    guard_all,
    guard_element_write,
    reviewed_after_change,
)
from ea.domain.archimate import ElementType as E
from ea.domain.auth import APPROVER_ROLE, LOCAL_DEVELOPER, SYSTEM, Caller
from ea.domain.errors import (
    ApprovalStaleError,
    ElementLockedError,
    InvalidTransitionError,
    NotAuthorisedError,
)
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
                apply_transition(
                    element,
                    transition,
                    caller,
                    now=LATER,
                    last_author="editor-1",
                    expected_revision=element.revision,
                )
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
            submitted,
            Tr.APPROVE,
            an_approver(),
            now=LATER,
            last_author="editor-1",
            expected_revision=submitted.revision,
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
                expected_revision=1,
                last_author=last_author,
            )

    def test_forgetting_the_argument_is_a_refusal_not_a_pass(self) -> None:
        with pytest.raises(NotAuthorisedError, match="separation"):
            apply_transition(
                an_element(S.IN_REVIEW), Tr.APPROVE, an_approver(), now=LATER, expected_revision=1
            )

    def test_a_different_subject_approves(self) -> None:
        approved = apply_transition(
            an_element(S.IN_REVIEW),
            Tr.APPROVE,
            an_approver(),
            now=LATER,
            expected_revision=1,
            last_author="editor-1",
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
                expected_revision=1,
                last_author="approver-1",
            )
        same_name_other_subject = an_approver(subject="approver-9")
        apply_transition(
            an_element(S.IN_REVIEW),
            Tr.APPROVE,
            same_name_other_subject,
            now=LATER,
            expected_revision=1,
            last_author="approver-1",
        )

    def test_switching_the_rule_off_is_explicit_and_even_an_unknown_author_passes(self) -> None:
        for last_author in (None, "approver-1"):
            approved = apply_transition(
                an_element(S.IN_REVIEW),
                Tr.APPROVE,
                an_approver(),
                now=LATER,
                expected_revision=1,
                last_author=last_author,
                separate_duties=False,
            )
            assert approved.approval_state is S.APPROVED

    def test_it_binds_approval_only(self) -> None:
        rejected = apply_transition(
            an_element(S.IN_REVIEW), Tr.REJECT, an_approver(), now=LATER, last_author=None
        )

        assert rejected.approval_state is S.DRAFT


#: What each kind of write may do to an element in each state — written out, not derived.
#: ok / "approver" (only a caller with the approver role) / no (refused to everyone).
WRITES = {
    "draft": dict.fromkeys(WriteKind, "ok"),
    "in_review": dict.fromkeys(WriteKind, "ok"),
    "approved": {
        WriteKind.CONTENT: "approver",
        WriteKind.LINK: "ok",
        WriteKind.DOCUMENT: "no",
        WriteKind.FILING: "no",
        WriteKind.RESTORE: "no",
    },
    "locked": dict.fromkeys(WriteKind, "no"),
}


def element_in(name: str) -> Element:
    return {
        "draft": an_element(S.DRAFT),
        "in_review": an_element(S.IN_REVIEW),
        "approved": an_element(S.APPROVED),
        "locked": an_element(S.APPROVED, locked=True),
    }[name]


class TestWhatReviewForbids:
    @pytest.mark.parametrize("kind", list(WriteKind))
    @pytest.mark.parametrize("state", list(WRITES))
    @pytest.mark.parametrize("who", ["editor", "approver", "nobody"])
    def test_the_whole_table_of_writes(self, state: str, kind: WriteKind, who: str) -> None:
        caller = {"editor": an_editor(), "approver": an_approver(), "nobody": None}[who]
        verdict = WRITES[state][kind]
        allowed = verdict == "ok" or (verdict == "approver" and who == "approver")

        if allowed:
            guard_element_write(element_in(state), kind, caller)
        else:
            with pytest.raises(ElementLockedError):
                guard_element_write(element_in(state), kind, caller)

    def test_the_refusal_says_what_to_do(self) -> None:
        with pytest.raises(ElementLockedError, match="unlock"):
            guard_element_write(element_in("locked"), WriteKind.CONTENT, an_approver())
        with pytest.raises(ElementLockedError, match="reopen"):
            guard_element_write(element_in("approved"), WriteKind.DOCUMENT)
        with pytest.raises(ElementLockedError, match="approver"):
            guard_element_write(element_in("approved"), WriteKind.CONTENT, an_editor())

    def test_a_batch_is_refused_whole_with_an_exact_count_and_a_bounded_list(self) -> None:
        batch = [replace(an_element(S.APPROVED), name=f"n{i:02d}") for i in range(30)]
        batch.append(an_element(S.DRAFT))

        with pytest.raises(ElementLockedError, match=r"30 element\(s\)") as raised:
            guard_all(batch, WriteKind.FILING)

        assert "n19" in str(raised.value) and "n20" not in str(raised.value)
        assert "and 10 more" in str(raised.value)
        guard_all([an_element(S.DRAFT), an_element(S.IN_REVIEW)], WriteKind.FILING)

    def test_a_sample_of_a_longer_list_keeps_the_true_total(self) -> None:
        sample = [replace(an_element(S.APPROVED), name="a")]

        with pytest.raises(ElementLockedError, match=r"500 element\(s\)"):
            guard_all(sample, WriteKind.FILING, total=500)

    def test_changing_an_approved_element_sends_it_back_to_review(self) -> None:
        back = reviewed_after_change(an_element(S.APPROVED))

        assert back.approval_state is S.IN_REVIEW
        assert back.approved_by_sub is None and back.approved_at is None
        draft = an_element(S.DRAFT)
        assert reviewed_after_change(draft) is draft


class TestEverythingNotListedIsRefused:
    """No default is permissive: an unknown state, role or transition is a refusal."""

    def bogus_state(self) -> Element:
        return replace(an_element(), approval_state="bogus")  # type: ignore[arg-type]

    def test_an_unknown_state_offers_nothing_and_accepts_no_transition(self) -> None:
        assert allowed_transitions("bogus", False, an_approver()) == ()  # type: ignore[arg-type]
        for transition in Tr:
            with pytest.raises((InvalidTransitionError, NotAuthorisedError)):
                apply_transition(
                    self.bogus_state(), transition, an_approver(), now=LATER, last_author="x"
                )

    def test_an_unknown_state_is_not_modifiable_by_anyone(self) -> None:
        for kind in WriteKind:
            for caller in (an_editor(), an_approver(), None):
                with pytest.raises(ElementLockedError):
                    guard_element_write(self.bogus_state(), kind, caller)

    def test_an_unknown_role_or_no_role_may_take_no_transition(self) -> None:
        stranger = Caller("s", "n", frozenset({"ea-god", "ea-editors"}))
        for caller in (stranger, Caller("s", "n")):
            assert allowed_transitions(S.DRAFT, False, caller) == ()
            for transition in Tr:
                with pytest.raises(NotAuthorisedError):
                    apply_transition(an_element(), transition, caller, now=LATER, last_author="x")

    def test_an_unknown_transition_is_refused(self) -> None:
        with pytest.raises(NotAuthorisedError):
            apply_transition(an_element(), "teleport", an_approver(), now=LATER)  # type: ignore[arg-type]


class TestApprovalNamesTheRevisionItSaw:
    def approve(self, element: Element, expected: int | None) -> Element:
        return apply_transition(
            element,
            Tr.APPROVE,
            an_approver(),
            now=LATER,
            last_author="editor-1",
            expected_revision=expected,
        )

    def test_the_revision_read_approves(self) -> None:
        assert self.approve(replace(an_element(S.IN_REVIEW), revision=4), 4).approval_state is (
            S.APPROVED
        )

    @pytest.mark.parametrize("expected", [None, 3, 5, 0])
    def test_another_or_missing_revision_is_stale(self, expected: int | None) -> None:
        with pytest.raises(ApprovalStaleError):
            self.approve(replace(an_element(S.IN_REVIEW), revision=4), expected)

    def test_the_other_transitions_certify_no_content_and_ignore_it(self) -> None:
        stale = replace(an_element(S.APPROVED), revision=4)

        assert apply_transition(stale, Tr.LOCK, an_approver(), now=LATER).locked is True
        assert (
            apply_transition(
                replace(an_element(S.IN_REVIEW), revision=4), Tr.REJECT, an_approver(), now=LATER
            ).approval_state
            is S.DRAFT
        )
