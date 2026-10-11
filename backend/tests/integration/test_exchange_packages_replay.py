"""Folders refused by the database, at random: an element is filed where the file said or nowhere.

A property test on a real PostgreSQL: a random tree of folders, a random quarter of them refused
(at their creation, or at the filing of their elements), and then every element is either
unfiled or under exactly the path the file gave it — never under a path made of the
parts of another. The batch rollback and the replay of the rows before a refusal are what this
exercises.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from ea.domain.errors import DuplicatePackageError, PackageNotFoundError
from ea.domain.exchange import Category
from ea.domain.ports import ElementFilter
from ea.services.packages import PackageService
from tests.exchange_files import el, exchange_xml
from tests.integration.test_exchange_roundtrip import REAL, content, whole  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


def folders(tree: list[tuple[str, list[str], Any]]) -> str:
    out = ""
    for label, refs, kids in tree:
        items = "".join(f'<item identifierRef="{r}"/>' for r in refs)
        out += f"<item><label>{label}</label>{items}{folders(kids)}</item>"
    return out


@pytest.mark.parametrize("mode", ["create", "move"])
@pytest.mark.parametrize("seed", range(6))
async def test_an_element_is_filed_where_the_file_said_or_not_at_all(
    whole: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    seed: int,
) -> None:
    architecture, _, packages, exchange, _ = whole
    rnd = random.Random(seed)
    counter = [0]
    elements: list[str] = []
    expected: dict[str, tuple[str, ...]] = {}

    def make(depth: int, path: tuple[str, ...]) -> list[tuple[str, list[str], Any]]:
        out = []
        for _ in range(rnd.randint(1, 3)):
            counter[0] += 1
            label, ident = f"F{counter[0]}", f"e{counter[0]}"
            elements.append(el(ident, "Node", f"n{counter[0]}"))
            here = (*path, label.casefold())
            expected[ident] = here
            kids = make(depth + 1, here) if depth < 3 and rnd.random() < 0.7 else []
            out.append((label, [ident], kids))
        return out

    tree = make(0, ())
    refused_names = {f"F{i}" for i in range(1, counter[0] + 1) if rnd.random() < 0.25}
    real_create, real_move = PackageService.create, PackageService.move_elements

    async def create(self, *, name, description="", parent_id=None):  # type: ignore[no-untyped-def]
        if mode == "create" and name in refused_names:
            raise DuplicatePackageError("a concurrent creation took it")
        return await real_create(self, name=name, description=description, parent_id=parent_id)

    async def move(self, element_ids, package_id):  # type: ignore[no-untyped-def]
        if (
            mode == "move"
            and package_id is not None
            and (await self.get(package_id)).package.name in refused_names
        ):
            raise PackageNotFoundError("the package vanished")
        return await real_move(self, element_ids, package_id)

    monkeypatch.setattr(PackageService, "create", create)
    monkeypatch.setattr(PackageService, "move_elements", move)
    organizations = f"<organizations>{folders(tree)}</organizations>"

    report = await exchange.import_exchange(
        content(exchange_xml("".join(elements), extra=organizations)), REAL
    )

    stored = {s.package.id: s.package for s in await packages.list_packages()}

    def path_of(package_id: Any) -> tuple[str, ...]:
        names: list[str] = []
        while package_id is not None:
            names.append(stored[package_id].name.casefold())
            package_id = stored[package_id].parent_id
        return tuple(reversed(names))

    wrong = []
    for element in await architecture.list_elements(ElementFilter(limit=1000)):
        ident = "e" + element.name[1:]
        if element.package_id is not None and path_of(element.package_id) != expected[ident]:
            wrong.append((ident, path_of(element.package_id), expected[ident]))
    assert wrong == []
    # a folder exists only under the path the file gave it
    assert {path_of(p) for p in stored} <= set(expected.values())
    # and what was refused is said once per folder that the database refused
    errors = [i for i in report.issues if i.severity.value == "error"]
    assert len(errors) <= len(refused_names)
    assert report.counts[Category.PACKAGES].created == len(stored)
