"""ArchiMate Model Exchange File Format (Open Group, XML) — writing and reading.

Hand-written strings rather than a tree: an export streams, and the model is
never held whole. Sections come in the order the XSD requires, and a section
with nothing in it is left out because the schema forbids an empty one.

Every text passes through one escaper (`_Writer.text`): markup is escaped,
characters XML 1.0 forbids are dropped and *counted*, so the caller can say so —
a name that silently lost a character would be a corrupted export that looks fine.

Reading is the other direction and the dangerous one: the file is hostile input.
It is parsed by `defusedxml` alone, as a stream (`iterparse`), never as a whole
tree: every bound — bytes (the caller's), items, nesting, properties per object —
is enforced while reading, and what has been handled is dropped from memory, so a
20 MB file of empty tags costs a counter and not a tree (docs/adr/0048, 0049).
A refusal never repeats the file: a line number at most.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Final

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, iterparse

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import InvalidExchangeFileError
from ea.domain.exchange import (
    ACCESS_BY_NAME,
    ACCESS_NAMES,
    DESCRIPTION_KEY,
    EXPORT_LANGUAGE,
    JUNCTION_NAMES,
    MAX_EXCHANGE_ITEMS,
    MAX_NESTING_DEPTH,
    MAX_PROPERTIES_PER_OBJECT,
    MODEL_IDENTIFIER,
    MODIFIER_KEY,
    NAMESPACE,
    PROPERTY_DEFINITION_PREFIX,
    Category,
    ExchangeElement,
    ExchangeModel,
    ExchangeRelationship,
    Issue,
    Severity,
    Unreadable,
    element_type_of,
    exchange_name,
    fold_properties,
    relationship_type_of,
)

if TYPE_CHECKING:
    # Annotation only — nothing is parsed with it; every parse goes through defusedxml.
    from xml.etree.ElementTree import Element  # nosec B405

_XSI: Final = "http://www.w3.org/2001/XMLSchema-instance"
_LOCATION: Final = "http://www.opengroup.org/xsd/archimate/3.1/archimate3_Diagram.xsd"
#: The `Char` production of XML 1.0: tab, LF, CR, and the planes minus the surrogates and
#: U+FFFE / U+FFFF. A lone surrogate (which Python strings can hold) falls outside it too.
_ILLEGAL: Final = re.compile("[^\t\n\r -퟿-�\U00010000-\U0010ffff]")
_ESCAPES: Final = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\r": "&#13;"}
#: A parser normalises a line break or a tab inside an attribute to a space.
_ATTRIBUTE_ESCAPES: Final = {**_ESCAPES, '"': "&quot;", "\n": "&#10;", "\t": "&#9;"}

DroppedReport = Callable[[str, int], None]


def _ignore(identifier: str, count: int) -> None:
    del identifier, count


class _Writer:
    def __init__(self, on_dropped: DroppedReport) -> None:
        self._on_dropped = on_dropped
        self._dropped = 0
        self.used: set[str] = set()

    def _clean(self, value: str) -> str:
        cleaned, count = _ILLEGAL.subn("", value)
        self._dropped += count
        return cleaned

    def text(self, value: str) -> str:
        return "".join(_ESCAPES.get(c, c) for c in self._clean(value))

    def attr(self, value: str) -> str:
        return "".join(_ATTRIBUTE_ESCAPES.get(c, c) for c in self._clean(value))

    def lang(self, tag: str, value: str) -> str:
        return f'<{tag} xml:lang="{EXPORT_LANGUAGE}">{self.text(value)}</{tag}>'

    def properties(self, properties: Mapping[str, str]) -> str:
        if not properties:
            return ""
        rows = []
        for key in sorted(properties):
            self.used.add(key)
            rows.append(
                f'<property propertyDefinitionRef="{self.attr(PROPERTY_DEFINITION_PREFIX + key)}">'
                f"{self.lang('value', properties[key])}</property>"
            )
        return "<properties>" + "".join(rows) + "</properties>"

    def settle(self, identifier: str) -> None:
        """Report what was dropped while writing the item `identifier`, then start afresh."""
        if self._dropped:
            self._on_dropped(identifier, self._dropped)
            self._dropped = 0

    def element(self, item: ExchangeElement) -> str:
        properties = dict(item.properties)
        if item.description:
            properties[DESCRIPTION_KEY] = item.description
        body = self.lang("name", item.name)
        if item.documentation:
            body += self.lang("documentation", item.documentation)
        body += self.properties(properties)
        xml = (
            f'<element identifier="{self.attr(item.identifier)}" '
            f'xsi:type="{exchange_name(item.element_type)}">{body}</element>'
        )
        self.settle(item.identifier)
        return xml

    def relationship(self, item: ExchangeRelationship) -> str:
        properties = dict(item.properties)
        attributes = (
            f'identifier="{self.attr(item.identifier)}" source="{self.attr(item.source)}" '
            f'target="{self.attr(item.target)}" xsi:type="{exchange_name(item.relationship_type)}"'
        )
        if item.relationship_type is RelationshipType.ACCESS:
            attributes += f' accessType="{ACCESS_NAMES[item.access_type or AccessType.ACCESS]}"'
        if item.relationship_type is RelationshipType.ASSOCIATION and item.directed:
            attributes += ' isDirected="true"'
        if item.relationship_type is RelationshipType.INFLUENCE and MODIFIER_KEY in properties:
            attributes += f' modifier="{self.attr(properties.pop(MODIFIER_KEY))}"'
        body = (self.lang("name", item.name) if item.name else "") + self.properties(properties)
        xml = f"<relationship {attributes}>{body}</relationship>"
        self.settle(item.identifier)
        return xml


async def render_model(
    name: str,
    elements: AsyncIterator[ExchangeElement],
    relationships: AsyncIterator[ExchangeRelationship],
    *,
    on_dropped: DroppedReport = _ignore,
) -> AsyncIterator[str]:
    """The whole document as text pieces, pulling from the two iterators in turn.

    `on_dropped(identifier, count)` is called for each item in which characters
    XML 1.0 forbids had to be removed; it never receives the text itself.
    """
    writer = _Writer(on_dropped)
    yield (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model xmlns="{NAMESPACE}" xmlns:xsi="{_XSI}" '
        f'xsi:schemaLocation="{NAMESPACE} {_LOCATION}" '
        f'identifier="{MODEL_IDENTIFIER}">{writer.lang("name", name)}'
    )
    writer.settle("model")
    opened = False
    async for item in elements:
        if not opened:
            yield "<elements>"
            opened = True
        yield writer.element(item)
    if opened:
        yield "</elements>"
    opened = False
    async for link in relationships:
        if not opened:
            yield "<relationships>"
            opened = True
        yield writer.relationship(link)
    if opened:
        yield "</relationships>"
    if writer.used:
        yield "<propertyDefinitions>"
        for key in sorted(writer.used):
            yield (
                f'<propertyDefinition identifier="{writer.attr(PROPERTY_DEFINITION_PREFIX + key)}"'
                f' type="string">{writer.lang("name", key)}</propertyDefinition>'
            )
        yield "</propertyDefinitions>"
    yield "</model>\n"


async def buffered(parts: AsyncIterator[str], limit: int = 65_536) -> AsyncIterator[bytes]:
    """Group text pieces into UTF-8 chunks of about `limit` characters.

    Grouped as text and encoded last, so a chunk boundary can never fall inside
    a multi-byte character.
    """
    pending: list[str] = []
    size = 0
    async for part in parts:
        pending.append(part)
        size += len(part)
        if size >= limit:
            yield "".join(pending).encode("utf-8")
            pending, size = [], 0
    if pending:
        yield "".join(pending).encode("utf-8")


# --- Reading ------------------------------------------------------------------

_XSI_TYPE: Final = f"{{{_XSI}}}type"
_NS_PREFIX: Final = "http://www.opengroup.org/xsd/archimate/"


def _q(tag: str) -> str:
    return f"{{{NAMESPACE}}}{tag}"


def _refuse(message: str) -> InvalidExchangeFileError:
    return InvalidExchangeFileError(message)


@dataclass(slots=True)
class _Draft:
    """A row read from the file whose properties wait for the definitions at its end."""

    identifier: str
    path: str
    name: str
    documentation: str
    pairs: list[tuple[str, str]]
    issues: list[Issue]
    element_type: ElementType | None = None
    relationship_type: RelationshipType | None = None
    source: str = ""
    target: str = ""
    access_type: AccessType | None = None
    directed: bool = False
    modifier: str = ""


#: What a slot of the file holds once read: a row to build, or a row to report as unreadable.
_Slot = _Draft | tuple[Unreadable, Issue]


@dataclass(slots=True)
class _Reading:
    seen: set[str] = field(default_factory=set)
    elements: list[_Slot] = field(default_factory=list)
    links: list[_Slot] = field(default_factory=list)
    definitions: dict[str, str] = field(default_factory=dict)
    items: int = 0

    def count_item(self) -> None:
        self.items += 1
        if self.items > MAX_EXCHANGE_ITEMS:
            raise _refuse(f"an exchange file holds at most {MAX_EXCHANGE_ITEMS} elements and links")


def _check_root(tag: str) -> None:
    if tag == _q("model"):
        return
    if tag.startswith(f"{{{_NS_PREFIX}") and tag.endswith("}model"):
        raise _refuse("unsupported exchange namespace; only the 3.x namespace is read")
    raise _refuse("not an ArchiMate exchange file: the root element is not 'model'")


def _text_of(node: Element, tag: str, issues: list[Issue]) -> str:
    found = node.findall(_q(tag))
    if len(found) > 1:
        issues.append(
            Issue(Severity.WARNING, "multiple_languages", f"several <{tag}> elements; first kept")
        )
    return (found[0].text or "").strip() if found else ""


def _pairs_of(node: Element) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for prop in node.findall(f"{_q('properties')}/{_q('property')}"):
        value = prop.find(_q("value"))
        pairs.append(
            (
                prop.get("propertyDefinitionRef") or "",
                (value.text or "") if value is not None else "",
            )
        )
    return pairs


def _unreadable(
    category: Category,
    identifier: str,
    path: str,
    code: str,
    message: str,
    *,
    shown: str | None = None,
    cascade: bool = True,
) -> _Slot:
    issue = Issue(Severity.ERROR, code, message, shown, path=path)
    return Unreadable(category, identifier, cascade=cascade), issue


def _read_element(node: Element, index: int, reading: _Reading) -> _Slot:
    path = f"elements/element[{index}]"
    identifier = node.get("identifier") or ""
    kind = node.get(_XSI_TYPE) or ""
    if not identifier:
        return _unreadable(
            Category.ELEMENTS,
            f"element[{index}]",
            path,
            "missing_identifier",
            "an element has no identifier",
        )
    if identifier in reading.seen:
        return _unreadable(
            Category.ELEMENTS,
            identifier,
            path,
            "duplicate_identifier",
            f"identifier {identifier!r} is already used; this one is ignored",
            shown=identifier,
            cascade=False,
        )
    reading.seen.add(identifier)
    if kind in JUNCTION_NAMES:
        issue = Issue(
            Severity.INFO,
            "junction_not_supported",
            "junctions cannot be stored; this one is ignored",
            identifier,
            path=path,
        )
        return Unreadable(Category.ELEMENTS, identifier, ignored=True), issue
    element_type = element_type_of(kind)
    if element_type is None:
        return _unreadable(
            Category.ELEMENTS,
            identifier,
            path,
            "unknown_type",
            "this is not an ArchiMate element type",
            shown=identifier,
        )
    issues: list[Issue] = []
    return _Draft(
        identifier,
        path,
        _text_of(node, "name", issues),
        _text_of(node, "documentation", issues),
        _pairs_of(node),
        issues,
        element_type=element_type,
    )


def _read_relationship(node: Element, index: int, reading: _Reading) -> _Slot:
    path = f"relationships/relationship[{index}]"
    identifier = node.get("identifier") or ""
    if not identifier:
        return _unreadable(
            Category.RELATIONSHIPS,
            f"relationship[{index}]",
            path,
            "missing_identifier",
            "a relationship has no identifier",
        )
    if identifier in reading.seen:
        return _unreadable(
            Category.RELATIONSHIPS,
            identifier,
            path,
            "duplicate_identifier",
            f"identifier {identifier!r} is already used; this one is ignored",
            shown=identifier,
            cascade=False,
        )
    reading.seen.add(identifier)
    relationship_type = relationship_type_of(node.get(_XSI_TYPE) or "")
    if relationship_type is None:
        return _unreadable(
            Category.RELATIONSHIPS,
            identifier,
            path,
            "unknown_type",
            "this is not an ArchiMate relationship type",
            shown=identifier,
        )
    source, target = node.get("source") or "", node.get("target") or ""
    if not source or not target:
        return _unreadable(
            Category.RELATIONSHIPS,
            identifier,
            path,
            "missing_end",
            "a relationship needs a source and a target",
            shown=identifier,
        )
    issues: list[Issue] = []
    draft = _Draft(
        identifier,
        path,
        _text_of(node, "name", issues),
        "",
        _pairs_of(node),
        issues,
        relationship_type=relationship_type,
        source=source,
        target=target,
    )
    if relationship_type is RelationshipType.ACCESS:
        draft.access_type = ACCESS_BY_NAME.get(node.get("accessType") or "Write")  # schema default
        if draft.access_type is None:
            issues.append(
                Issue(
                    Severity.WARNING, "unknown_access_type", "unknown accessType; 'write' is used"
                )
            )
            draft.access_type = AccessType.WRITE
    draft.directed = relationship_type is RelationshipType.ASSOCIATION and node.get(
        "isDirected"
    ) in ("true", "1")
    if relationship_type is RelationshipType.INFLUENCE:
        draft.modifier = node.get("modifier") or ""
    if node.find(_q("documentation")) is not None:
        issues.append(
            Issue(
                Severity.INFO,
                "documentation_dropped",
                "a relationship has no documentation field; it is not stored",
            )
        )
    return draft


def _too_many_properties(slot: _Slot, category: Category) -> _Slot:
    if isinstance(slot, tuple) or len(slot.pairs) <= MAX_PROPERTIES_PER_OBJECT:
        return slot
    return _unreadable(
        category,
        slot.identifier,
        slot.path,
        "too_many_properties",
        f"an object has at most {MAX_PROPERTIES_PER_OBJECT} properties",
        shown=slot.identifier,
    )


def _events(data: bytes) -> Iterator[tuple[str, Element]]:
    try:
        yield from iterparse(
            BytesIO(data),
            events=("start", "end", "pi"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException:
        raise _refuse(
            "DOCTYPE declarations, entities and external references are not allowed"
        ) from None
    except ParseError as error:
        position = getattr(error, "position", None)
        where = f" (line {position[0]})" if position else ""
        raise _refuse(f"the file is not well-formed XML{where}") from None
    except (ValueError, LookupError):
        raise _refuse("the file cannot be decoded as XML") from None


def _read(data: bytes) -> _Reading:
    reading = _Reading()
    tags: list[str] = []
    nodes: list[Element] = []
    counts = {"element": 0, "relationship": 0}
    for event, node in _events(data):
        if event == "pi":
            raise _refuse("processing instructions are not allowed")
        if event == "start":
            if not nodes:
                _check_root(node.tag)
            tags.append(node.tag)
            nodes.append(node)
            if len(nodes) > MAX_NESTING_DEPTH:
                raise _refuse(f"elements are nested more than {MAX_NESTING_DEPTH} levels deep")
            continue
        depth = len(nodes)
        if depth == 3:
            section, tag = tags[1], tags[2]
            if section == _q("elements") and tag == _q("element"):
                reading.count_item()
                counts["element"] += 1
                slot = _read_element(node, counts["element"], reading)
                reading.elements.append(_too_many_properties(slot, Category.ELEMENTS))
            elif section == _q("relationships") and tag == _q("relationship"):
                reading.count_item()
                counts["relationship"] += 1
                slot = _read_relationship(node, counts["relationship"], reading)
                reading.links.append(_too_many_properties(slot, Category.RELATIONSHIPS))
            elif section == _q("propertyDefinitions") and tag == _q("propertyDefinition"):
                _read_definition(node, reading)
                tags.pop(), nodes.pop()
                continue  # the siblings of a definition are the other definitions: keep them
            nodes[1].clear()  # forget what has been handled: memory stays flat
        elif depth == 2:
            nodes[0].clear()
        tags.pop()
        nodes.pop()
    return reading


def _read_definition(node: Element, reading: _Reading) -> None:
    name = node.find(_q("name"))
    identifier = node.get("identifier")
    if identifier and name is not None:
        if len(reading.definitions) >= MAX_EXCHANGE_ITEMS:
            raise _refuse(
                f"an exchange file holds at most {MAX_EXCHANGE_ITEMS} property definitions"
            )
        reading.definitions[identifier] = (name.text or "").strip()
    node.clear()


def _resolved(
    draft: _Draft, definitions: dict[str, str]
) -> tuple[list[tuple[str, str]], list[Issue]]:
    """Name each property by its definition; one that points nowhere is reported, not guessed."""
    pairs: list[tuple[str, str]] = []
    issues: list[Issue] = []
    for ref, value in draft.pairs:
        key = definitions.get(ref)
        if key is None:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "unknown_property_definition",
                    "a property refers to a definition the file does not declare",
                )
            )
        else:
            pairs.append((key, value))
    return pairs, issues


def parse_model(data: bytes) -> ExchangeModel:
    """Read an exchange file; raise `InvalidExchangeFileError` only if nothing can be read."""
    if not data.strip():
        raise _refuse("the file is empty")
    reading = _read(data)
    elements: list[ExchangeElement] = []
    links: list[ExchangeRelationship] = []
    unreadable: list[Unreadable] = []
    issues: list[Issue] = []
    for slot in (*reading.elements, *reading.links):
        if isinstance(slot, tuple):
            unreadable.append(slot[0])
            issues.append(slot[1])
            continue
        pairs, missing = _resolved(slot, reading.definitions)
        folded = fold_properties(pairs)
        for issue in (*slot.issues, *missing, *folded.issues):
            issues.append(_located(issue, slot))
        if slot.element_type is not None:
            elements.append(
                ExchangeElement(
                    identifier=slot.identifier,
                    element_type=slot.element_type,
                    name=slot.name,
                    description=folded.description,
                    documentation=slot.documentation,
                    properties=folded.properties,
                    path=slot.path,
                )
            )
        elif slot.relationship_type is not None:
            properties = dict(folded.properties)
            if slot.modifier:
                properties[MODIFIER_KEY] = slot.modifier
            links.append(
                ExchangeRelationship(
                    identifier=slot.identifier,
                    relationship_type=slot.relationship_type,
                    source=slot.source,
                    target=slot.target,
                    name=slot.name,
                    access_type=slot.access_type,
                    directed=slot.directed,
                    properties=properties,
                    path=slot.path,
                )
            )
    return ExchangeModel(tuple(elements), tuple(links), tuple(unreadable), tuple(issues))


def _located(issue: Issue, draft: _Draft) -> Issue:
    return Issue(issue.severity, issue.code, issue.message, draft.identifier, path=draft.path)
