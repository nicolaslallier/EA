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
    from collections.abc import Sequence
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


class WriteKind(StrEnum):
    """The ways to write to an element, or to what hangs from it (docs/adr/0046)."""

    CONTENT = "content"  # its fields and properties; deleting it
    LINK = "link"  # a relationship added to or removed from it
    DOCUMENT = "document"  # a document attached, revised or discarded
    FILING = "filing"  # moved to another package, or re-filed when its package is deleted
    RESTORE = "restore"  # rewritten or deleted by a baseline restoration


def is_frozen(element: Element) -> bool:
    """Approved or locked, or in a state this code does not know: the bulk writes leave it alone."""
    return element.locked or element.approval_state not in _EDITABLE


def guard_element_write(element: Element, kind: WriteKind, caller: Caller | None = None) -> None:
    """THE rule of what review lets through — every way into an element asks it.

    Called with the element as read under its row lock, in the transaction that
    writes (`update_element`, `delete_element`, a link, a document, a filing, a
    restoration's protection: see `tests/unit/test_review_guard.py`). Raises
    `ElementLockedError`; returns when the write may go on.

    - locked, or a state this code does not know: refused to everyone;
    - draft and in review: allowed;
    - approved: a change to its content only by an approver (who sends it back to
      review), a link by any editor (same consequence); a document, a filing or
      a restoration by nobody — they cannot send it back to review in the same
      transaction, so an approver reopens it first.

    No caller (`None`) never lifts a refusal.
    """
    if element.locked:
        msg = f"{element.name!r} is locked: an approver must unlock it first"
        raise ElementLockedError(msg)
    if element.approval_state in _EDITABLE:
        return
    if element.approval_state is ApprovalState.APPROVED:
        if kind is WriteKind.LINK:
            return
        if kind is WriteKind.CONTENT:
            if caller is not None and caller.can_approve:
                return
            msg = f"{element.name!r} is approved: only an approver may change it"
            raise ElementLockedError(msg)
        msg = f"{element.name!r} is approved: an approver must reopen it first"
        raise ElementLockedError(msg)
    msg = f"{element.name!r} is {element.approval_state}: an approver must reopen it first"
    raise ElementLockedError(msg)


#: How many names a refusal over a batch spells out.
_NAMED: Final = 20


def guard_all(
    elements: Sequence[Element],
    kind: WriteKind,
    caller: Caller | None = None,
    *,
    total: int | None = None,
) -> None:
    """`guard_element_write` over a batch: all or none, with an exact count and a bounded list.

    `total` is the true number of refused elements when `elements` is only a
    sample of them.
    """
    refused: list[Element] = []
    for element in elements:
        try:
            guard_element_write(element, kind, caller)
        except ElementLockedError:
            refused.append(element)
    if not refused:
        return
    count = max(total or 0, len(refused))
    shown = ", ".join(repr(e.name[:60]) for e in refused[:_NAMED])
    tail = f" and {count - _NAMED} more" if count > _NAMED else ""
    msg = (
        f"{count} element(s) are approved or locked ({kind.value}), "
        f"an approver must reopen them first: {shown}{tail}"
    )
    raise ElementLockedError(msg)


def reviewed_after_change(element: Element) -> Element:
    """An approved element that has been changed is in review again — never approved in silence."""
    if element.approval_state is not ApprovalState.APPROVED:
        return element
    return replace(
        element, approval_state=ApprovalState.IN_REVIEW, approved_by_sub=None, approved_at=None
    )
