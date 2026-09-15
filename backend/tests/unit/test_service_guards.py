"""Every use case asks who is calling before it does anything.

Checked on the source rather than by calling thirty methods with invented
arguments: the first statement of each public coroutine must be the check, and
a new method without one fails here. Two behavioural tests prove the check does
what it says.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import NotAuthenticatedError, NotAuthorisedError
from ea.domain.ports import ElementFilter
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.files import FileService
from ea.services.ipam import IpamService
from tests.conftest import a_reader

WRITES = {
    ArchitectureService: {
        "create_element",
        "update_element",
        "delete_element",
        "connect",
        "disconnect",
    },
    DiagramService: {"create", "update", "delete", "replace_layout"},
    DocumentService: {"attach", "attach_text", "revise", "revise_text", "discard", "reindex_all"},
    FileService: {"upload", "delete"},
    IpamService: {"declare_network", "assign_address", "allocate_next", "release_address"},
}


def first_call(method: object) -> str | None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))  # type: ignore[arg-type]
    body = tree.body[0].body  # type: ignore[attr-defined]
    statements = [
        s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    if not statements:
        return None
    first = statements[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
        func = first.value.func
        return func.id if isinstance(func, ast.Name) else None
    return None


def public_coroutines(service: type) -> list[str]:
    return [
        name
        for name, member in vars(service).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    ]


@pytest.mark.parametrize("service", list(WRITES), ids=lambda s: s.__name__)
def test_every_public_use_case_starts_by_checking_its_caller(service: type) -> None:
    for name in public_coroutines(service):
        expected = "require_editor" if name in WRITES[service] else "require_caller"
        assert first_call(getattr(service, name)) == expected, f"{service.__name__}.{name}"


@pytest.mark.parametrize("service", list(WRITES), ids=lambda s: s.__name__)
def test_the_write_list_names_only_methods_that_exist(service: type) -> None:
    assert WRITES[service] <= set(public_coroutines(service))


@pytest.mark.asyncio
async def test_nobody_reads_nothing(service: ArchitectureService, nobody_calling: None) -> None:
    with pytest.raises(NotAuthenticatedError):
        await service.list_elements(ElementFilter())


@pytest.mark.asyncio
async def test_a_reader_cannot_create(service: ArchitectureService) -> None:
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")


@pytest.mark.asyncio
async def test_a_reader_cannot_create_a_diagram(diagram_service: DiagramService) -> None:
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await diagram_service.create(name="Vente")


@pytest.mark.asyncio
async def test_a_reader_opens_the_diagram_list(diagram_service: DiagramService) -> None:
    with acting_as(a_reader()):
        assert await diagram_service.list_diagrams() == ()
