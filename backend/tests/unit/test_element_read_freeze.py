"""An element or a diagram under a published package says who froze it (docs/adr/0074)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ea.api.schemas import DiagramRead, ElementRead, FrozenByRead
from ea.domain.archimate import ElementType as E
from ea.domain.diagrams import Diagram, DiagramDetail
from ea.domain.model import Element
from ea.domain.package_versions import FrozenBy
from ea.domain.ports import GraphView
from ea.services.caller import acting_as
from tests.conftest import an_approver

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def an_element(*, package_id: UUID | None, name: str = "db-01") -> Element:
    return replace(Element.create(element_type=E.NODE, name=name, now=NOW), package_id=package_id)


def test_an_element_in_a_published_subtree_names_its_freeze_and_offers_nothing() -> None:
    finance, budget = uuid4(), uuid4()
    with acting_as(an_approver()):
        read = ElementRead.of(
            an_element(package_id=budget),
            freezes={budget: FrozenBy(finance, "Finance", "v2.1.0")},
        )
    assert read.frozen_by == FrozenByRead(package_id=finance, path="Finance", version="v2.1.0")
    assert read.allowed_transitions == []


def test_an_unfiled_element_is_never_frozen() -> None:
    elsewhere = uuid4()
    with acting_as(an_approver()):
        read = ElementRead.of(
            an_element(package_id=None),
            freezes={elsewhere: FrozenBy(elsewhere, "Finance", "v1.0.0")},
        )
    assert read.frozen_by is None and read.allowed_transitions != []


def test_a_diagram_and_the_elements_it_draws_carry_their_own_freeze() -> None:
    finance, budget = uuid4(), uuid4()
    frozen = FrozenBy(finance, "Finance", "v1.0.0")
    inside = an_element(package_id=budget, name="inside")
    outside = an_element(package_id=uuid4(), name="outside")
    detail = DiagramDetail(
        diagram=Diagram.create(name="Vue", now=NOW, package_id=budget),
        nodes=(),
        graph=GraphView(elements=(inside, outside), relationships=()),
    )
    with acting_as(an_approver()):
        read = DiagramRead.of(detail, freezes={budget: frozen})
    assert read.frozen_by == FrozenByRead.of(frozen)
    assert {e.id: e.frozen_by for e in read.elements} == {
        inside.id: FrozenByRead.of(frozen),
        outside.id: None,
    }
