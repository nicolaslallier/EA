"""Writing the three Archi-style CSV files."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable

import pytest

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.exchange import ExchangeElement, ExchangeRelationship
from ea.formats.csv_files import guard_cell, render_csv, unguard_cell


async def aiter_of[T](items: Iterable[T]) -> AsyncIterator[T]:
    for item in items:
        yield item


async def written(
    elements: Iterable[ExchangeElement] = (),
    relationships: Iterable[ExchangeRelationship] = (),
    *,
    safe_cells: bool = True,
) -> dict[str, list[list[str]]]:
    files: dict[str, str] = defaultdict(str)
    async for name, text in render_csv(
        aiter_of(elements), aiter_of(relationships), safe_cells=safe_cells
    ):
        files[name] += text
    return {name: list(csv.reader(io.StringIO(text, newline=""))) for name, text in files.items()}


def actor(**fields: object) -> ExchangeElement:
    return ExchangeElement("a-1", ElementType.BUSINESS_ACTOR, "Alice", **fields)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_headers_follow_the_archi_layout() -> None:
    files = await written()

    assert files["elements.csv"] == [["ID", "Type", "Name", "Documentation", "Specialization"]]
    assert files["relations.csv"] == [
        ["ID", "Type", "Name", "Documentation", "Source", "Target", "Specialization"]
    ]
    assert files["properties.csv"] == [["ID", "Key", "Value"]]


@pytest.mark.asyncio
async def test_an_element_row_and_its_property_rows() -> None:
    files = await written(
        [actor(description="Who pays", documentation="Doc", properties={"z": "1", "a": "2"})]
    )

    assert files["elements.csv"][1] == ["a-1", "BusinessActor", "Alice", "Doc", ""]
    assert files["properties.csv"][1:] == [
        ["a-1", "ea.description", "Who pays"],
        ["a-1", "a", "2"],
        ["a-1", "z", "1"],
    ]


@pytest.mark.asyncio
async def test_a_relationship_row_carries_its_type_with_the_relationship_suffix() -> None:
    link = ExchangeRelationship(
        "r-1",
        RelationshipType.ACCESS,
        "a-1",
        "b-1",
        name="reads",
        access_type=AccessType.READ_WRITE,
        properties={"why": "x"},
    )
    directed = ExchangeRelationship(
        "r-2", RelationshipType.ASSOCIATION, "a-1", "b-1", directed=True
    )

    files = await written([actor()], [link, directed])

    assert files["relations.csv"][1] == ["r-1", "AccessRelationship", "reads", "", "a-1", "b-1", ""]
    # `ea.*` names, which a user property can never take: a property called `directed`
    # must not be read back as the arrow of an association.
    assert files["properties.csv"][1:] == [
        ["r-1", "ea.access_type", "read_write"],
        ["r-1", "why", "x"],
        ["r-2", "ea.directed", "true"],
    ]


@pytest.mark.asyncio
async def test_cells_that_would_run_as_a_formula_are_neutralised() -> None:
    files = await written([ExchangeElement("=1+1", ElementType.NODE, "=cmd|' /C calc'!A0")])

    assert files["elements.csv"][1][0] == "'=1+1"
    assert files["elements.csv"][1][2] == "'=cmd|' /C calc'!A0"


@pytest.mark.asyncio
async def test_every_text_cell_is_guarded_in_every_file() -> None:
    link = ExchangeRelationship(
        "=r", RelationshipType.FLOW, "=a", "+b", name="@n", properties={"k": "-v"}
    )
    element = ExchangeElement("=a", ElementType.NODE, "-n", documentation="+d", description="@x")

    files = await written([element], [link])

    assert files["elements.csv"][1] == ["'=a", "Node", "'-n", "'+d", ""]
    assert files["relations.csv"][1] == ["'=r", "FlowRelationship", "'@n", "", "'=a", "'+b", ""]
    assert files["properties.csv"][1:] == [
        ["'=a", "ea.description", "'@x"],
        ["'=r", "k", "'-v"],
    ]


@pytest.mark.asyncio
async def test_safe_cells_can_be_turned_off() -> None:
    files = await written([ExchangeElement("a", ElementType.NODE, "=raw")], safe_cells=False)

    assert files["elements.csv"][1][2] == "=raw"


@pytest.mark.asyncio
async def test_commas_quotes_and_newlines_survive_quoting() -> None:
    files = await written([actor(documentation='one, "two"\nthree\r\nfour')])

    assert files["elements.csv"][1][3] == 'one, "two"\nthree\r\nfour'


@pytest.mark.parametrize(
    "value",
    ["=1+1", "+x", "-5", "@a", "'=a", "''-b", "plain", "", "'", "a=b", "-", "-Legacy", "'-Legacy"],
)
def test_unguarding_undoes_guarding_exactly(value: str) -> None:
    assert unguard_cell(guard_cell(value)) == value


def test_only_dangerous_cells_are_changed() -> None:
    assert guard_cell("plain") == "plain"
    assert guard_cell("=x") == "'=x"
    assert guard_cell("'=x") == "''=x"
    assert guard_cell("-Legacy") == "'-Legacy"  # a real name: shown quoted, given back whole


#: What a spreadsheet may read as a formula even though the sign is not the first character.
HIDDEN_FORMULAS = [
    "\t=1",
    "\r=1",
    "\t",
    " =1",
    "  \t+1",
    "\N{COMBINING GRAPHEME JOINER}=1",  # a mark (Mn), invisible
    "\N{COMBINING ACUTE ACCENT}@x",
    "\N{ZERO WIDTH SPACE}=1",  # zero-width space
    "\N{ZERO WIDTH NO-BREAK SPACE}=1",  # byte order mark
    "\N{RIGHT-TO-LEFT OVERRIDE}=1",  # right-to-left override
    "\x01@SUM(A1)",
    "\N{NO-BREAK SPACE}-2+3",  # no-break space
    '=HYPERLINK("http://x")',
    "+cmd|' /C calc'!A0",
    "@SUM(A1)",
    "-2+3",
]


@pytest.mark.parametrize("value", HIDDEN_FORMULAS)
def test_a_formula_behind_blanks_or_invisible_characters_is_neutralised(value: str) -> None:
    guarded = guard_cell(value)

    assert guarded == "'" + value
    assert unguard_cell(guarded) == value


@pytest.mark.parametrize(
    "value", ["plain", "é=1", "a =1", "1+1", "'x", "\N{ZERO WIDTH SPACE}x", " x", "x\t"]
)
def test_a_cell_without_a_leading_trigger_is_left_alone(value: str) -> None:
    assert guard_cell(value) == value
    assert unguard_cell(value) == value


@pytest.mark.parametrize("value", [*HIDDEN_FORMULAS, "plain", "'=1", "'\t=1", "''", "'  -x"])
def test_the_guard_is_its_own_inverse_for_every_shape(value: str) -> None:
    assert unguard_cell(guard_cell(value)) == value


@pytest.mark.asyncio
async def test_a_user_property_called_like_a_synthetic_one_is_a_property() -> None:
    link = ExchangeRelationship(
        "r",
        RelationshipType.ASSOCIATION,
        "a",
        "b",
        properties={"directed": "true", "access_type": "x"},
    )

    files = await written([actor()], [link])

    assert files["properties.csv"][1:] == [
        ["r", "access_type", "x"],
        ["r", "directed", "true"],
    ]


@pytest.mark.asyncio
async def test_an_access_without_a_stated_type_is_written_as_the_unspecified_access() -> None:
    link = ExchangeRelationship("r", RelationshipType.ACCESS, "a", "b")

    files = await written([actor()], [link])

    assert files["properties.csv"][1:] == [["r", "ea.access_type", "access"]]


@pytest.mark.asyncio
async def test_a_junction_is_written_with_its_exchange_type() -> None:
    junction = ExchangeElement(
        "j-1", ElementType.JUNCTION, "Gate", properties={"junction_type": "or"}
    )

    files = await written([junction])

    assert files["elements.csv"][1] == ["j-1", "OrJunction", "Gate", "", ""]
