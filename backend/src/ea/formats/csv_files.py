"""Archi-style CSV (docs/adr/0051): `elements.csv`, `relations.csv` and `properties.csv`.

RFC 4180: UTF-8, comma, every cell quoted, CRLF. A cell that a spreadsheet would run as
a formula is prefixed with `'` on the way out and given back on the way in.

Reading is the dangerous direction: the files are hostile input. They are read as a
stream, record by record, and every bound (records, characters per record, items) is
applied *while reading*, so the first crossed bound stops the reader. A refusal never
repeats the file: a file name and a line number at most.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from typing import Final

from ea.domain.archimate import RelationshipType
from ea.domain.exchange import (
    DESCRIPTION_KEY,
    ELEMENTS_FILE,
    PROPERTIES_FILE,
    RELATIONS_FILE,
    ExchangeElement,
    ExchangeRelationship,
    exchange_name,
)

ELEMENTS_HEADER: Final = ("ID", "Type", "Name", "Documentation", "Specialization")
RELATIONS_HEADER: Final = (
    "ID",
    "Type",
    "Name",
    "Documentation",
    "Source",
    "Target",
    "Specialization",
)
PROPERTIES_HEADER: Final = ("ID", "Key", "Value")
#: The relationship's own attributes travel as properties under the exchange's reserved
#: prefix: no user property can be called that, so none can be mistaken for them.
ACCESS_TYPE_KEY: Final = "ea.access_type"
DIRECTED_KEY: Final = "ea.directed"
_FORMULA: Final = "=+-@"
_GUARD: Final = "'"


# --- Formulas -----------------------------------------------------------------


def _runs_as_formula(value: str) -> bool:
    """Whether a spreadsheet may read `value` as a formula.

    A leading tab or carriage return is dangerous by itself; otherwise the first character
    that is not a blank, a control or an invisible format character (zero-width space, BOM,
    bidi override...) decides: some applications skip those before looking for `=`.
    """
    if value[:1] in ("\t", "\r"):
        return True
    for character in value:
        if character.isspace() or unicodedata.category(character) in ("Cc", "Cf"):
            continue
        return character in _FORMULA
    return False


def guard_cell(value: str) -> str:
    """Prefix a cell a spreadsheet would run as a formula with `'`.

    The test is on the value without its own leading `'`s, so a cell that already starts
    with `'` before such a sign is prefixed too: `unguard_cell` removes exactly one `'` in
    exactly those cases and gives every value back unchanged.
    """
    return _GUARD + value if _runs_as_formula(value.lstrip(_GUARD)) else value


def unguard_cell(value: str) -> str:
    if value.startswith(_GUARD) and _runs_as_formula(value.lstrip(_GUARD)):
        return value[1:]
    return value


def _identity(value: str) -> str:
    return value


# --- Writing --------------------------------------------------------------------


def _row(cells: Sequence[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerow(cells)
    return buffer.getvalue()


def _property_rows(
    leading: Sequence[tuple[str, str]], properties: Mapping[str, str]
) -> Iterator[tuple[str, str]]:
    yield from leading
    for key in sorted(properties):
        yield key, properties[key]


async def render_csv(
    elements: AsyncIterator[ExchangeElement],
    relationships: AsyncIterator[ExchangeRelationship],
    *,
    safe_cells: bool,
) -> AsyncIterator[tuple[str, str]]:
    """The three files as `(file name, text)` pieces, a consumer routing by name."""
    guard: Callable[[str], str] = guard_cell if safe_cells else _identity
    yield ELEMENTS_FILE, _row(ELEMENTS_HEADER)
    yield RELATIONS_FILE, _row(RELATIONS_HEADER)
    yield PROPERTIES_FILE, _row(PROPERTIES_HEADER)
    async for item in elements:
        identifier = guard(item.identifier)
        yield (
            ELEMENTS_FILE,
            _row(
                [
                    identifier,
                    exchange_name(item.element_type),
                    guard(item.name),
                    guard(item.documentation),
                    "",
                ]
            ),
        )
        leading = [(DESCRIPTION_KEY, item.description)] if item.description else []
        for key, value in _property_rows(leading, item.properties):
            yield PROPERTIES_FILE, _row([identifier, key, guard(value)])
    async for link in relationships:
        identifier = guard(link.identifier)
        yield (
            RELATIONS_FILE,
            _row(
                [
                    identifier,
                    f"{exchange_name(link.relationship_type)}Relationship",
                    guard(link.name),
                    "",
                    guard(link.source),
                    guard(link.target),
                    "",
                ]
            ),
        )
        stated: list[tuple[str, str]] = []
        if link.relationship_type is RelationshipType.ACCESS and link.access_type is not None:
            stated.append((ACCESS_TYPE_KEY, link.access_type.value))
        if link.directed:
            stated.append((DIRECTED_KEY, "true"))
        for key, value in _property_rows(stated, link.properties):
            yield PROPERTIES_FILE, _row([identifier, key, guard(value)])
