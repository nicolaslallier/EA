"""The log line of a refusal that leaves no event in the journal.

A write refused by review or locking changes nothing, so the audit trail is
silent about it (docs/adr/0044): the structured log is the only trace. The ids
are bounded here, at the source — a refusal over a thousand elements names the
first few and their count.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Final
from uuid import UUID

from ea.domain.errors import (
    ApprovalStaleError,
    ElementLockedError,
    InvalidTransitionError,
    NotAuthorisedError,
)

#: How many ids a refusal's log line carries; the count says the rest.
LOGGED_IDS: Final = 5

logger = logging.getLogger("ea.refusals")


@contextmanager
def logged_refusal(
    action: str, element_ids: Sequence[UUID] = (), *, count: int | None = None
) -> Iterator[None]:
    """Log, then re-raise, a refusal by review or locking. Anything else passes untouched."""
    try:
        yield
    except (
        ElementLockedError,
        InvalidTransitionError,
        ApprovalStaleError,
        NotAuthorisedError,
    ) as refusal:
        logger.warning(
            "%s: %s",
            action,
            type(refusal).__name__,
            extra={
                "action": action,
                "reason": type(refusal).__name__,
                "element_id": str(element_ids[0]) if len(element_ids) == 1 else None,
                "element_ids": [str(i) for i in element_ids[:LOGGED_IDS]],
                "element_count": count if count is not None else len(element_ids),
            },
        )
        raise
