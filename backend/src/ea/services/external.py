"""Use cases over the links from an element to objects in other tools — docs/adr/0062.

What is decided here, and nowhere else:

* **Who may.** A reader lists the references and sees the declared targets; an editor links,
  unlinks, refreshes and opens tickets. Every public method starts with `require_caller()` or
  `require_editor()`.
* **A caller names a target, never an address.** `target` is a key of the configuration the
  process was started with; the host, the scope and the account are the provider's, built from
  that configuration. An unknown target, an inactive one and one of another kind are refused
  with the same message — which does not tell a caller what is configured.
* **Reading an element never calls out.** Listing serves the cache. Resolving happens when a
  reference is created and when a person (or the refresh flow) asks.
* **A third party is bounded.** Every outbound call holds a place of the `Admission`, global and
  per caller, and a busy process answers `ExternalBusyError` at once instead of queueing.
* **A write to a third party is a dry run unless it is asked for.** `open_issue` shows what
  would be sent, and sends it only with `dry_run=False`; it is attempted once. If the ticket is
  opened and the local link then fails, the error says so — the ticket exists, and a retry
  would open a second.
* **A refresh that changes nothing says nothing.** The trail records a link, an unlink, and a
  refresh that changed the title or the state; the title itself only as a length and a digest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.audit import AuditAction, external_reference_view
from ea.domain.errors import (
    DuplicateExternalReferenceError,
    ExternalReferenceNotFoundError,
    ExternalRefusedError,
    ExternalUnavailableError,
    InvalidExternalReferenceError,
    UnknownExternalTargetError,
)
from ea.domain.external import (
    MAX_REFRESH_BATCH,
    MAX_STALE_PAGE,
    STALE_AFTER,
    ExternalKind,
    ExternalReference,
    ExternalSnapshot,
    ExternalStatus,
    ExternalView,
    IssueDraft,
    IssuePlan,
    TargetStatus,
    clean_scope,
    parse_reference,
)
from ea.services.audit import AuditTrail
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.audit import AuditEvent
    from ea.domain.ports import ExternalReferenceRepository, ExternalSystem
    from ea.services.architecture import ArchitectureService
    from ea.services.exchange_admission import Admission

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_writable(system: ExternalSystem) -> None:
    """The one gate every write to a third party passes: a target is read-only unless declared.

    Decided on the target the *server* resolved, before any network call, for an editor and
    for a dry run alike (`tests/unit/test_external_write_gate.py` reads the source).
    """
    if system.writable is not True:
        msg = f"the {system.kind.value} target you named is not writable: it was not declared so"
        raise UnknownExternalTargetError(msg)


def _not_found(reference_id: UUID) -> ExternalReferenceNotFoundError:
    return ExternalReferenceNotFoundError(f"no external reference with id {reference_id}")


class ExternalReferenceService:
    """The single entry point `api/` and `mcp/` use for external references."""

    def __init__(
        self,
        repository: ExternalReferenceRepository,
        architecture: ArchitectureService,
        systems: Mapping[str, ExternalSystem],
        *,
        admission: Admission | None = None,
        inactive: Mapping[str, ExternalKind] | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._systems = dict(systems)
        self._inactive = dict(inactive or {})
        self._admission = admission
        self._now = clock
        self._trail = AuditTrail(clock)

    # --- reading: never calls out -------------------------------------------------------

    def _view(self, reference: ExternalReference) -> ExternalView:
        return ExternalView(reference=reference, stale=reference.is_stale(self._now()))

    async def targets(self) -> tuple[TargetStatus, ...]:
        """The declared targets by name and kind — never an address, a scope or an account."""
        require_caller()
        active = [
            TargetStatus(system.name, system.kind, active=True, writable=system.writable)
            for system in self._systems.values()
        ]
        inactive = [
            TargetStatus(name, kind, active=False, writable=False)
            for name, kind in self._inactive.items()
        ]
        return tuple(sorted([*active, *inactive], key=lambda status: status.name))

    async def list_for_element(self, element_id: UUID) -> tuple[ExternalView, ...]:
        require_caller()
        await self._architecture.get_element(element_id)
        return tuple(self._view(row) for row in await self._repository.list_for(element_id))

    async def list_stale(self, *, limit: int = MAX_STALE_PAGE) -> tuple[ExternalView, ...]:
        """References whose cache is older than `STALE_AFTER`, oldest first (the refresh flow)."""
        require_caller()
        if not 1 <= limit <= MAX_STALE_PAGE:
            msg = f"limit is between 1 and {MAX_STALE_PAGE}"
            raise ValueError(msg)
        rows = await self._repository.list_stale(before=self._now() - STALE_AFTER, limit=limit)
        return tuple(self._view(row) for row in rows)

    # --- resolving a target -------------------------------------------------------------

    def _writable_system(self, kind: ExternalKind, target: str) -> ExternalSystem:
        system = self._system_for(kind, target)
        ensure_writable(system)
        return system

    def _system_for(self, kind: ExternalKind, target: str | None) -> ExternalSystem:
        """The active target a caller named — or the only one serving the kind."""
        candidates = {n: s for n, s in self._systems.items() if s.kind is kind}
        if target is None and len(candidates) == 1:
            target = next(iter(candidates))
        system = candidates.get(target) if target is not None else None
        if system is None:
            names = ", ".join(sorted(candidates)) or "none"
            msg = f"name one of the active {kind.value} targets: {names}"
            raise UnknownExternalTargetError(msg)
        return system

    async def _ask(self, system: ExternalSystem, external_id: str) -> ExternalSnapshot:
        """One outbound read, under a place of the admission."""
        caller = require_caller()
        if self._admission is None:
            return await system.resolve(external_id)
        async with self._admission.lease(caller.subject):
            return await system.resolve(external_id)

    # --- writing the catalogue ----------------------------------------------------------

    async def link(
        self,
        element_id: UUID,
        *,
        kind: ExternalKind,
        reference: str,
        target: str | None = None,
    ) -> ExternalView:
        """Cite an external object from an element, after the provider has confirmed it exists.

        A provider that cannot be asked creates nothing: a link nobody verified is worse than
        none. A free link (`kind=url`) is stored as written and never called.
        """
        caller = require_editor()
        await self._architecture.get_element(element_id)
        if kind is ExternalKind.URL:
            if target:
                msg = "a free link has no target"
                raise InvalidExternalReferenceError(msg)
            parsed = parse_reference(kind, reference, web_base=None)
            system, target_name, snapshot = None, "", None
        else:
            system = self._system_for(kind, target)
            target_name = system.name
            parsed = parse_reference(kind, reference, web_base=system.web_url)
        # The duplicate is found before the provider is asked: it costs a read, not a quota.
        for row in await self._repository.list_for(element_id):
            if (row.target, row.external_id) == (target_name, parsed.external_id):
                msg = "this element already cites that object"
                raise DuplicateExternalReferenceError(msg)
        if system is not None:
            snapshot = await self._ask(system, parsed.external_id)
        created = ExternalReference.create(
            element_id=element_id,
            kind=kind,
            target=target_name,
            external_id=parsed.external_id,
            url=parsed.url,
            snapshot=snapshot,
            created_by=caller.username or caller.subject,
            now=self._now(),
        )
        return await self._store(created)

    async def _store(self, created: ExternalReference) -> ExternalView:
        def linked(stored: ExternalReference) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.EXTERNAL_REFERENCE_LINKED,
                    entity_id=stored.id,
                    after=external_reference_view(stored),
                ),
            )

        stored = await self._repository.add(created, audit=linked)
        logger.info(
            "external reference linked",
            extra={
                "action": "external_linked",
                "reference_id": str(stored.id),
                "element_id": str(stored.element_id),
                "target": stored.target,
            },
        )
        return self._view(stored)

    async def refresh(self, reference_id: UUID) -> ExternalView:
        """Ask the provider again. If it cannot be asked the line is returned as it was, stale.

        A provider that says the object is gone (or no longer in scope) sets its state to
        `unknown` and keeps the title: a reference is never deleted by a refresh.
        """
        require_editor()
        row = await self._repository.get(reference_id)
        if row is None:
            raise _not_found(reference_id)
        return await self._refresh(row)

    async def _refresh(self, row: ExternalReference) -> ExternalView:
        system = self._systems.get(row.target)
        if row.kind is ExternalKind.URL:
            return self._view(row)
        if system is None or system.kind is not row.kind:
            logger.warning(
                "a reference points at a target that is not active",
                extra={"action": "external_target_inactive", "target": row.target},
            )
            return ExternalView(reference=row, stale=True)
        try:
            snapshot = await self._ask(system, row.external_id)
        except ExternalRefusedError:
            snapshot = ExternalSnapshot(row.title, ExternalStatus.UNKNOWN)
        except ExternalUnavailableError:
            return ExternalView(reference=row, stale=True)

        def refreshed(before: ExternalReference) -> tuple[AuditEvent, ...]:
            if (before.title, before.status) == (snapshot.title, snapshot.status):
                return ()
            return (
                self._trail.event(
                    AuditAction.EXTERNAL_REFERENCE_REFRESHED,
                    entity_id=before.id,
                    before=external_reference_view(before),
                    after=external_reference_view(before.with_snapshot(snapshot, now=self._now())),
                ),
            )

        updated = await self._repository.update_snapshot(
            row.id, snapshot, now=self._now(), audit=refreshed
        )
        if updated is None:
            raise _not_found(row.id)
        return self._view(updated)

    async def refresh_element(self, element_id: UUID) -> tuple[ExternalView, ...]:
        """Refresh up to `MAX_REFRESH_BATCH` references of an element, the oldest first.

        A target that fails is not asked again in this call: its lines stay as they were.
        """
        require_editor()
        await self._architecture.get_element(element_id)
        rows = await self._repository.list_for(element_id)
        due = sorted(
            (row for row in rows if row.kind is not ExternalKind.URL),
            key=lambda row: (row.synced_at or datetime.min.replace(tzinfo=UTC), str(row.id)),
        )[:MAX_REFRESH_BATCH]
        down: set[str] = set()
        refreshed: dict[UUID, ExternalView] = {}
        for row in due:
            if row.target in down:
                refreshed[row.id] = ExternalView(reference=row, stale=True)
                continue
            view = await self._refresh(row)
            if view.reference is row:
                down.add(row.target)
            refreshed[row.id] = view
        return tuple(
            refreshed.get(row.id) or self._view(row)
            for row in await self._repository.list_for(element_id)
        )

    async def unlink(self, reference_id: UUID) -> None:
        """Remove the link — never the object it points at."""
        require_editor()
        row = await self._repository.get(reference_id)
        if row is None:
            raise _not_found(reference_id)

        def unlinked(deleted: ExternalReference) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.EXTERNAL_REFERENCE_UNLINKED,
                    entity_id=deleted.id,
                    before=external_reference_view(deleted),
                ),
            )

        if not await self._repository.delete(reference_id, audit=unlinked):
            raise _not_found(reference_id)
        logger.info(
            "external reference unlinked",
            extra={
                "action": "external_unlinked",
                "reference_id": str(reference_id),
                "element_id": str(row.element_id),
            },
        )

    # --- writing to a third party -------------------------------------------------------

    async def open_issue(
        self,
        element_id: UUID,
        *,
        target: str,
        scope: str,
        title: str,
        body: str,
        dry_run: bool = True,
    ) -> IssuePlan:
        """Open a ticket at a provider and cite it from the element — a dry run by default.

        The first thing returned is *what would be sent*: where, with which title and text.
        With ``dry_run=False`` the ticket is opened **once** (no retry: it is not idempotent)
        and then linked. The words travel to a third party, so they are the caller's, bounded
        and visible in the plan; the trail records the link, never the text.
        """
        caller = require_editor()
        await self._architecture.get_element(element_id)
        system = self._writable_system(ExternalKind.GITHUB_ISSUE, target)
        clean = clean_scope(system.kind, scope)
        draft = IssueDraft.of(title, body)
        if not system.allows(clean):
            msg = "that scope is not one this target may write to"
            raise ExternalRefusedError(msg)
        if dry_run:
            return IssuePlan(True, system.name, clean, draft.title, draft.body, None)
        if self._admission is None:
            external_id = await system.create_issue(clean, draft)
        else:
            async with self._admission.lease(caller.subject):
                external_id = await system.create_issue(clean, draft)
        logger.info(
            "a ticket was opened at a provider",
            extra={"action": "external_issue_opened", "target": system.kind.value},
        )
        parsed = parse_reference(system.kind, external_id, web_base=system.web_url)
        created = ExternalReference.create(
            element_id=element_id,
            kind=system.kind,
            target=system.name,
            external_id=parsed.external_id,
            url=parsed.url,
            snapshot=ExternalSnapshot.of(draft.title, ExternalStatus.OPEN),
            created_by=caller.username or caller.subject,
            now=self._now(),
        )
        try:
            view = await self._store(created)
        except DuplicateExternalReferenceError:
            msg = f"the ticket was opened ({parsed.external_id}) but is already cited here"
            raise DuplicateExternalReferenceError(msg) from None
        return IssuePlan(False, system.name, clean, draft.title, draft.body, view)
