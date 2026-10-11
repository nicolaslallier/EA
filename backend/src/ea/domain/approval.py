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

from ea.domain.errors import ElementLockedError, InvalidTransitionError, NotAuthorisedError

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


#: Everything but `submit` is the approver's.
APPROVER_TRANSITIONS: Final = frozenset(
    {Transition.APPROVE, Transition.REJECT, Transition.REOPEN, Transition.LOCK, Transition.UNLOCK}
)

#: (state, locked) -> the transitions the state offers, before any role is asked.
#: A transition not listed here is refused: there is no other way in.
_OFFERED: Final[dict[tuple[ApprovalState, bool], tuple[Transition, ...]]] = {
    (ApprovalState.DRAFT, False): (Transition.SUBMIT,),
    (ApprovalState.IN_REVIEW, False): (Transition.APPROVE, Transition.REJECT),
    (ApprovalState.APPROVED, False): (Transition.REOPEN, Transition.LOCK),
    (ApprovalState.APPROVED, True): (Transition.UNLOCK,),
}


def _may(transition: Transition, caller: Caller) -> bool:
    return caller.can_approve if transition in APPROVER_TRANSITIONS else caller.can_write


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
    separate_duties: bool = False,
) -> Element:
    """The element after the transition.

    403 if the role is missing — checked first: whether a state offers a
    transition is not something a caller without the role needs to learn — then
    409 if the state forbids it. With `separate_duties`, `approve` is refused to
    `last_author`, the subject that made the element's last change.
    """
    if not _may(transition, caller):
        needed = "ea-approver" if transition in APPROVER_TRANSITIONS else "ea-editor"
        msg = f"{transition.value} needs the {needed} role"
        raise NotAuthorisedError(msg)
    if transition not in _OFFERED.get((element.approval_state, element.locked), ()):
        state = "locked" if element.locked else element.approval_state.value.replace("_", " ")
        msg = f"{element.name!r} is {state}: {transition.value} is not a transition it offers"
        raise InvalidTransitionError(msg)
    if (
        transition is Transition.APPROVE
        and separate_duties
        and last_author is not None
        and last_author == caller.subject
    ):
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
    return element.locked or element.approval_state is ApprovalState.APPROVED


def check_modifiable(element: Element, caller: Caller) -> None:
    """Refuse a change the element's state forbids this caller."""
    if element.locked:
        msg = f"{element.name!r} is locked: an approver must unlock it first"
        raise ElementLockedError(msg)
    if element.approval_state is ApprovalState.APPROVED and not caller.can_approve:
        msg = f"{element.name!r} is approved: only an approver may change it"
        raise ElementLockedError(msg)


def check_untouched(element: Element) -> None:
    """Refuse a change no role may make while the element is approved or locked.

    For what is not an edit of the element's facts but still moves it or its
    material — filing it elsewhere, attaching a document — and which therefore
    cannot send it back to review in the same transaction.
    """
    if is_frozen(element):
        state = "locked" if element.locked else "approved"
        msg = f"{element.name!r} is {state}: an approver must reopen it first"
        raise ElementLockedError(msg)


def reviewed_after_change(element: Element) -> Element:
    """An approved element that has been changed is in review again — never approved in silence."""
    if element.approval_state is not ApprovalState.APPROVED:
        return element
    return replace(
        element, approval_state=ApprovalState.IN_REVIEW, approved_by_sub=None, approved_at=None
    )
