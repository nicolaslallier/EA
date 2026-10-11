"""Use cases over baselines — docs/adr/0045.

The rules this layer owns: a baseline is read before it is used (a readable
404), restoring is a two-step gesture (a dry run to read, then `confirm`), and a
restoration is **one** story in the audit trail — a `baseline.restored` event
followed by one event per entity it touched, all under the same request id and a
`restore:<id>` correlation. The plan is decided in the domain (`plan_restore`)
and by the repository, which computes it at one instant; the repository applies
it atomically, only if it is still the plan that was read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.audit import AuditAction, element_view, relationship_view
from ea.domain.baselines import (
    Baseline,
    BaselineDiff,
    RestorePlan,
    baseline_view,
    diff_snapshots,
)
from ea.domain.errors import BaselineNotFoundError, RestoreBlockedError
from ea.services.audit import AuditTrail
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.audit import AuditEvent
    from ea.domain.ports import BaselineRepository

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    plan: RestorePlan
    applied: bool


class BaselineService:
    """The single entry point `api/` and `mcp/` use to capture, compare and restore."""

    def __init__(self, repository: BaselineRepository, *, clock: Clock = _utc_now) -> None:
        self._repository = repository
        self._now = clock
        self._trail = AuditTrail(clock)

    async def list_baselines(self) -> tuple[Baseline, ...]:
        require_caller()
        return await self._repository.list_all()

    async def get(self, baseline_id: UUID) -> Baseline:
        require_caller()
        baseline = await self._repository.get(baseline_id)
        if baseline is None:
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)
        return baseline

    async def create(self, *, name: str, description: str = "") -> Baseline:
        caller = require_editor()
        baseline = Baseline.create(
            name=name,
            description=description,
            created_by_sub=caller.subject,
            created_by=caller.username,
            now=self._now(),
        )

        def created(stored: Baseline) -> tuple[AuditEvent, ...]:
            # Built inside the write, from the copy as stored: the counts are real.
            return (
                self._trail.event(
                    AuditAction.BASELINE_CREATED, entity_id=stored.id, after=baseline_view(stored)
                ),
            )

        stored = await self._repository.create(baseline, audit=created)
        logger.info(
            "baseline captured: %r",
            stored.name,
            extra={"action": "baseline_created", "baseline_id": str(stored.id)},
        )
        return stored

    async def delete(self, baseline_id: UUID) -> None:
        """Remove the copy. The model it copied is not touched."""
        require_editor()

        def deleted(stored: Baseline) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.BASELINE_DELETED, entity_id=stored.id, before=baseline_view(stored)
                ),
            )

        if not await self._repository.delete(baseline_id, audit=deleted):
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)

    async def diff(self, baseline_id: UUID, against: UUID | None = None) -> BaselineDiff:
        """The baseline against another one, or against the model as it is now."""
        require_caller()
        await self.get(baseline_id)
        older = await self._repository.snapshot_of(baseline_id)
        if against is None:
            newer = await self._repository.current_snapshot()
        else:
            await self.get(against)
            newer = await self._repository.snapshot_of(against)
        return diff_snapshots(older, newer)

    async def restore(
        self,
        baseline_id: UUID,
        *,
        dry_run: bool = True,
        confirm: bool = False,
        keep_new: bool | None = None,
    ) -> RestoreOutcome:
        """Plan a restoration, and apply it only when told twice (`dry_run=False`, `confirm`).

        `keep_new` defaults to `False` for a global baseline: restoring is
        exact. A plan that touches a protected element is returned by a dry run
        (its `blocked` list) and refused when applied; the repository checks the
        protection again inside its transaction.
        """
        require_editor()
        if not dry_run and not confirm:
            msg = "restoring changes the catalogue: read the dry run, then pass confirm=true"
            raise ValueError(msg)
        baseline = await self.get(baseline_id)
        wanted = bool(keep_new)
        plan = await self._repository.restore_plan(baseline_id, keep_new=wanted)
        if dry_run:
            return RestoreOutcome(plan, applied=False)
        if plan.is_blocked:
            names = ", ".join(repr(e.name) for e in plan.blocked)
            msg = f"the restoration would touch protected elements: {names}. Unlock them first"
            raise RestoreBlockedError(msg)
        story = f"restore:{baseline.id}"

        def events(verified: RestorePlan) -> list[AuditEvent]:
            # Called by the repository on the plan it verified in its own transaction.
            return self._events(baseline, verified, story)

        await self._repository.apply_restore(
            baseline_id, keep_new=wanted, expected=plan, now=self._now(), audit=events
        )
        logger.info(
            "baseline %r restored",
            baseline.name,
            extra={"action": "baseline_restored", "baseline_id": str(baseline.id)},
        )
        return RestoreOutcome(plan, applied=True)

    def _events(self, baseline: Baseline, plan: RestorePlan, story: str) -> list[AuditEvent]:
        def about(action: AuditAction, entity_id: UUID | None, **views: object) -> AuditEvent:
            return self._trail.event(
                action,
                entity_id=entity_id,
                correlation=story,
                **views,  # type: ignore[arg-type]
            )

        events = [
            about(
                AuditAction.BASELINE_RESTORED,
                baseline.id,
                after={
                    **baseline_view(baseline),
                    "elements_created": len(plan.elements_to_create),
                    "elements_updated": len(plan.elements_to_update),
                    "elements_deleted": len(plan.elements_to_delete),
                    "relationships_created": len(plan.relationships_to_create),
                    "relationships_deleted": len(plan.relationships_to_delete),
                    "relationships_cascaded": plan.cascaded_relationships,
                    "documents_lost": plan.documents_lost,
                    "boxes_lost": plan.boxes_lost,
                    "warnings": list(plan.warnings),
                },
            )
        ]
        events += [
            about(AuditAction.ELEMENT_CREATED, e.id, after=element_view(e))
            for e in plan.elements_to_create
        ]
        events += [
            about(
                AuditAction.ELEMENT_UPDATED,
                u.current.id,
                before=element_view(u.current),
                after=element_view(replace(u.restored, updated_at=self._now())),
            )
            for u in plan.elements_to_update
        ]
        events += [
            about(AuditAction.ELEMENT_DELETED, e.id, before=element_view(e))
            for e in plan.elements_to_delete
        ]
        events += [
            about(AuditAction.RELATIONSHIP_DELETED, r.id, before=relationship_view(r))
            for r in plan.relationships_to_delete
        ]
        events += [
            about(AuditAction.RELATIONSHIP_CREATED, r.id, after=relationship_view(r))
            for r in plan.relationships_to_create
        ]
        return events
