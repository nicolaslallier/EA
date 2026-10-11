"""Sibling names are unique by the database's own `lower()`; the import asks the database.

Python's `casefold()` and `lower()` are not PostgreSQL's `lower()`: `ß` and `SS` are two
siblings for the database and one for `casefold`; `İ` and `i` are one for the database and two
for Python's `lower`. A key computed in Python would refuse what the database accepts and
accept what it refuses — the second as a refused row that spends the budget of 100 and rejects
the whole file. So the key is `lower()` as the database computes it.
"""

from __future__ import annotations

from typing import Any

import pytest

from ea.domain.exchange import Category
from ea.services.packages import PackageService
from tests.exchange_files import el, exchange_xml
from tests.integration.test_exchange_roundtrip import REAL, content, whole  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


def folders(*labels: str) -> str:
    return (
        "<organizations>"
        + "".join(f"<item><label>{x}</label></item>" for x in labels)
        + ("</organizations>")
    )


async def stored(packages: PackageService) -> list[str]:
    return sorted(s.package.name for s in await packages.list_packages())


@pytest.mark.parametrize(
    ("pair", "same"),
    [
        (("ß", "SS"), False),  # two siblings for the database; one for `casefold`
        (("İ", "i"), True),  # one for the database ('İ' lowers to 'i'); two for Python
        (("Σ", "ς"), False),
        (("K", "k"), True),  # the Kelvin sign
        (("ǅ", "ǆ"), True),
        (("A", "a"), True),
        (("É", "é"), True),
    ],
)
async def test_two_siblings_are_one_exactly_when_the_database_says_so(
    whole: Any,  # noqa: F811
    pair: tuple[str, str],
    same: bool,
) -> None:
    _, _, packages, exchange, _ = whole

    report = await exchange.import_exchange(
        content(exchange_xml(el("a", "Node", "A"), extra=folders(*pair))), REAL
    )

    assert await stored(packages) == sorted(pair[:1] if same else pair)
    assert report.counts[Category.PACKAGES].created == (1 if same else 2)
    codes = [i.code for i in report.issues if i.severity.value == "error"]
    # Told in memory, by the file's own plan: not a refusal of the database's constraint.
    assert codes == (["duplicate_package"] if same else [])


async def test_existing_packages_that_only_casefold_confuses_are_told_apart(
    whole: Any,  # noqa: F811
) -> None:
    _, _, packages, exchange, _ = whole
    sharp = (await packages.create(name="ß")).package.id
    caps = (await packages.create(name="SS")).package.id
    data = exchange_xml(
        el("a", "Node", "A") + el("b", "Node", "B"),
        extra="<organizations>"
        '<item><label>ß</label><item identifierRef="a"/></item>'
        '<item><label>SS</label><item identifierRef="b"/></item>'
        "</organizations>",
    )

    report = await exchange.import_exchange(content(data), REAL)

    assert report.counts[Category.PACKAGES].reused == 2
    assert report.counts[Category.PACKAGES].created == 0
    filed = {s.package.name: s.counts.elements for s in await packages.list_packages()}
    assert filed == {"ß": 1, "SS": 1}
    assert sharp != caps


async def test_a_hundred_and_one_collisions_of_non_ascii_names_do_not_reject_the_file(
    whole: Any,  # noqa: F811
) -> None:
    _, _, packages, exchange, _ = whole
    n = 101
    labels = [f"İk{i}" for i in range(n)] + [f"ik{i}" for i in range(n)]
    data = exchange_xml(el("a", "Node", "A"), extra=folders(*labels))

    report = await exchange.import_exchange(content(data), REAL)  # no TooManyRefusedRowsError

    assert report.counts[Category.PACKAGES].created == n
    assert report.counts[Category.PACKAGES].rejected == n
    assert len(await stored(packages)) == n
