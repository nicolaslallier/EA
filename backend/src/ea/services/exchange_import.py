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
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from ea.domain.approval import reviewed_after_change
from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagrams import MAX_DIAGRAM_NAME_LENGTH, DiagramNode, validate_diagram_name
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    CyclicContainmentError,
    DomainError,
    DuplicateDiagramError,
    DuplicateElementError,
    ElementLockedError,
    ElementNotFoundError,
    IllegalRelationshipError,
    RowRefusedError,
)
from ea.domain.exchange import (
    MAX_DESCRIPTION_LENGTH,
    MAX_REFUSED_ROWS,
    MAX_VIEW_BOXES,
    SOURCE_ID,
    Category,
    ConflictPolicy,
    ExchangeElement,
    ExchangeModel,
    ExchangeRelationship,
    ExchangeView,
    ImportOptions,
    Issue,
    Outcome,
    ReportBuilder,
    Severity,
    Unreadable,
    accept_element,
    accept_link,
    validate_box,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element

if TYPE_CHECKING:
    from collections.abc import MutableMapping, MutableSet, Sequence

    from ea.domain.ports import ExchangeRepository
    from ea.services.architecture import ArchitectureService
    from ea.services.diagrams import DiagramService

logger = logging.getLogger(__name__)

#: The stable name a report gives each refusal the services can raise.
_CODES: Final[dict[type[Exception], str]] = {
    IllegalRelationshipError: "relationship_not_allowed",
    CyclicContainmentError: "cyclic_containment",
    DuplicateElementError: "duplicate_element",
    AddressAlreadyAssignedError: "address_taken",
    ElementNotFoundError: "unknown_end",
    ElementLockedError: "element_locked",
    DuplicateDiagramError: "duplicate_view",
}
_MISSING: Final = object()
_ADDED: Final = object()
_REMOVED: Final = object()

ElementKey = tuple[ElementType, str]
_UNIQUE_PROPERTIES: Final = (
    ("address", ADDRESS_PROPERTY),
    ("prefix", PREFIX_PROPERTY),
)
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
    #: Diagram names taken: the catalogue's, then this import's (through the ledger).
    diagram_names: set[str] = field(default_factory=set)
    created: set[ElementKey] = field(default_factory=set)
    added_links: set[LinkKey] = field(default_factory=set)
    #: File identifiers whose row was refused. Deliberately outside the ledger: a refused
    #: row is never replayed, and what depends on it must be rejected, then and after.
    refused: set[str] = field(default_factory=set)

    def resolve(self, identifier: str) -> UUID | None:
        """The element an identifier names: this file's, else one already in the catalogue."""
        return self.ids.get(identifier) or self.by_source.get(identifier)


@dataclass(frozen=True, slots=True)
class _ViewStep:
    """A view ready to be written: judged in memory, its boxes already resolved to elements
    that exist. Writing it can only be refused by the database."""

    view: ExchangeView
    name: str
    description: str
    nodes: tuple[DiagramNode, ...]
    notes: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class _Row:
    index: int  # position in the file; the report follows it
    category: Category
    item: ExchangeElement | ExchangeRelationship | _ViewStep


@dataclass(frozen=True, slots=True)
class _Result:
    category: Category
    outcome: Outcome
    issues: tuple[Issue, ...] = ()


def _located(issue: Issue, item: ExchangeElement | ExchangeRelationship | _ViewStep) -> Issue:
    if isinstance(item, _ViewStep):
        return Issue(
            issue.severity, issue.code, issue.message, item.view.identifier, None, item.view.path
        )
    return Issue(issue.severity, issue.code, issue.message, item.identifier, item.line, item.path)


def _held_by_the_catalogue(state: _State) -> set[tuple[str, str, str]]:
    """`(kind, vrf, value)` of every address and prefix the catalogue already holds."""
    held: set[tuple[str, str, str]] = set()
    for element in state.by_key.values():
        for kind, key in _UNIQUE_PROPERTIES:
            if element.properties.get(key):
                held.add((kind, read_vrf(element.properties), element.properties[key]))
    return held


def _clash(item: ExchangeElement, held: set[tuple[str, str, str]]) -> RowRefusedError | None:
    """Claim the address and prefix of a row that will create, or say which one is taken.

    The properties are already canonical (`accept_element`), so equal strings are equal
    addresses; the database enforces the same pair `(vrf, value)` with a unique index.
    """
    wanted = [
        (kind, read_vrf(item.properties), item.properties[key])
        for kind, key in _UNIQUE_PROPERTIES
        if item.properties.get(key)
    ]
    for entry in wanted:
        if entry in held:
            code = "address_taken" if entry[0] == "address" else "cidr_taken"
            return RowRefusedError(code, f"this {entry[0]} is already in use in its VRF")
    held.update(wanted)
    return None


class ImportRun:
    """One import: the plan, the rows, the report. Lives for one call of the service."""

    def __init__(
        self,
        repository: ExchangeRepository,
        architecture: ArchitectureService,
        options: ImportOptions,
        *,
        diagrams: DiagramService | None = None,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._options = options
        self._diagrams = diagrams
        self._ledger = _Ledger()
        self._results: dict[int, _Result] = {}
        #: The next position in the report; both phases number their rows from it.
        self._position = 0
        #: Diagrams of the catalogue when the import began: `(name, id)`.
        self._existing_diagrams: list[tuple[str, UUID]] = []

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

        refusals = await self._repository.apply_rows(rows, apply)
        for refusal in refusals:
            self._refuse_row(rows[refusal.index], refusal.error)
        # The second phase: what hangs on the elements, written once their fate is known.
        # It shares the first one's budget of refusals.
        steps = await self._plan_structure(model, state)
        if steps:
            more = await self._repository.apply_rows(
                steps, apply, max_refused=max(0, MAX_REFUSED_ROWS - len(refusals))
            )
            for refusal in more:
                self._refuse_row(steps[refusal.index], refusal.error)
        for index in sorted(self._results):
            result = self._results[index]
            report.count(result.category, result.outcome)
            for issue in result.issues:
                report.add(issue, result.category)

    def _refuse_row(self, row: _Row, error: Exception) -> None:
        self._results[row.index] = _Result(
            row.category, Outcome.REJECTED, (_located(refusal_issue(error, ""), row.item),)
        )

    # --- Before the first write: what the domain can judge alone ------------------

    @staticmethod
    def _count_unreadable(item: Unreadable, state: _State, report: ReportBuilder) -> None:
        report.count(item.category, Outcome.IGNORED if item.ignored else Outcome.REJECTED)
        if item.cascade:
            state.refused.add(item.identifier)

    def _plan(self, model: ExchangeModel, state: _State) -> list[_Row]:
        rows: list[_Row] = []
        index = self._position
        ends: dict[str, tuple[ElementType, object]] = {}
        held = _held_by_the_catalogue(state)
        creating: set[ElementKey] = set()
        for element in model.elements:
            try:
                accepted = accept_element(element)  # the only door to the services below
            except RowRefusedError as refusal:
                state.refused.add(element.identifier)
                self._reject(index, Category.ELEMENTS, refusal_issue(refusal, ""), element)
            else:
                key = (accepted.element_type, accepted.name)
                # Only a row that will *create* writes its address; a merged one writes none.
                # Limit: a first holder later refused by the database still blocks its
                # followers (they stay rejected here, conservatively).
                clash = (
                    _clash(accepted, held)
                    if key not in state.by_key and key not in creating
                    else None
                )
                if clash is not None:
                    state.refused.add(element.identifier)
                    self._reject(index, Category.ELEMENTS, refusal_issue(clash, ""), element)
                    index += 1
                    continue
                creating.add(key)
                ends[accepted.identifier] = (
                    accepted.element_type,
                    (accepted.element_type, accepted.name),
                )
                rows.append(_Row(index, Category.ELEMENTS, accepted))
            index += 1
        for link in model.relationships:
            judged = self._judge_link(link, state, ends)
            if isinstance(judged, Issue):
                self._reject(index, Category.RELATIONSHIPS, judged, link)
            else:
                rows.append(_Row(index, Category.RELATIONSHIPS, judged))
            index += 1
        self._position = index
        return rows

    def _reject(
        self,
        index: int,
        category: Category,
        issue: Issue,
        item: ExchangeElement | ExchangeRelationship | _ViewStep,
    ) -> None:
        self._results[index] = _Result(category, Outcome.REJECTED, (_located(issue, item),))

    def _judge_link(
        self, link: ExchangeRelationship, state: _State, ends: dict[str, tuple[ElementType, object]]
    ) -> Issue | ExchangeRelationship:
        """Why this link can never be written, or the link as the services may receive it."""
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
            return accept_link(link, source[0], target[0], same_element=source[1] == target[1])
        except RowRefusedError as refusal:
            return refusal_issue(refusal, "")

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
            row.item.identifier: state.by_key.get((row.item.element_type, row.item.name.strip()))
            for row in rows
            if isinstance(row.item, ExchangeElement)
        }

        def existing(identifier: str) -> UUID | None:
            if identifier in by_identifier:
                match = by_identifier[identifier]
                return match.id if match is not None and reuse else None
            return state.by_source.get(identifier)

        touched: set[UUID] = set()
        for row in rows:
            item = row.item
            if isinstance(item, _ViewStep):
                continue
            names = (
                [item.identifier]
                if isinstance(item, ExchangeElement)
                else [item.source, item.target]
            )
            touched.update(uid for uid in map(existing, names) if uid is not None)
        return touched

    # --- The rows, possibly twice --------------------------------------------------

    async def _apply(self, row: _Row, state: _State) -> None:
        self._ledger.begin(row.index)
        try:
            if isinstance(row.item, ExchangeElement):
                result = await self._element(row, row.item, state)
            elif isinstance(row.item, ExchangeRelationship):
                result = await self._link(row, row.item, state)
            else:
                result = await self._view(row, row.item, state)
        except Exception as error:
            if isinstance(row.item, ExchangeElement):
                state.refused.add(row.item.identifier)
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
        if isinstance(row.item, ExchangeElement):
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
            msg = f"a {item.element_type.value} of this name already exists"
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

    # --- What hangs on the elements: views (and, below, packages) -------------------

    async def _plan_structure(self, model: ExchangeModel, state: _State) -> list[_Row]:
        """Judge in memory everything the second phase will write, then number its rows.

        The first phase is over: which identifiers name an element, and which were refused,
        is final. So a box is resolved here, to an element that exists, and what the database
        is left to refuse is a name taken a moment ago or a lost race, not a bad file.
        """
        rows: list[_Row] = []
        rows.extend(await self._plan_views(model, state))
        return rows

    def _take(self) -> int:
        index = self._position
        self._position += 1
        return index

    async def _plan_views(self, model: ExchangeModel, state: _State) -> list[_Row]:
        rows: list[_Row] = []
        if model.views and self._options.with_views and self._diagrams is not None:
            existing = await self._diagrams.list_diagrams()
            self._existing_diagrams = [(d.name, d.id) for d in existing]
            state.diagram_names.update(d.name for d in existing)
        for view in model.views:
            index = self._take()
            if not self._options.with_views or self._diagrams is None:
                issues: tuple[Issue, ...] = ()
                if self._options.with_views:
                    issues = (
                        Issue(
                            Severity.INFO,
                            "views_not_available",
                            "this deployment cannot store views; ignored",
                            view.identifier,
                            None,
                            view.path,
                        ),
                    )
                self._results[index] = _Result(Category.VIEWS, Outcome.IGNORED, issues)
                continue
            judged = self._judge_view(view, state)
            if isinstance(judged, Issue):
                self._results[index] = _Result(Category.VIEWS, Outcome.REJECTED, (judged,))
            else:
                rows.append(_Row(index, Category.VIEWS, judged))
        return rows

    @staticmethod
    def _judge_view(view: ExchangeView, state: _State) -> Issue | _ViewStep:
        """Why this view can never be written, or the view as the diagram service may take it."""

        def note(severity: Severity, code: str, message: str) -> Issue:
            return Issue(severity, code, message, view.identifier, None, view.path)

        if len(view.boxes) > MAX_VIEW_BOXES:
            return note(
                Severity.ERROR, "view_too_large", f"a view has at most {MAX_VIEW_BOXES} boxes"
            )
        name, description = view.name.strip(), view.description.strip()
        if not name:
            return note(Severity.ERROR, "blank_name", "a view needs a name")
        if "\x00" in name or "\x00" in description:
            return note(Severity.ERROR, "nul_character", "a view cannot contain a NUL character")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            return note(
                Severity.ERROR, "description_too_long", "the description of a view is too long"
            )
        try:
            name = validate_diagram_name(name)
        except ValueError:
            return note(Severity.ERROR, "name_too_long", "the name of a view is too long")
        notes: list[Issue] = list(_ignored_notes(view))
        nodes: list[DiagramNode] = []
        placed: set[UUID] = set()
        for box in view.boxes:
            target = None if box.element in state.refused else state.resolve(box.element)
            if target is None:
                notes.append(
                    note(
                        Severity.WARNING,
                        "box_omitted",
                        "a box shows an element that was not imported; omitted",
                    )
                )
                continue
            try:
                validate_box(box)
            except RowRefusedError as refusal:
                notes.append(
                    note(Severity.WARNING, refusal.code, f"{refusal}; this box is omitted")
                )
                continue
            if target in placed:
                notes.append(
                    note(
                        Severity.WARNING,
                        "box_duplicate",
                        "an element is drawn twice on this view; the first box is kept",
                    )
                )
                continue
            placed.add(target)
            nodes.append(DiagramNode(target, box.x, box.y, box.width, box.height))
        return _ViewStep(view, name, description, tuple(nodes), tuple(notes))

    async def _view(self, row: _Row, step: _ViewStep, state: _State) -> _Result:
        """Write a view, or find it already there: same name (or its import-suffixed copy) and
        the same boxes. Anything else gets the first free name, ` (import N)`."""
        diagrams = self._diagrams
        if diagrams is None:  # pragma: no cover - a step exists only when there is a service
            msg = "no diagram service"
            raise RuntimeError(msg)
        ours = _layout_key(step.nodes)
        family = re.compile(r"\A" + re.escape(step.name) + r"( \(import [0-9]+\))?\Z")
        for name, diagram_id in self._existing_diagrams:
            if (
                family.fullmatch(name)
                and _layout_key((await diagrams.open(diagram_id)).nodes) == ours
            ):
                return _Result(row.category, Outcome.REUSED, step.notes)
        name = _free_name(step.name, state.diagram_names)
        diagram = await diagrams.create(name=name, description=step.description)
        self._ledger.add(state.diagram_names, name)
        await diagrams.replace_layout(diagram.id, list(step.nodes))
        return _Result(row.category, Outcome.CREATED, step.notes)


def _ignored_notes(view: ExchangeView) -> tuple[Issue, ...]:
    notes: list[Issue] = []
    if view.connections_ignored:
        notes.append(
            Issue(
                Severity.INFO,
                "connections_ignored",
                f"{view.connections_ignored} connection(s) not imported: "
                "a view link is never a fact",
                view.identifier,
                None,
                view.path,
            )
        )
    if view.shapes_ignored:
        notes.append(
            Issue(
                Severity.INFO,
                "shapes_ignored",
                f"{view.shapes_ignored} container(s) or label(s) not imported",
                view.identifier,
                None,
                view.path,
            )
        )
    return tuple(notes)


def _layout_key(nodes: Sequence[DiagramNode]) -> frozenset[tuple[UUID, int, int, int, int]]:
    """A layout as an exchange file can say it: whole units, one box per element."""
    return frozenset(
        (n.element_id, round(n.x), round(n.y), round(n.width), round(n.height)) for n in nodes
    )


_NAME_ATTEMPTS: Final = 1_000


def _free_name(base: str, taken: set[str]) -> str:
    """`base`, or `base (import 2)`, `(import 3)`... cut so that the whole fits a diagram name."""
    for attempt in range(1, _NAME_ATTEMPTS):
        suffix = "" if attempt == 1 else f" (import {attempt})"
        name = base[: MAX_DIAGRAM_NAME_LENGTH - len(suffix)] + suffix
        if name not in taken:
            return name
    raise RowRefusedError("name_exhausted", "no free name for this view")
