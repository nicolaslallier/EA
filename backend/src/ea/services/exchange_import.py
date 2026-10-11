"""The import of an exchange file, row by row (docs/adr/0048, docs/adr/0050).

Two rules shape everything here.

**What the domain can judge alone is judged first, in memory.** An unknown type, a
name that is blank or too long, a link the ArchiMate matrix forbids, a dangling
reference: these rows are rejected with a stable code *before* the first write and
are never given to the database, so they do not spend the budget of refusals the
transaction tolerates (`MAX_REFUSED_ROWS`, past which the file is rejected whole).
A real file holds hundreds of links the matrix forbids; only what the database
alone can decide — an approval guard, a concurrent duplicate, a constraint — counts.

**A row may run twice.** `apply_rows` rolls a batch back when one of its rows is
refused and applies the rows before it again, without a savepoint, under new ids:
the first pass is gone with the rollback. So nothing a row learns lives outside a
`_Ledger`, which records every change to the import's maps by row and undoes the
rows from the first one replayed; results are keyed by row and overwritten; and a
refusal is a *raise*, never a result, whenever a write may have preceded it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from ea.domain.approval import reviewed_after_change
from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    CyclicContainmentError,
    DomainError,
    DuplicateElementError,
    ElementLockedError,
    ElementNotFoundError,
    IllegalRelationshipError,
    RowRefusedError,
)
from ea.domain.exchange import (
    SOURCE_ID,
    Category,
    ConflictPolicy,
    ExchangeElement,
    ExchangeModel,
    ExchangeRelationship,
    ImportOptions,
    Issue,
    Outcome,
    ReportBuilder,
    Severity,
    Unreadable,
    validate_element,
    validate_link,
)
from ea.domain.ipam import validate_ipam_properties
from ea.domain.model import Element

if TYPE_CHECKING:
    from collections.abc import MutableMapping, MutableSet

    from ea.domain.ports import ExchangeRepository
    from ea.services.architecture import ArchitectureService

logger = logging.getLogger(__name__)

#: The stable name a report gives each refusal the services can raise.
_CODES: Final[dict[type[Exception], str]] = {
    IllegalRelationshipError: "relationship_not_allowed",
    CyclicContainmentError: "cyclic_containment",
    DuplicateElementError: "duplicate_element",
    AddressAlreadyAssignedError: "address_taken",
    ElementNotFoundError: "unknown_end",
    ElementLockedError: "element_locked",
}
_MISSING: Final = object()
_ADDED: Final = object()
_REMOVED: Final = object()

ElementKey = tuple[ElementType, str]
LinkKey = tuple[RelationshipType, UUID, UUID]


def refusal_issue(
    error: Exception, identifier: str, line: int | None = None, path: str | None = None
) -> Issue:
    """A report line for a refusal. A database error is never quoted: it is named, not echoed."""
    if isinstance(error, RowRefusedError):
        code, message = error.code, str(error)
    elif isinstance(error, DomainError):
        code, message = _CODES.get(type(error), "invalid_row"), str(error)
    else:
        code, message = "integrity_error", "the database refused this row"
    return Issue(Severity.ERROR, code, message, identifier, line, path)


class _Ledger:
    """The undo log of the import's maps, by row.

    A row is applied again after a refusal elsewhere in its batch; it must find the maps
    as they were *before it*. `begin(row)` going backwards is that replay starting: every
    change made by the rows from `row` on is undone, in reverse, and the rows then
    rebuild it. A row that raised made no change (it changes the maps last), so only
    completed rows have entries.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[int, Any, Any, Any]] = []
        self._row = -1
        self._high = -1

    def begin(self, row: int) -> None:
        if row <= self._high:
            self._rewind(row)
        self._row = row
        self._high = max(self._high, row)

    def put[K, V](self, mapping: MutableMapping[K, V], key: K, value: V) -> None:
        self._undo.append((self._row, mapping, key, mapping.get(key, _MISSING)))
        mapping[key] = value

    def add[V](self, members: MutableSet[V], value: V) -> None:
        if value not in members:
            members.add(value)
            self._undo.append((self._row, members, value, _ADDED))

    def discard[V](self, members: MutableSet[V], value: V) -> None:
        if value in members:
            members.discard(value)
            self._undo.append((self._row, members, value, _REMOVED))

    def _rewind(self, row: int) -> None:
        while self._undo and self._undo[-1][0] >= row:
            _, container, key, old = self._undo.pop()
            if old is _ADDED:
                container.discard(key)
            elif old is _REMOVED:
                container.add(key)
            elif old is _MISSING:
                container.pop(key, None)
            else:
                container[key] = old


@dataclass(slots=True)
class _State:
    """What an import has learned: the catalogue as it was, plus what it added (via the ledger)."""

    by_key: dict[ElementKey, Element]
    by_source: dict[str, UUID]
    types: dict[UUID, ElementType]
    links: set[LinkKey]
    approved: set[UUID]
    ids: dict[str, UUID] = field(default_factory=dict)
    created: set[ElementKey] = field(default_factory=set)
    added_links: set[LinkKey] = field(default_factory=set)
    #: File identifiers whose row was refused. Deliberately outside the ledger: a refused
    #: row is never replayed, and what depends on it must be rejected, then and after.
    refused: set[str] = field(default_factory=set)

    def resolve(self, identifier: str) -> UUID | None:
        """The element an identifier names: this file's, else one already in the catalogue."""
        return self.ids.get(identifier) or self.by_source.get(identifier)


@dataclass(frozen=True, slots=True)
class _Row:
    index: int  # position in the file; the report follows it
    category: Category
    element: ExchangeElement | None = None
    link: ExchangeRelationship | None = None

    @property
    def identifier(self) -> str:
        item = self.element or self.link
        assert item is not None
        return item.identifier


@dataclass(frozen=True, slots=True)
class _Result:
    category: Category
    outcome: Outcome
    issues: tuple[Issue, ...] = ()


def _located(issue: Issue, item: ExchangeElement | ExchangeRelationship) -> Issue:
    return Issue(issue.severity, issue.code, issue.message, item.identifier, item.line, item.path)


class ImportRun:
    """One import: the plan, the rows, the report. Lives for one call of the service."""

    def __init__(
        self,
        repository: ExchangeRepository,
        architecture: ArchitectureService,
        options: ImportOptions,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._options = options
        self._ledger = _Ledger()
        self._results: dict[int, _Result] = {}

    async def run(self, model: ExchangeModel, report: ReportBuilder) -> None:
        known = await self._repository.known_elements()
        state = _State(
            by_key={(e.element_type, e.name): e for e in known},
            by_source={e.properties[SOURCE_ID]: e.id for e in known if SOURCE_ID in e.properties},
            types={e.id: e.element_type for e in known},
            links=set(await self._repository.known_relationship_keys()),
            # Asked of the domain, not compared: the rule of review lives in one place.
            approved={e.id for e in known if reviewed_after_change(e) is not e},
        )
        for skipped in model.unreadable:
            self._count_unreadable(skipped, state, report)
        rows = self._plan(model, state)
        await self._repository.lock_elements(self._touched(rows, state))

        async def apply(row: _Row) -> None:
            await self._apply(row, state)

        for refusal in await self._repository.apply_rows(rows, apply):
            row = rows[refusal.index]
            item = row.element or row.link
            assert item is not None
            self._results[row.index] = _Result(
                row.category, Outcome.REJECTED, (_located(refusal_issue(refusal.error, ""), item),)
            )
        for index in sorted(self._results):
            result = self._results[index]
            report.count(result.category, result.outcome)
            for issue in result.issues:
                report.add(issue, result.category)

    # --- Before the first write: what the domain can judge alone ------------------

    @staticmethod
    def _count_unreadable(item: Unreadable, state: _State, report: ReportBuilder) -> None:
        report.count(item.category, Outcome.IGNORED if item.ignored else Outcome.REJECTED)
        if item.cascade:
            state.refused.add(item.identifier)

    def _plan(self, model: ExchangeModel, state: _State) -> list[_Row]:
        rows: list[_Row] = []
        index = 0
        ends: dict[str, tuple[ElementType, object]] = {}
        for element in model.elements:
            try:
                validate_element(element)
                validate_ipam_properties(element.element_type, element.properties)
            except (DomainError, ValueError) as refusal:
                state.refused.add(element.identifier)
                self._reject(index, Category.ELEMENTS, refusal_issue(refusal, "", None), element)
            else:
                ends[element.identifier] = (
                    element.element_type,
                    (element.element_type, element.name.strip()),
                )
                rows.append(_Row(index, Category.ELEMENTS, element=element))
            index += 1
        for link in model.relationships:
            problem = self._judge_link(link, state, ends)
            if problem is None:
                rows.append(_Row(index, Category.RELATIONSHIPS, link=link))
            else:
                self._reject(index, Category.RELATIONSHIPS, problem, link)
            index += 1
        return rows

    def _reject(
        self,
        index: int,
        category: Category,
        issue: Issue,
        item: ExchangeElement | ExchangeRelationship,
    ) -> None:
        self._results[index] = _Result(category, Outcome.REJECTED, (_located(issue, item),))

    def _judge_link(
        self, link: ExchangeRelationship, state: _State, ends: dict[str, tuple[ElementType, object]]
    ) -> Issue | None:
        """Why this link can never be written, or `None`. Touches nothing."""
        if link.source in state.refused or link.target in state.refused:
            return Issue(
                Severity.ERROR,
                "end_rejected",
                "an end of this relationship was rejected or ignored",
            )
        source, target = self._end(link.source, state, ends), self._end(link.target, state, ends)
        if source is None or target is None:
            return Issue(
                Severity.ERROR,
                "unknown_end",
                "an end of this relationship is not in the file or the catalogue",
            )
        try:
            validate_link(link, source[0], target[0], same_element=source[1] == target[1])
        except RowRefusedError as refusal:
            return refusal_issue(refusal, "")
        return None

    @staticmethod
    def _end(
        identifier: str, state: _State, ends: dict[str, tuple[ElementType, object]]
    ) -> tuple[ElementType, object] | None:
        if identifier in ends:
            return ends[identifier]
        uid = state.by_source.get(identifier)
        return (state.types[uid], uid) if uid is not None else None

    def _touched(self, rows: list[_Row], state: _State) -> set[UUID]:
        """The existing elements this import will write to or link, to lock them in id order.

        Locking them before the first write means an ordinary write meets them in the same
        order as the import (docs/adr/0050, the interlock limit). An element reached only
        by a file row refused at write time is locked for nothing: harmless.
        """
        reuse = self._options.on_conflict is ConflictPolicy.REUSE
        by_identifier = {
            row.element.identifier: state.by_key.get(
                (row.element.element_type, row.element.name.strip())
            )
            for row in rows
            if row.element is not None
        }

        def existing(identifier: str) -> UUID | None:
            if identifier in by_identifier:
                match = by_identifier[identifier]
                return match.id if match is not None and reuse else None
            return state.by_source.get(identifier)

        touched: set[UUID] = set()
        for row in rows:
            names = [row.element.identifier] if row.element else []
            if row.link:
                names = [row.link.source, row.link.target]
            touched.update(uid for uid in map(existing, names) if uid is not None)
        return touched

    # --- The rows, possibly twice --------------------------------------------------

    async def _apply(self, row: _Row, state: _State) -> None:
        self._ledger.begin(row.index)
        try:
            if row.element is not None:
                result = await self._element(row, row.element, state)
            else:
                assert row.link is not None
                result = await self._link(row, row.link, state)
        except Exception as error:
            if row.element is not None:
                state.refused.add(row.element.identifier)
            if isinstance(error, ValueError):  # a pure check, raised before any write
                raise RowRefusedError("invalid_row", str(error)) from error
            raise
        self._results[row.index] = result

    def _rejected(
        self,
        row: _Row,
        item: ExchangeElement | ExchangeRelationship,
        state: _State,
        code: str,
        why: str,
    ) -> _Result:
        if row.element is not None:
            state.refused.add(item.identifier)
        issue = Issue(Severity.ERROR, code, why, item.identifier, item.line, item.path)
        return _Result(row.category, Outcome.REJECTED, (issue,))

    async def _element(self, row: _Row, item: ExchangeElement, state: _State) -> _Result:
        key = (item.element_type, item.name.strip())
        issues: list[Issue] = []
        if key in state.created:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "duplicate_in_file",
                    "the same type and name appear under another identifier; merged",
                    item.identifier,
                    item.line,
                    item.path,
                )
            )
        elif key in state.by_key and self._options.on_conflict is ConflictPolicy.FAIL:
            msg = f"a {item.element_type.value} named {key[1]!r} already exists"
            return self._rejected(row, item, state, "already_exists", msg)
        match = state.by_key.get(key)
        if match is None:
            created = await self._architecture.create_element(
                element_type=item.element_type,
                name=item.name,
                description=item.description,
                documentation=item.documentation,
                properties={**item.properties, SOURCE_ID: item.identifier},
            )
            self._ledger.put(state.by_key, key, created)
            self._ledger.put(state.by_source, item.identifier, created.id)
            self._ledger.put(state.types, created.id, created.element_type)
            self._ledger.add(state.created, key)
            self._ledger.put(state.ids, item.identifier, created.id)
            return _Result(row.category, Outcome.CREATED, tuple(issues))
        current = await self._complete(match, item, state, issues)
        self._ledger.put(state.by_key, key, current)
        self._ledger.put(state.ids, item.identifier, current.id)
        if SOURCE_ID not in match.properties:
            self._ledger.put(state.by_source, item.identifier, current.id)
        return _Result(row.category, Outcome.REUSED, tuple(issues))

    async def _complete(
        self, match: Element, item: ExchangeElement, state: _State, issues: list[Issue]
    ) -> Element:
        """Fill what is empty — a description, a `source_id` — and touch nothing else.

        This *is* a write to an existing element, so the approval guard may refuse it: that is
        a warning, not a failure, and never a way round the guard.
        """
        # The copy of the catalogue read at the start only decides which rows *try*. Whether
        # a field is still blank is decided by the service on the row it holds locked:
        # what somebody wrote in between is kept (`complete_element`).
        description = item.description if item.description and not match.description else ""
        missing_source = not match.properties.get(SOURCE_ID)
        if not description and not missing_source:
            return match
        try:
            updated = await self._architecture.complete_element(
                match.id,
                description=description,
                properties={SOURCE_ID: item.identifier} if missing_source else None,
            )
        except ElementLockedError as refusal:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "completion_not_applied",
                    str(refusal),
                    item.identifier,
                    item.line,
                    item.path,
                )
            )
            return match
        except ElementNotFoundError:
            msg = "this element was deleted while the file was being imported"
            raise RowRefusedError("element_gone", msg) from None
        if match.id in state.approved and reviewed_after_change(updated) is updated:
            self._ledger.discard(state.approved, match.id)
            issues.append(self._reopened(item))
        return updated

    @staticmethod
    def _reopened(item: ExchangeElement | ExchangeRelationship) -> Issue:
        return Issue(
            Severity.WARNING,
            "approved_element_reopened",
            "an approved element was changed and is back in review",
            item.identifier,
            item.line,
            item.path,
        )

    async def _link(self, row: _Row, item: ExchangeRelationship, state: _State) -> _Result:
        if item.source in state.refused or item.target in state.refused:
            return self._rejected(row, item, state, "end_rejected", "an end was rejected")
        source, target = state.resolve(item.source), state.resolve(item.target)
        if source is None or target is None:
            msg = "an end of this relationship is not in the file or the catalogue"
            return self._rejected(row, item, state, "unknown_end", msg)
        key = (item.relationship_type, source, target)
        if key in state.links:
            if self._options.on_conflict is ConflictPolicy.FAIL:
                return self._rejected(
                    row, item, state, "already_exists", "this link already exists"
                )
            again = key in state.added_links
            issues = (
                Issue(
                    Severity.WARNING,
                    "duplicate_relationship",
                    "the same type, source and target appear twice; one is kept",
                    item.identifier,
                    item.line,
                    item.path,
                ),
            )
            return _Result(row.category, Outcome.REUSED, issues if again else ())
        await self._architecture.connect(
            relationship_type=item.relationship_type,
            source_id=source,
            target_id=target,
            name=item.name,
            access_type=item.access_type,
            directed=item.directed,
            properties={**item.properties, SOURCE_ID: item.identifier},
        )
        self._ledger.add(state.links, key)
        self._ledger.add(state.added_links, key)
        issues_made: list[Issue] = []
        for end in dict.fromkeys((source, target)):
            if end in state.approved:
                self._ledger.discard(state.approved, end)
                issues_made.append(self._reopened(item))
        return _Result(row.category, Outcome.CREATED, tuple(issues_made))
