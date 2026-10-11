"""What an exchange file holds, and how its names map onto ours.

Pure: no XML, no CSV, no SQL. The ArchiMate rules stay in `domain/archimate/`
and are applied by `ArchitectureService`; this module only translates names
and carries values between a file and the service (docs/adr/0048).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from ea.domain.archimate import AccessType, ElementType, RelationshipType, validate_relationship
from ea.domain.errors import DomainError, IllegalRelationshipError, RowRefusedError
from ea.domain.ipam import validate_ipam_properties
from ea.domain.model import Element, Relationship, is_property_key

MAX_EXCHANGE_BYTES: Final = 20 * 1024 * 1024
EXPORT_PAGE_SIZE: Final = 1_000
EXPORT_TIMEOUT_SECONDS: Final = 300.0
#: The deployment's language; Exchange wants an `xml:lang` on every text.
EXPORT_LANGUAGE: Final = "fr"
#: One namespace for the whole 3.x family; 3.1 and 3.2 differ by schema location.
NAMESPACE: Final = "http://www.opengroup.org/xsd/archimate/3.0/"

#: The `identifier` of the `<model>` itself and the prefix of a property definition's: no
#: element or relationship may take either, or the file would hold one `xs:ID` twice.
MODEL_IDENTIFIER: Final = "id-ea-model"
PROPERTY_DEFINITION_PREFIX: Final = "propid-"
#: The prefix of the identifier a view is given (`view-<name>`): no element or relationship
#: may take a `source_id` starting so, or a view could share its `xs:ID`.
VIEW_IDENTIFIER_PREFIX: Final = "view-"

SOURCE_ID: Final = "source_id"
DESCRIPTION_KEY: Final = "ea.description"
RESERVED_PREFIX: Final = "ea."
MODIFIER_KEY: Final = "modifier"
#: Exchange's two junction types. We cannot hold one (`Element.create` refuses connectors).
JUNCTION_NAMES: Final = frozenset({"AndJunction", "OrJunction"})

_EMPTY: Mapping[str, str] = MappingProxyType({})
_NCNAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_.-]*\Z")
#: What `id-{uuid}` looks like: no `source_id` may mimic it for another object.
_GENERATED = re.compile(r"\Aid-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_NOT_NCNAME = re.compile(r"[^A-Za-z0-9_.-]")


class ExchangeFormat(StrEnum):
    ARCHIMATE_XML = "archimate-xml"
    CSV = "csv"


class ScopeKind(StrEnum):
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class ExportScope:
    kind: ScopeKind = ScopeKind.MODEL
    package_id: UUID | None = None
    query_id: UUID | None = None

    @property
    def label(self) -> str:
        """Used in the download's file name — fixed words, never user input."""
        return self.kind.value


@dataclass(frozen=True, slots=True)
class ExportRequest:
    format: ExchangeFormat = ExchangeFormat.ARCHIMATE_XML
    scope: ExportScope = field(default_factory=ExportScope)
    with_views: bool = True
    #: CSV only: prefix with `'` a cell a spreadsheet would run as a formula (docs/adr/0051).
    safe_cells: bool = True


# --- Names ---------------------------------------------------------------


def pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def exchange_name(kind: ElementType | RelationshipType) -> str:
    """`business_actor` → `BusinessActor`: the `xsi:type` of Exchange."""
    return pascal_case(kind.value)


_ELEMENTS_BY_NAME: Final = {exchange_name(t): t for t in ElementType if not t.is_connector}
_RELATIONSHIPS_BY_NAME: Final = {exchange_name(t): t for t in RelationshipType}

#: `Access`, `Read`, `Write`, `ReadWrite` — derived, never restated.
ACCESS_NAMES: Final = {kind: pascal_case(kind.value) for kind in AccessType}
ACCESS_BY_NAME: Final = {name: kind for kind, name in ACCESS_NAMES.items()}


def element_type_of(name: str) -> ElementType | None:
    return _ELEMENTS_BY_NAME.get(name)


def relationship_type_of(name: str) -> RelationshipType | None:
    return _RELATIONSHIPS_BY_NAME.get(name)


# --- The three CSV files ---------------------------------------------------

ELEMENTS_FILE: Final = "elements.csv"
RELATIONS_FILE: Final = "relations.csv"
PROPERTIES_FILE: Final = "properties.csv"
CSV_FILES: Final = (ELEMENTS_FILE, RELATIONS_FILE, PROPERTIES_FILE)
#: Records (lines of the table, blank ones included) one CSV file may hold.
MAX_CSV_ROWS: Final = 100_000
#: Characters of one record, quoted newlines included. It is below the `csv` module's own
#: field limit (131072) and does not rely on it: that limit is a global state of the module,
#: and the stricter one is applied here, before the reader is handed the text.
MAX_CSV_RECORD_CHARS: Final = 100_000

_RELATIONSHIP_SUFFIX: Final = "Relationship"


def lookup_element_type(raw: str) -> ElementType | None:
    """`BusinessActor` or `business_actor` — Archi writes the first, people the second."""
    found = _ELEMENTS_BY_NAME.get(raw.strip())
    if found is not None:
        return found
    try:
        candidate = ElementType(raw.strip())
    except ValueError:
        return None
    return None if candidate.is_connector else candidate


def lookup_relationship_type(raw: str) -> RelationshipType | None:
    """`CompositionRelationship`, `Composition` or `composition`."""
    name = raw.strip().removesuffix(_RELATIONSHIP_SUFFIX)
    found = _RELATIONSHIPS_BY_NAME.get(name)
    if found is not None:
        return found
    try:
        return RelationshipType(name)
    except ValueError:
        return None


# --- Identifiers ---------------------------------------------------------


def xml_identifier(raw: str) -> str:
    """An `xs:ID`: kept when it already is an NCName, otherwise `id-` + a safe form."""
    if _NCNAME.match(raw):
        return raw
    return "id-" + _NOT_NCNAME.sub("_", raw)


def identifier_of(
    source_id: str | None, uid: UUID
) -> str:  # ordering key only, see IdentifierRegistry
    """What a file calls this element: its `source_id`, or a name made of its UUID."""
    return xml_identifier(source_id) if source_id else f"id-{uid}"


class IdentifierRegistry:
    """Hands out the identifiers of one exported file, each exactly once.

    An `xs:ID` is unique in a file, and a relationship's `source`/`target` point
    at one by name: two objects under the same identifier would make a link
    point at the wrong one. So an object's `source_id` is used only when it is a
    safe XML name, is not shaped like a generated `id-{uuid}` (unless it is this
    very object's), is not reserved, and nobody has it yet; otherwise the object
    gets `id-{uuid}`, which cannot collide — a UUID is unique and no other
    object may use that shape. One set lookup per object: linear in the file.
    """

    def __init__(self, taken: set[str] | None = None) -> None:
        self._taken = taken if taken is not None else set()
        self._taken.add(MODEL_IDENTIFIER)
        #: The objects whose `source_id` could not be used, in the order met.
        self.replaced: list[UUID] = []

    def assign(self, source_id: str | None, uid: UUID) -> str:
        generated = f"id-{uid}"
        if source_id and self._usable(source_id, generated):
            self._taken.add(source_id)
            return source_id
        if source_id:
            self.replaced.append(uid)
        self._taken.add(generated)
        return generated

    def claim(self, preferred: str) -> str:
        """An identifier for something that is not an element or a link (a view): `preferred`,
        or `preferred-2`, `-3`... when taken. Always unique in the file."""
        identifier, number = preferred, 2
        while identifier in self._taken:
            identifier, number = f"{preferred}-{number}", number + 1
        self._taken.add(identifier)
        return identifier

    def _usable(self, source_id: str, generated: str) -> bool:
        if source_id == generated:
            return generated not in self._taken
        return (
            _NCNAME.match(source_id) is not None
            and _GENERATED.match(source_id) is None
            and not source_id.startswith((PROPERTY_DEFINITION_PREFIX, VIEW_IDENTIFIER_PREFIX))
            and source_id not in self._taken
        )


# --- What a file carries -------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExchangeElement:
    identifier: str
    element_type: ElementType
    name: str
    description: str = ""
    documentation: str = ""
    properties: Mapping[str, str] = _EMPTY
    #: Position in the source file (CSV line); XML reports a path instead.
    line: int | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeRelationship:
    identifier: str
    relationship_type: RelationshipType
    source: str
    target: str
    name: str = ""
    access_type: AccessType | None = None
    directed: bool = False
    properties: Mapping[str, str] = _EMPTY
    line: int | None = None
    path: str | None = None


def _without_source_id(properties: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({k: v for k, v in properties.items() if k != SOURCE_ID})


def exchange_element_of(
    element: Element, registry: IdentifierRegistry | None = None
) -> ExchangeElement:
    return ExchangeElement(
        identifier=(registry or IdentifierRegistry()).assign(
            element.properties.get(SOURCE_ID), element.id
        ),
        element_type=element.element_type,
        name=element.name,
        description=element.description,
        documentation=element.documentation,
        properties=_without_source_id(element.properties),
    )


def exchange_relationship_of(
    relationship: Relationship,
    *,
    source: str,
    target: str,
    registry: IdentifierRegistry | None = None,
) -> ExchangeRelationship:
    return ExchangeRelationship(
        identifier=(registry or IdentifierRegistry()).assign(
            relationship.properties.get(SOURCE_ID), relationship.id
        ),
        relationship_type=relationship.relationship_type,
        source=source,
        target=target,
        name=relationship.name,
        access_type=relationship.access_type,
        directed=relationship.directed,
        properties=_without_source_id(relationship.properties),
    )


@dataclass(frozen=True, slots=True)
class ExchangeBox:
    """One element drawn on a view: where, and how big. `element` is a file identifier."""

    element: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class ExchangeView:
    """A view of the file. A view owns no fact: only its boxes are ever stored."""

    identifier: str
    name: str
    boxes: tuple[ExchangeBox, ...]
    description: str = ""
    #: What the reader set aside: a view link is never a fact, a container or label is no element.
    connections_ignored: int = 0
    shapes_ignored: int = 0
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ExportedBox:
    element_id: UUID
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class ExportedView:
    """A saved diagram as the export reads it: its boxes name elements by their database id."""

    id: UUID
    name: str
    description: str
    boxes: tuple[ExportedBox, ...]


# --- What reading a file yields ----------------------------------------------

MAX_EXCHANGE_ITEMS: Final = 50_000
#: Elements nested in one another, `organizations` folders included (200) plus the frame.
MAX_NESTING_DEPTH: Final = 250
MAX_PROPERTIES_PER_OBJECT: Final = 100
MAX_PROPERTY_VALUE_LENGTH: Final = 2_000
#: Amplification: small inputs that become big trees. Nodes under one element or relationship
#: (its texts, its properties and their values, with room to spare).
MAX_NODES_PER_ITEM: Final = 4 * MAX_PROPERTIES_PER_OBJECT + 64
MAX_ATTRIBUTES_PER_NODE: Final = 100
#: Nodes started since the reader last let go of what it had handled, wherever they are.
MAX_LIVE_NODES: Final = 100_000
#: Bytes read without one start or end tag completing: a tag (or a text) this long is not an
#: exchange file, and the parser would build it whole before telling us.
MAX_SILENT_BYTES: Final = 1024 * 1024


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Category(StrEnum):
    ELEMENTS = "elements"
    RELATIONSHIPS = "relationships"
    PACKAGES = "packages"
    VIEWS = "views"


@dataclass(frozen=True, slots=True)
class Issue:
    """One line of the import report. `path` locates it in an XML file, `line` in a CSV."""

    severity: Severity
    code: str
    message: str
    source_id: str | None = None
    line: int | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class Unreadable:
    """A row the reader could not turn into an element or link, set aside for the report.

    `ignored` marks what is deliberately not imported (a junction) as opposed to
    refused. `cascade` is false for a repeated identifier: refusing the second
    must not refuse the first, which is the row everything points at.
    """

    category: Category
    identifier: str
    ignored: bool = False
    cascade: bool = True


@dataclass(frozen=True, slots=True)
class ExchangeModel:
    elements: tuple[ExchangeElement, ...] = ()
    relationships: tuple[ExchangeRelationship, ...] = ()
    unreadable: tuple[Unreadable, ...] = ()
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True, slots=True)
class FoldedProperties:
    properties: Mapping[str, str]
    description: str
    issues: tuple[Issue, ...]


def fold_properties(pairs: Iterable[tuple[str, str]]) -> FoldedProperties:
    """Sort a file's properties into what is stored, the description, and what is reported.

    `ea.description` is the description; any other `ea.*` and `source_id` belong
    to the exchange itself; a key the model would refuse is dropped, never
    renamed (two keys could collide); the last of a repeated key wins.
    """
    properties: dict[str, str] = {}
    description = ""
    issues: list[Issue] = []
    for key, value in pairs:
        if key == DESCRIPTION_KEY:
            description = value
        elif key.startswith(RESERVED_PREFIX) or key == SOURCE_ID:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "reserved_property_dropped",
                    "a property named like one of the exchange's own is not stored",
                )
            )
        elif not is_property_key(key):
            issues.append(
                Issue(
                    Severity.WARNING,
                    "invalid_property_key",
                    "a property whose name is not a plain identifier was dropped",
                )
            )
        else:
            if key in properties:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        "duplicate_property",
                        f"property {key!r} appears twice; the last value wins",
                    )
                )
            properties[key] = value
    return FoldedProperties(MappingProxyType(properties), description, tuple(issues))


# --- Importing -----------------------------------------------------------------

#: Lines of the report kept per category; the counts stay exact whatever is cut.
MAX_REPORT_ISSUES: Final = 1_000
#: The HTTP schema's bounds (`api/schemas.py`), restated here because the domain may not
#: import it; `test_the_limits_are_the_ones_the_http_schema_declares` ties the two together.
MAX_NAME_LENGTH: Final = 200
MAX_DESCRIPTION_LENGTH: Final = 2_000
MAX_DOCUMENTATION_LENGTH: Final = 20_000


class ConflictPolicy(StrEnum):
    REUSE = "reuse"
    FAIL = "fail"


class Outcome(StrEnum):
    CREATED = "created"
    REUSED = "reused"
    REJECTED = "rejected"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class ImportOptions:
    dry_run: bool = True
    on_conflict: ConflictPolicy = ConflictPolicy.REUSE
    strict: bool = False
    #: CSV only: give back a cell the export had prefixed with `'` (docs/adr/0051).
    safe_cells: bool = True


def digest_of(*parts: bytes) -> str:
    """SHA-256 of one file as `shasum` would print it; several files, length-prefixed."""
    if len(parts) == 1:
        return hashlib.sha256(parts[0]).hexdigest()
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ImportContent:
    """The bytes received: one XML file, or `elements.csv` with its companions."""

    format: ExchangeFormat
    main: bytes
    relations: bytes | None = None
    properties: bytes | None = None

    @property
    def parts(self) -> tuple[bytes, ...]:
        return tuple(p for p in (self.main, self.relations, self.properties) if p is not None)

    @property
    def size(self) -> int:
        return sum(len(part) for part in self.parts)

    @property
    def sha256(self) -> str:
        """`shasum` of the file when there is only one; otherwise each file in its own slot,
        so that the same bytes sent as `relations` and as `properties` do not look alike."""
        if self.relations is None and self.properties is None:
            return digest_of(self.main)
        return digest_of(self.main, self.relations or b"", self.properties or b"")


@dataclass(frozen=True, slots=True)
class Counts:
    created: int = 0
    reused: int = 0
    rejected: int = 0
    ignored: int = 0


@dataclass(frozen=True, slots=True)
class ImportReport:
    dry_run: bool
    #: True only when the transaction was committed: a dry run and a refused
    #: `strict` import both leave it False, and both wrote nothing.
    applied: bool
    format: ExchangeFormat
    sha256: str
    counts: Mapping[Category, Counts]
    issues: tuple[Issue, ...]
    #: True when `issues` is shorter than `issue_count`: the counts above stay exact.
    truncated: bool
    #: Every line the import produced, kept or not.
    issue_count: int = 0
    #: How long the import took, so that a person can tell a simulation's cost from a minute's.
    duration_ms: int = 0


#: What a line of the report may show of the file: the report is read by people and by
#: terminals, and the file is hostile (a 5 MB identifier, a forged log line, a bidi override).
MAX_REPORT_TEXT: Final = 200


def reportable(text: str) -> str:
    """At most `MAX_REPORT_TEXT` characters, without control or format characters (Cc, Cf)."""
    head = text[: MAX_REPORT_TEXT * 2]  # cut before scanning: the text may be megabytes
    clean = "".join(c for c in head if unicodedata.category(c) not in ("Cc", "Cf"))
    if len(clean) <= MAX_REPORT_TEXT and len(text) == len(head):
        return clean
    return clean[: MAX_REPORT_TEXT - 1] + "…"


class ReportBuilder:
    """Counts every row, keeps the first `MAX_REPORT_ISSUES` lines of each category."""

    def __init__(self) -> None:
        self._counts = {category: dict.fromkeys(Outcome, 0) for category in Category}
        self._issues: list[Issue] = []
        self._kept = dict.fromkeys(Category, 0)
        self._total = 0

    def count(self, category: Category, outcome: Outcome) -> None:
        self._counts[category][outcome] += 1

    def add(
        self, issue: Issue, category: Category = Category.ELEMENTS, *, always: bool = False
    ) -> None:
        self._total += 1
        if self._kept[category] >= MAX_REPORT_ISSUES and not always:
            return
        issue = replace(
            issue,
            message=reportable(issue.message),
            source_id=None if issue.source_id is None else reportable(issue.source_id),
            path=None if issue.path is None else reportable(issue.path),
        )
        self._kept[category] += 1
        self._issues.append(issue)

    def has_rejections(self) -> bool:
        return any(by[Outcome.REJECTED] for by in self._counts.values())

    def build(
        self,
        *,
        dry_run: bool,
        applied: bool,
        format: ExchangeFormat,  # noqa: A002
        sha256: str,
        duration_ms: int = 0,
    ) -> ImportReport:
        counts = {
            category: Counts(**{outcome.value: n for outcome, n in by.items()})
            for category, by in self._counts.items()
        }
        return ImportReport(
            dry_run,
            applied,
            format,
            sha256,
            counts,
            tuple(self._issues),
            truncated=len(self._issues) < self._total,
            issue_count=self._total,
            duration_ms=duration_ms,
        )


def _check_text(value: str, what: str) -> None:
    if "\x00" in value:
        raise RowRefusedError("nul_character", f"{what} cannot contain a NUL character")


def _check_identifier(identifier: str) -> None:
    """A file's identifier is stored as the `source_id` property: it is a value like any other."""
    _check_text(identifier, "an identifier")
    if len(identifier) > MAX_PROPERTY_VALUE_LENGTH:
        raise RowRefusedError(
            "identifier_too_long",
            f"an identifier is at most {MAX_PROPERTY_VALUE_LENGTH} characters",
        )


def _check_properties(properties: Mapping[str, str]) -> None:
    if len(properties) > MAX_PROPERTIES_PER_OBJECT:
        raise RowRefusedError(
            "too_many_properties", f"an object has at most {MAX_PROPERTIES_PER_OBJECT} properties"
        )
    for key, value in properties.items():
        if key == SOURCE_ID:
            raise RowRefusedError("reserved_property_key", "'source_id' is the exchange's own")
        if not is_property_key(key):
            msg = "a property name is not a plain identifier"
            raise RowRefusedError("invalid_property_key", msg)
        _check_text(value, "a property value")
        if len(value) > MAX_PROPERTY_VALUE_LENGTH:
            raise RowRefusedError(
                "property_value_too_long",
                f"a property value is at most {MAX_PROPERTY_VALUE_LENGTH} characters",
            )


def _check_name(name: str) -> None:
    if any(unicodedata.category(c) == "Cc" for c in name):
        raise RowRefusedError("control_character", "a name cannot contain control characters")


def validate_element(item: ExchangeElement) -> None:
    """Refuse an element the HTTP schema would refuse (same bounds, tested equal)."""
    if not item.name.strip():
        raise RowRefusedError("blank_name", "an element name cannot be blank")
    if len(item.name.strip()) > MAX_NAME_LENGTH:
        raise RowRefusedError("name_too_long", f"a name is at most {MAX_NAME_LENGTH} characters")
    if len(item.description.strip()) > MAX_DESCRIPTION_LENGTH:
        raise RowRefusedError(
            "description_too_long", f"a description is at most {MAX_DESCRIPTION_LENGTH} characters"
        )
    if len(item.documentation.strip()) > MAX_DOCUMENTATION_LENGTH:
        raise RowRefusedError(
            "documentation_too_long",
            f"documentation is at most {MAX_DOCUMENTATION_LENGTH} characters",
        )
    for text in (item.name, item.description, item.documentation):
        _check_text(text, "a text")
    _check_name(item.name)
    _check_identifier(item.identifier)
    _check_properties(item.properties)


def validate_link(
    item: ExchangeRelationship,
    source_type: ElementType,
    target_type: ElementType,
    *,
    same_element: bool,
) -> None:
    """Refuse, without touching the database, what the metamodel or a bound forbids."""
    if len(item.name.strip()) > MAX_NAME_LENGTH:
        raise RowRefusedError("name_too_long", f"a name is at most {MAX_NAME_LENGTH} characters")
    _check_text(item.name, "a name")
    _check_name(item.name)
    _check_identifier(item.identifier)
    _check_properties(item.properties)
    try:
        validate_relationship(
            item.relationship_type, source_type, target_type, same_element=same_element
        )
    except IllegalRelationshipError as refusal:
        raise RowRefusedError("relationship_not_allowed", str(refusal)) from None


def accept_element(item: ExchangeElement) -> ExchangeElement:
    """The only way an element of a file reaches a service: judged, then handed back clean.

    Every rule the domain has applies — bounds, NUL, control characters, property names, the
    IPAM rules of any address or prefix — and what comes back is a normalised copy (stripped
    texts, the canonical spelling of an address, a read-only property map). A refusal is a
    `RowRefusedError` with a stable code, whatever raised it.
    """
    validate_element(item)
    try:
        canonical = validate_ipam_properties(item.element_type, item.properties)
    except (DomainError, ValueError) as refusal:
        raise RowRefusedError("invalid_ipam_property", str(refusal)) from None
    return replace(
        item,
        name=item.name.strip(),
        description=item.description.strip(),
        documentation=item.documentation.strip(),
        properties=MappingProxyType(dict(canonical or {})),
    )


def accept_link(
    item: ExchangeRelationship,
    source_type: ElementType,
    target_type: ElementType,
    *,
    same_element: bool,
) -> ExchangeRelationship:
    """`accept_element` for a link: the matrix and the bounds, then a clean copy."""
    validate_link(item, source_type, target_type, same_element=same_element)
    return replace(item, name=item.name.strip(), properties=MappingProxyType(dict(item.properties)))
