"""Review and locking of an element — see docs/adr/0046.

Pure: the table of transitions, who may take each, and what each state forbids
are decided here. `ArchitectureService` applies them inside the transaction that
writes the element, on the row that transaction holds locked. The SPA restates
none of it: it is handed `allowed_transitions` by the API and draws a button for
each.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ea.domain.errors import (
    ApprovalStaleError,
    ElementLockedError,
    InvalidTransitionError,
    NotAuthorisedError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ea.domain.auth import Caller
    from ea.domain.model import Element


class ApprovalState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"


class Transition(StrEnum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    REOPEN = "reopen"
    LOCK = "lock"
    UNLOCK = "unlock"


#: (state, locked) -> the transitions the state offers, before any role is asked.
#: A transition not listed here is refused: there is no other way in.
_OFFERED: Final[dict[tuple[ApprovalState, bool], tuple[Transition, ...]]] = {
    (ApprovalState.DRAFT, False): (Transition.SUBMIT,),
    (ApprovalState.IN_REVIEW, False): (Transition.APPROVE, Transition.REJECT),
    (ApprovalState.APPROVED, False): (Transition.REOPEN, Transition.LOCK),
    (ApprovalState.APPROVED, True): (Transition.UNLOCK,),
}


#: The role each transition needs. Written out, and read with no default: a
#: transition missing here is refused, never granted to whoever can write.
_NEEDS: Final[dict[Transition, str]] = {
    Transition.SUBMIT: "ea-editor",
    Transition.APPROVE: "ea-approver",
    Transition.REJECT: "ea-approver",
    Transition.REOPEN: "ea-approver",
    Transition.LOCK: "ea-approver",
    Transition.UNLOCK: "ea-approver",
}

#: The only states a plain editor may change an element in.
_EDITABLE: Final = frozenset({ApprovalState.DRAFT, ApprovalState.IN_REVIEW})


def _needs(transition: Transition) -> str | None:
    return _NEEDS.get(transition)


def needs_approver(transition: Transition) -> bool:
    return _needs(transition) == "ea-approver"


def _may(transition: Transition, caller: Caller) -> bool:
    needed = _needs(transition)
    if needed == "ea-editor":
        return caller.can_write
    if needed == "ea-approver":
        return caller.can_approve
    return False


def allowed_transitions(
    state: ApprovalState, locked: bool, caller: Caller
) -> tuple[Transition, ...]:
    return tuple(t for t in _OFFERED.get((state, locked), ()) if _may(t, caller))


def apply_transition(
    element: Element,
    transition: Transition,
    caller: Caller,
    *,
    now: datetime,
    last_author: str | None = None,
    expected_revision: int | None = None,
    separate_duties: bool = True,
) -> Element:
    """The element after the transition.

    403 if the role is missing — checked first: whether a state offers a
    transition is not something a caller without the role needs to learn — then
    409 if the state forbids it. With `separate_duties` — the default, so that
    forgetting it is a refusal — `approve` is refused to `last_author`, the
    subject that made the element's last change, and equally when that subject is
    unknown: a missing fact never lets the rule be skipped. Only an explicit
    `separate_duties=False` (`EA_APPROVAL_SEPARATION`) switches it off.

    `approve` also needs `expected_revision`, the `revision` of the element the
    approver read: a different or missing one is a 409 (`ApprovalStaleError`),
    so nothing is approved that the approver did not see.
    """
    if not _may(transition, caller):
        msg = f"{transition} needs the {_needs(transition) or 'a role no one holds'} role"
        raise NotAuthorisedError(msg)
    if transition not in _OFFERED.get((element.approval_state, element.locked), ()):
        state = "locked" if element.locked else str(element.approval_state).replace("_", " ")
        msg = f"{element.name!r} is {state}: {transition} is not a transition it offers"
        raise InvalidTransitionError(msg)
    if transition is Transition.APPROVE and expected_revision != element.revision:
        # Only an approval certifies content: the other transitions say nothing
        # about what the element holds, so they take no revision.
        msg = (
            f"{element.name!r} is at revision {element.revision}"
            + ("" if expected_revision is None else f", not {expected_revision} as you read it")
            + ": read it again before approving"
        )
        raise ApprovalStaleError(msg)
    if transition is Transition.APPROVE and separate_duties:
        if not last_author:
            msg = (
                "separation of duties: who made the last change to this element is unknown, "
                "so it cannot be approved"
            )
            raise NotAuthorisedError(msg)
        if last_author == caller.subject:
            msg = (
                "separation of duties: you made the last change to this element, "
                "someone else must approve it"
            )
            raise NotAuthorisedError(msg)
    match transition:
        case Transition.SUBMIT:
            return replace(element, approval_state=ApprovalState.IN_REVIEW)
        case Transition.APPROVE:
            return replace(
                element,
                approval_state=ApprovalState.APPROVED,
                approved_by_sub=caller.subject,
                approved_at=now,
            )
        case Transition.REJECT:
            return replace(element, approval_state=ApprovalState.DRAFT)
        case Transition.REOPEN:
            return replace(
                element,
                approval_state=ApprovalState.DRAFT,
                approved_by_sub=None,
                approved_at=None,
                locked=False,
            )
        case Transition.LOCK:
            return replace(element, locked=True)
        case Transition.UNLOCK:
            return replace(element, locked=False)


def is_frozen(element: Element) -> bool:
    """Approved or locked: what a bulk operation leaves alone."""
    return element.locked or element.approval_state not in _EDITABLE


def check_modifiable(element: Element, caller: Caller) -> None:
    """Refuse a change the element's state forbids this caller."""
    if element.locked:
        msg = f"{element.name!r} is locked: an approver must unlock it first"
        raise ElementLockedError(msg)
    if element.approval_state in _EDITABLE:
        return
    if element.approval_state is ApprovalState.APPROVED and caller.can_approve:
        return
    msg = f"{element.name!r} is {element.approval_state}: only an approver may change it"
    raise ElementLockedError(msg)


def check_untouched(element: Element) -> None:
    """Refuse a change no role may make while the element is approved or locked.

    For what is not an edit of the element's facts but still moves it or its
    material — filing it elsewhere, attaching a document — and which therefore
    cannot send it back to review in the same transaction.
    """
    if is_frozen(element):
        state = "locked" if element.locked else str(element.approval_state)
        msg = f"{element.name!r} is {state}: an approver must reopen it first"
        raise ElementLockedError(msg)


def reviewed_after_change(element: Element) -> Element:
    """An approved element that has been changed is in review again — never approved in silence."""
    if element.approval_state is not ApprovalState.APPROVED:
        return element
    return replace(
        element, approval_state=ApprovalState.IN_REVIEW, approved_by_sub=None, approved_at=None
    )
