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
from dataclasses import replace
from typing import Final

from ea.domain.archimate import AccessType, RelationshipType
from ea.domain.errors import InvalidExchangeFileError
from ea.domain.exchange import (
    DESCRIPTION_KEY,
    ELEMENTS_FILE,
    JUNCTION_NAMES,
    MAX_CSV_RECORD_CHARS,
    MAX_CSV_ROWS,
    MAX_EXCHANGE_ITEMS,
    MAX_PROPERTIES_PER_OBJECT,
    PROPERTIES_FILE,
    RELATIONS_FILE,
    Category,
    ExchangeElement,
    ExchangeModel,
    ExchangeRelationship,
    Issue,
    Severity,
    Unreadable,
    exchange_name,
    fold_properties,
    lookup_element_type,
    lookup_relationship_type,
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
        if link.relationship_type is RelationshipType.ACCESS:
            # As the XML does: no stated type is the unspecified `access`, not the default `write`.
            stated.append((ACCESS_TYPE_KEY, (link.access_type or AccessType.ACCESS).value))
        if link.directed:
            stated.append((DIRECTED_KEY, "true"))
        for key, value in _property_rows(stated, link.properties):
            yield PROPERTIES_FILE, _row([identifier, key, guard(value)])


# --- Reading --------------------------------------------------------------------


def _refuse(message: str) -> InvalidExchangeFileError:
    return InvalidExchangeFileError(message)


class _Lines:
    """The text `csv.reader` is fed: line by line, decoded as it goes, bounded as it goes.

    The reader asks for a line at a time, so a file is never decoded or split whole. A NUL,
    bytes that are not UTF-8, and a record (the lines the reader needs to complete one row,
    quoted line breaks included) longer than `MAX_CSV_RECORD_CHARS` stop it at the line that
    crosses the bound: `readline` is given the bound plus one character, so a 20 MB line
    without a break is never read to its end. Line breaks are handed over untouched
    (`newline=""`), so a quoted cell gives back the `\r\n` or the `\r` it was written with.
    """

    def __init__(self, data: bytes, name: str) -> None:
        self._name = name
        self._text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
        #: Characters handed over since the last row was completed.
        self.record = 0
        #: The line the record being read started on.
        self.start = 1

    def __iter__(self) -> _Lines:
        return self

    def __next__(self) -> str:
        try:
            line = self._text.readline(MAX_CSV_RECORD_CHARS + 1)
        except UnicodeDecodeError:
            raise _refuse(f"{self._name} is not UTF-8") from None
        if not line:
            raise StopIteration
        if "\x00" in line:
            raise _refuse(f"{self._name} holds a NUL byte (line {self.start})")
        self.record += len(line)
        if self.record > MAX_CSV_RECORD_CHARS:
            raise _refuse(
                f"{self._name}: the record at line {self.start} is longer than "
                f"{MAX_CSV_RECORD_CHARS} characters"
            )
        return line


def _table(
    data: bytes, name: str, required: tuple[str, ...]
) -> Iterator[tuple[int, dict[str, str]]]:
    """The rows of one file, one at a time, keyed by lower-cased header.

    Yields `(line the row's record starts on, cells)`. A row shorter than the header has
    empty cells; blank lines are skipped. A generator: a bound crossed stops the file where
    it is crossed, and what has been handed over is not kept here.
    """
    if not data or data.isspace():
        raise _refuse(f"{name} is empty")
    lines = _Lines(data, name)
    reader = csv.reader(lines, strict=True)
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration:
        raise _refuse(f"{name} has no header") from None
    except csv.Error:
        raise _refuse(f"{name} is not valid CSV (line 1)") from None
    named = [column for column in header if column]
    if len(set(named)) != len(named):
        raise _refuse(f"the header of {name} repeats a column")
    if any(column not in header for column in required):
        raise _refuse(f"the header of {name} must name: {', '.join(required)}")
    records = 0
    while True:
        lines.record = 0
        lines.start = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            return
        except csv.Error:
            raise _refuse(f"{name} is not valid CSV (line {lines.start})") from None
        records += 1
        if records > MAX_CSV_ROWS:
            raise _refuse(f"{name} holds at most {MAX_CSV_ROWS} rows")
        if not any(cell.strip() for cell in cells):
            continue
        if any(cell.strip() for cell in cells[len(header) :]):
            raise _refuse(f"{name}: line {lines.start} has more cells than the header")
        padded = [*cells, *[""] * max(0, len(header) - len(cells))]
        yield lines.start, dict(zip(header, padded, strict=False))


def _access_named(raw: str | None) -> AccessType | None:
    try:
        return AccessType(raw) if raw else None
    except ValueError:
        return None


class _Reading:
    """What `parse_csv` has read so far: the rows, what was set aside, and what was said."""

    def __init__(self, unguard: Callable[[str], str]) -> None:
        self.unguard = unguard
        self.seen: set[str] = set()
        self.items = 0
        self.unreadable: list[Unreadable] = []
        self.issues: list[Issue] = []
        #: The property rows of each id, never more than one over the limit per object.
        self.owners: dict[str, list[tuple[str, str]]] = {}

    def identifier(self, cell: str) -> str:
        return self.unguard(cell).strip()

    def count_item(self) -> None:
        self.items += 1
        if self.items > MAX_EXCHANGE_ITEMS:
            raise _refuse(f"an exchange file holds at most {MAX_EXCHANGE_ITEMS} elements and links")

    def say(self, issue: Issue, file: str, line: int, identifier: str) -> None:
        self.issues.append(replace(issue, source_id=identifier or None, line=line, path=file))

    def reject(
        self,
        category: Category,
        file: str,
        line: int,
        identifier: str,
        issue: Issue,
        *,
        ignored: bool = False,
        cascade: bool = True,
    ) -> None:
        """Set a row aside. Without an identifier nothing can point at it: no cascade."""
        self.say(issue, file, line, identifier)
        self.unreadable.append(
            Unreadable(
                category,
                identifier,
                ignored=ignored,
                cascade=cascade and bool(identifier),
            )
        )

    def too_many(self, pairs: list[tuple[str, str]]) -> bool:
        return len(pairs) > MAX_PROPERTIES_PER_OBJECT


def _read_properties(data: bytes, reading: _Reading) -> None:
    for _, row in _table(data, PROPERTIES_FILE, ("id", "key", "value")):
        pairs = reading.owners.setdefault(reading.identifier(row["id"]), [])
        if len(pairs) <= MAX_PROPERTIES_PER_OBJECT:  # one more than allowed says "too many"
            pairs.append((row["key"], reading.unguard(row["value"])))


def _read_elements(data: bytes, reading: _Reading) -> list[ExchangeElement]:
    found: list[ExchangeElement] = []
    for line, row in _table(data, ELEMENTS_FILE, ("id", "type", "name")):
        reading.count_item()
        identifier = reading.identifier(row["id"])
        kind = row["type"].strip()

        def refuse(
            code: str,
            message: str,
            *,
            line: int = line,
            identifier: str = identifier,
            severity: Severity = Severity.ERROR,
            **how: bool,
        ) -> None:
            reading.reject(
                Category.ELEMENTS,
                ELEMENTS_FILE,
                line,
                identifier,
                Issue(severity, code, message),
                **how,
            )

        if not identifier:
            refuse("missing_identifier", "a row has no ID")
            continue
        if identifier in reading.seen:
            refuse(
                "duplicate_identifier", "this ID is already used; the row is ignored", cascade=False
            )
            continue
        reading.seen.add(identifier)
        if kind in JUNCTION_NAMES or kind.lower() == "junction":
            refuse(
                "junction_not_supported",
                "junctions cannot be stored; ignored",
                severity=Severity.INFO,
                ignored=True,
            )
            continue
        element_type = lookup_element_type(kind)
        if element_type is None:
            refuse("unknown_type", "this is not an ArchiMate element type")
            continue
        pairs = reading.owners.get(identifier, [])
        if reading.too_many(pairs):
            refuse(
                "too_many_properties",
                f"an object has at most {MAX_PROPERTIES_PER_OBJECT} properties",
            )
            continue
        if row.get("specialization", "").strip():
            reading.say(
                Issue(
                    Severity.WARNING,
                    "specialization_ignored",
                    "the Specialization column is not stored",
                ),
                ELEMENTS_FILE,
                line,
                identifier,
            )
        folded = fold_properties(pairs)
        for issue in folded.issues:
            reading.say(issue, ELEMENTS_FILE, line, identifier)
        found.append(
            ExchangeElement(
                identifier=identifier,
                element_type=element_type,
                name=reading.unguard(row["name"]),
                description=folded.description,
                documentation=reading.unguard(row.get("documentation", "")),
                properties=folded.properties,
                line=line,
                path=ELEMENTS_FILE,
            )
        )
    return found


def _read_relations(data: bytes, reading: _Reading) -> list[ExchangeRelationship]:
    found: list[ExchangeRelationship] = []
    for line, row in _table(data, RELATIONS_FILE, ("id", "type", "source", "target")):
        reading.count_item()
        identifier = reading.identifier(row["id"])

        def refuse(
            code: str, message: str, *, line: int = line, identifier: str = identifier, **how: bool
        ) -> None:
            reading.reject(
                Category.RELATIONSHIPS,
                RELATIONS_FILE,
                line,
                identifier,
                Issue(Severity.ERROR, code, message),
                **how,
            )

        if not identifier or identifier in reading.seen:
            refuse(
                "missing_identifier" if not identifier else "duplicate_identifier",
                "this relation has no usable ID; the row is ignored",
                cascade=False,
            )
            continue
        reading.seen.add(identifier)
        relationship_type = lookup_relationship_type(row["type"])
        source = reading.identifier(row["source"])
        target = reading.identifier(row["target"])
        if relationship_type is None:
            refuse("unknown_type", "this is not an ArchiMate relationship type", cascade=False)
            continue
        if not source or not target:
            refuse("missing_end", "a relation needs a Source and a Target", cascade=False)
            continue
        pairs = reading.owners.get(identifier, [])
        stated = dict(pairs)
        rest = [(k, v) for k, v in pairs if k not in (ACCESS_TYPE_KEY, DIRECTED_KEY)]
        if reading.too_many(rest):
            refuse(
                "too_many_properties",
                f"an object has at most {MAX_PROPERTIES_PER_OBJECT} properties",
                cascade=False,
            )
            continue
        access_type = None
        if relationship_type is RelationshipType.ACCESS:
            raw = stated.get(ACCESS_TYPE_KEY)
            access_type = _access_named(raw) or AccessType.WRITE  # the schema's default
            if raw and _access_named(raw) is None:
                reading.say(
                    Issue(
                        Severity.WARNING,
                        "unknown_access_type",
                        "unknown access type; 'write' is used",
                    ),
                    RELATIONS_FILE,
                    line,
                    identifier,
                )
        if row.get("documentation", "").strip():
            reading.say(
                Issue(
                    Severity.INFO,
                    "documentation_dropped",
                    "a relationship has no documentation field; it is not stored",
                ),
                RELATIONS_FILE,
                line,
                identifier,
            )
        folded = fold_properties(rest)
        for issue in folded.issues:
            reading.say(issue, RELATIONS_FILE, line, identifier)
        found.append(
            ExchangeRelationship(
                identifier=identifier,
                relationship_type=relationship_type,
                source=source,
                target=target,
                name=reading.unguard(row.get("name", "")),
                access_type=access_type,
                directed=relationship_type is RelationshipType.ASSOCIATION
                and stated.get(DIRECTED_KEY, "").lower() in ("true", "1"),
                properties=folded.properties,
                line=line,
                path=RELATIONS_FILE,
            )
        )
    return found


def parse_csv(
    main: bytes,
    relations: bytes | None = None,
    properties: bytes | None = None,
    *,
    safe_cells: bool = True,
) -> ExchangeModel:
    """Read `elements.csv` (+ `relations.csv`, `properties.csv`) into an `ExchangeModel`.

    Raise `InvalidExchangeFileError` for a file that cannot be read at all (empty, not
    UTF-8, a NUL, a malformed table, a bound crossed); a row that is wrong is reported
    and set aside. `safe_cells=False` keeps the `'` of a neutralised cell.
    """
    reading = _Reading(unguard_cell if safe_cells else _identity)
    if properties is not None:
        _read_properties(properties, reading)
    elements = _read_elements(main, reading)
    links = _read_relations(relations, reading) if relations is not None else []
    for owner in sorted(set(reading.owners) - reading.seen):
        reading.issues.append(
            Issue(
                Severity.WARNING,
                "unknown_property_owner",
                "properties.csv names an ID that elements.csv and relations.csv do not",
                owner,
                path=PROPERTIES_FILE,
            )
        )
    return ExchangeModel(
        tuple(elements), tuple(links), tuple(reading.unreadable), tuple(reading.issues)
    )
