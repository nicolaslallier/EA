"""A constraint the server names becomes the refusal a caller can act on — never a guess."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from ea.db.models.package import PackageRecord
from ea.domain.diagrams import Diagram
from ea.domain.errors import (
    DuplicateDiagramError,
    DuplicatePackageError,
    PackageNotEmptyError,
    PackageNotFoundError,
)
from ea.repositories.diagram_store import FOREIGN_KEY_TO_PACKAGE, _refused
from ea.repositories.package_store import NAME_TAKEN, REFERENCES, _refusal, _to_package

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def refused_by(constraint: str) -> IntegrityError:
    driver = Exception("refused")
    driver.constraint_name = constraint  # type: ignore[attr-defined]
    adapter = Exception("refused")
    adapter.__cause__ = driver
    return IntegrityError("INSERT", {}, adapter)


@pytest.mark.parametrize("constraint", NAME_TAKEN)
def test_a_sibling_name_index_is_a_duplicate(constraint: str) -> None:
    refusal = _refusal(refused_by(constraint), "Finance")

    assert isinstance(refusal, DuplicatePackageError)
    assert "'Finance'" in str(refusal)


@pytest.mark.parametrize("constraint", REFERENCES)
def test_a_key_to_a_package_is_a_missing_package_when_writing(constraint: str) -> None:
    assert isinstance(_refusal(refused_by(constraint), "x"), PackageNotFoundError)


@pytest.mark.parametrize("constraint", REFERENCES)
def test_a_key_to_a_package_is_a_non_empty_package_when_deleting(constraint: str) -> None:
    assert isinstance(_refusal(refused_by(constraint), "x", deleting=True), PackageNotEmptyError)


def test_an_unknown_constraint_is_left_alone() -> None:
    assert _refusal(refused_by("some_other_constraint"), "x") is None


def test_a_diagram_names_a_missing_package_apart_from_a_taken_name() -> None:
    diagram = Diagram.create(name="Vente", now=NOW)

    assert isinstance(_refused(refused_by(FOREIGN_KEY_TO_PACKAGE), diagram), PackageNotFoundError)
    assert isinstance(_refused(refused_by("uq_diagrams_name"), diagram), DuplicateDiagramError)


def test_a_stored_row_reads_back_as_the_same_package() -> None:
    row = PackageRecord(
        id=None, parent_id=None, name="Finance", description="d", created_at=NOW, updated_at=NOW
    )
    package = _to_package(row)

    assert (package.name, package.description, package.parent_id) == ("Finance", "d", None)
