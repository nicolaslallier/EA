"""Quality rules over the model: orphans, duplicates, naming, missing data.

Pure functions of the elements and links they are given, each identified by a
stable `RuleCode` — a client, a pipeline and the SPA all branch on the code, so
a code is never renamed (docs/adr/0057). Nothing is stored: a stored
result would be a second truth that every edit of an element made stale.

A rule is a judgement about *this* model, not a fact of ArchiMate: what the
metamodel forbids is refused at write time, what it merely makes suspicious is
reported here. Messages name elements in French, since a person reads them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.archimate import ElementType, RelationshipType, permits
from ea.domain.model import PROPERTY_KEY, Element, Relationship

#: Declared here and imported by `api/schemas.py` (a service cannot import the API).
MAX_FINDINGS: Final = 1000
#: How many elements one run takes; beyond it the scope is "too large" (422).
MAX_QUALITY_ELEMENTS: Final = 5000
DEFAULT_SIMILARITY: Final = 0.9
#: Fewer letters than this is an acronym ("CRM", "SAP"), not shouting.
_MIN_SHOUTING_LETTERS: Final = 4


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RuleCode(StrEnum):
    ORPHAN = "orphan"
    DUPLICATE_PROBABLE = "duplicate_probable"
    NAMING = "naming"
    MISSING_PROPERTY = "missing_property"
    NO_DESCRIPTION = "no_description"
    INCONSISTENT_RELATIONSHIP = "inconsistent_relationship"


SEVERITY_OF: Final[Mapping[RuleCode, Severity]] = {
    RuleCode.ORPHAN: Severity.WARNING,
    RuleCode.DUPLICATE_PROBABLE: Severity.WARNING,
    RuleCode.NAMING: Severity.INFO,
    RuleCode.MISSING_PROPERTY: Severity.WARNING,
    RuleCode.NO_DESCRIPTION: Severity.INFO,
    RuleCode.INCONSISTENT_RELATIONSHIP: Severity.ERROR,
}

#: Containers are not expected to be linked to anything.
_EXEMPT_FROM_ORPHAN: Final = frozenset(
    {ElementType.GROUPING, ElementType.LOCATION, ElementType.PLATEAU}
)


@dataclass(frozen=True, slots=True)
class Finding:
    rule: RuleCode
    severity: Severity
    element_id: UUID
    element_name: str
    message: str
    related_id: UUID | None = None
    related_name: str | None = None


@dataclass(frozen=True, slots=True)
class QualityParams:
    """The two knobs: how alike two names must be, and which properties a type must carry."""

    similarity: float = DEFAULT_SIMILARITY
    required_properties: Mapping[ElementType, frozenset[str]] = field(default_factory=dict)
    max_elements: int = MAX_QUALITY_ELEMENTS

    @classmethod
    def from_settings(
        cls, similarity: float, required: Mapping[str, Sequence[str]]
    ) -> QualityParams:
        """Validate the environment's values; raises `ValueError` so boot fails loudly."""
        if not 0.0 <= similarity <= 1.0:
            msg = f"EA_QUALITY_SIMILARITY: similarity must be between 0 and 1, not {similarity}"
            raise ValueError(msg)
        parsed: dict[ElementType, frozenset[str]] = {}
        for type_name, keys in required.items():
            try:
                element_type = ElementType(type_name)
            except ValueError:
                msg = f"EA_QUALITY_REQUIRED names an unknown element type {type_name!r}"
                raise ValueError(msg) from None
            for key in keys:
                if not PROPERTY_KEY.match(key):
                    msg = f"EA_QUALITY_REQUIRED: {key!r} is not a plain property name"
                    raise ValueError(msg)
            parsed[element_type] = frozenset(keys)
        return cls(similarity=similarity, required_properties=parsed)


def _finding(
    rule: RuleCode, element: Element, message: str, related: Element | None = None
) -> Finding:
    return Finding(
        rule=rule,
        severity=SEVERITY_OF[rule],
        element_id=element.id,
        element_name=element.name,
        message=message,
        related_id=related.id if related else None,
        related_name=related.name if related else None,
    )


def _normalise(text: str) -> str:
    """Case, accents and punctuation folded away: `Café-API` and `cafe api` agree."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", bare.casefold()).split())


def check_orphans(
    elements: Sequence[Element], relationships: Sequence[Relationship]
) -> list[Finding]:
    """Elements with no link at all, in or out. `relationships` must touch the scope."""
    linked = {end for link in relationships for end in (link.source_id, link.target_id)}
    return [
        _finding(RuleCode.ORPHAN, element, f"« {element.name} » n'est relié à aucun élément.")
        for element in elements
        if element.element_type not in _EXEMPT_FROM_ORPHAN and element.id not in linked
    ]


def check_descriptions(elements: Sequence[Element]) -> list[Finding]:
    return [
        _finding(RuleCode.NO_DESCRIPTION, element, f"« {element.name} » n'a pas de description.")
        for element in elements
        if not element.description.strip()
    ]


def check_naming(elements: Sequence[Element]) -> list[Finding]:
    """One finding per element, listing every problem its name has."""
    findings: list[Finding] = []
    for element in elements:
        name = element.name
        problems: list[str] = []
        if not name.strip():
            problems.append("le nom est vide")
        if "  " in name:
            problems.append("le nom contient des espaces doubles")
        letters = [c for c in name if c.isalpha()]
        if len(letters) >= _MIN_SHOUTING_LETTERS and name == name.upper():
            problems.append("le nom est tout en capitales")
        type_names = {
            _normalise(element.element_type.label),
            _normalise(element.element_type.value),
        }
        if name.strip() and _normalise(name) in type_names:
            problems.append("le nom répète le type de l'élément")
        if problems:
            findings.append(
                _finding(RuleCode.NAMING, element, f"« {name.strip()} » : {' ; '.join(problems)}.")
            )
    return findings


def check_required_properties(
    elements: Sequence[Element], required: Mapping[ElementType, frozenset[str]]
) -> list[Finding]:
    findings: list[Finding] = []
    for element in elements:
        for key in sorted(required.get(element.element_type, ())):
            if not element.properties.get(key, "").strip():
                findings.append(
                    _finding(
                        RuleCode.MISSING_PROPERTY,
                        element,
                        f"« {element.name} » n'a pas de valeur pour « {key} ».",
                    )
                )
    return findings


def _alike(one: str, other: str, similarity: float) -> bool:
    if one == other:
        return True
    matcher = SequenceMatcher(None, one, other)
    # The two upper bounds are cheap and rule most pairs out before `ratio()`.
    return (
        matcher.real_quick_ratio() >= similarity
        and matcher.quick_ratio() >= similarity
        and matcher.ratio() >= similarity
    )


def check_duplicates(elements: Sequence[Element], similarity: float) -> list[Finding]:
    """Same type, names equal once normalised or at least `similarity` alike.

    Compared per (type, first normalised letter), so a thousand elements are
    not a million pairs. A typo in the *first* letter slips through — accepted:
    this is a warning, never an error, and the threshold is a setting.
    """
    # ponytail: pairwise within a block, bounded by MAX_QUALITY_ELEMENTS;
    # a MinHash/n-gram index if a block ever holds thousands of near names.
    blocks: dict[tuple[ElementType, str], list[tuple[str, Element]]] = {}
    for element in sorted(elements, key=lambda e: (e.name, str(e.id))):
        normalised = _normalise(element.name)
        if normalised:
            blocks.setdefault((element.element_type, normalised[0]), []).append(
                (normalised, element)
            )
    findings: list[Finding] = []
    for block in blocks.values():
        for index, (normalised, later) in enumerate(block):
            for earlier_normalised, earlier in block[:index]:
                if _alike(normalised, earlier_normalised, similarity):
                    findings.append(
                        _finding(
                            RuleCode.DUPLICATE_PROBABLE,
                            later,
                            f"« {later.name} » ressemble à « {earlier.name} ».",
                            earlier,
                        )
                    )
                    break
    return findings


#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT: Final = frozenset({RelationshipType.COMPOSITION, RelationshipType.AGGREGATION})


def _containment_loops(relationships: Sequence[Relationship]) -> list[Relationship]:
    """The containment links that close a loop: back edges of a depth-first walk."""
    edges: dict[UUID, list[Relationship]] = {}
    for link in relationships:
        if link.relationship_type in _CONTAINMENT:
            edges.setdefault(link.source_id, []).append(link)
    on_path, done = 1, 2
    state: dict[UUID, int] = {}
    closing: list[Relationship] = []
    for start in sorted(edges, key=str):
        if start in state:
            continue
        state[start] = on_path
        stack = [(start, iter(edges.get(start, ())))]
        while stack:
            node, pending = stack[-1]
            edge = next(pending, None)
            if edge is None:
                state[node] = done
                stack.pop()
                continue
            seen = state.get(edge.target_id)
            if seen == on_path:
                closing.append(edge)
            elif seen is None:
                state[edge.target_id] = on_path
                stack.append((edge.target_id, iter(edges.get(edge.target_id, ()))))
    return closing


def check_relationships(
    elements: Sequence[Element], relationships: Sequence[Relationship]
) -> list[Finding]:
    """Links the metamodel would refuse today, and containment loops.

    The metamodel may have evolved since a link was stored, or an import may
    have let it through: either way the link exists, so it is reported — never
    deleted. A link whose end is outside `elements` is still judged, from the
    types it stores, and its far end is named by its type.
    """
    by_id = {element.id: element for element in elements}
    findings: list[Finding] = []
    flagged: set[UUID] = set()

    def name_of(end: UUID, stored_type: ElementType) -> str:
        known = by_id.get(end)
        return known.name if known else stored_type.value

    for link in relationships:
        if permits(
            link.relationship_type,
            link.source_type,
            link.target_type,
            same_element=link.source_id == link.target_id,
        ):
            continue
        flagged.add(link.id)
        source_name = name_of(link.source_id, link.source_type)
        target_name = name_of(link.target_id, link.target_type)
        findings.append(
            Finding(
                rule=RuleCode.INCONSISTENT_RELATIONSHIP,
                severity=Severity.ERROR,
                element_id=link.source_id,
                element_name=source_name,
                message=(
                    f"« {source_name} » {link.relationship_type.value} « {target_name} » : "
                    "le métamodèle ne permet plus ce lien."
                ),
                related_id=link.target_id,
                related_name=target_name,
            )
        )
    for link in _containment_loops(relationships):
        if link.id in flagged:
            continue
        source_name = name_of(link.source_id, link.source_type)
        target_name = name_of(link.target_id, link.target_type)
        findings.append(
            Finding(
                rule=RuleCode.INCONSISTENT_RELATIONSHIP,
                severity=Severity.ERROR,
                element_id=link.source_id,
                element_name=source_name,
                message=(
                    f"« {source_name} » {link.relationship_type.value} « {target_name} » "
                    "boucle : la hiérarchie de contenu se referme sur elle-même."
                ),
                related_id=link.target_id,
                related_name=target_name,
            )
        )
    return findings


@dataclass(frozen=True, slots=True)
class RuleCount:
    rule: RuleCode
    severity: Severity
    count: int


@dataclass(frozen=True, slots=True)
class QualityReport:
    findings: tuple[Finding, ...]

    @property
    def counts(self) -> tuple[RuleCount, ...]:
        """One entry per rule, zeros included, so a screen can show what is clean."""
        tally = Counter(finding.rule for finding in self.findings)
        return tuple(RuleCount(rule, SEVERITY_OF[rule], tally.get(rule, 0)) for rule in RuleCode)


_SEVERITY_ORDER: Final = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def run_checks(
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
    params: QualityParams,
) -> QualityReport:
    """Every rule over the scope. `relationships` must be the links touching it."""
    found = [
        *check_orphans(elements, relationships),
        *check_duplicates(elements, params.similarity),
        *check_naming(elements),
        *check_required_properties(elements, params.required_properties),
        *check_descriptions(elements),
        *check_relationships(elements, relationships),
    ]
    found.sort(
        key=lambda f: (
            _SEVERITY_ORDER[f.severity],
            f.element_name.casefold(),
            f.rule.value,
            f.message,
        )
    )
    return QualityReport(findings=tuple(found))


@dataclass(frozen=True, slots=True)
class QualityPage:
    #: The whole report, so counts describe the scope and not the filter.
    report: QualityReport
    findings: tuple[Finding, ...]
    next_cursor: int | None


def page_of(
    report: QualityReport,
    *,
    severity: Severity | None = None,
    code: RuleCode | None = None,
    limit: int = 100,
    cursor: int = 0,
) -> QualityPage:
    kept = [
        finding
        for finding in report.findings
        if (severity is None or finding.severity is severity)
        and (code is None or finding.rule is code)
    ]
    window = tuple(kept[cursor : cursor + limit])
    end = cursor + limit
    return QualityPage(report, window, end if end < len(kept) else None)
