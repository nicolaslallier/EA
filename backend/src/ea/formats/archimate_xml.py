"""ArchiMate Model Exchange File Format (Open Group, XML) — writing.

Hand-written strings rather than a tree: an export streams, and the model is
never held whole. Sections come in the order the XSD requires, and a section
with nothing in it is left out because the schema forbids an empty one.

Every text passes through one escaper (`_Writer.text`): markup is escaped,
characters XML 1.0 forbids are dropped and *counted*, so the caller can say so —
a name that silently lost a character would be a corrupted export that looks fine.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Final

from ea.domain.archimate import AccessType, RelationshipType
from ea.domain.exchange import (
    ACCESS_NAMES,
    DESCRIPTION_KEY,
    EXPORT_LANGUAGE,
    MODEL_IDENTIFIER,
    MODIFIER_KEY,
    NAMESPACE,
    PROPERTY_DEFINITION_PREFIX,
    ExchangeElement,
    ExchangeRelationship,
    exchange_name,
)

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
