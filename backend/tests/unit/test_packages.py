"""The classification tree: names, paths, cycles and depth — pure rules, no database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ea.domain.errors import CyclicPackageError, PackageTooDeepError
from ea.domain.packages import (
    MAX_PACKAGE_DEPTH,
    UNSET,
    Package,
    PackageCounts,
    Unset,
    check_tree,
    descendants_of,
    path_of,
    validate_package_name,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_package(
    name: str = "Finance", parent: Package | None = None, description: str = ""
) -> Package:
    return Package.create(
        name=name, parent_id=parent.id if parent else None, now=NOW, description=description
    )


def tree(*packages: Package) -> dict[UUID, Package]:
    return {package.id: package for package in packages}


class TestNames:
    def test_a_name_is_trimmed(self) -> None:
        assert validate_package_name("  Finance ") == "Finance"

    @pytest.mark.parametrize("name", ["", "   ", "a/b", "x" * 201, "a\x00b"])
    def test_blank_slashed_or_long_names_are_refused(self, name: str) -> None:
        with pytest.raises(ValueError):
            validate_package_name(name)

    def test_two_hundred_characters_are_allowed(self) -> None:
        assert len(validate_package_name("x" * 200)) == 200


class TestThePackage:
    def test_create_trims_and_stamps(self) -> None:
        package = a_package(" Finance ", description=" Les comptes ")

        assert (package.name, package.description) == ("Finance", "Les comptes")
        assert package.created_at == package.updated_at == NOW
        assert package.parent_id is None

    def test_a_nul_is_refused_in_the_description_not_at_insert(self) -> None:
        # PostgreSQL cannot store a NUL in `text`: a driver error, in a log, otherwise.
        with pytest.raises(ValueError, match="NUL"):
            a_package(description="a\x00b")
        with pytest.raises(ValueError, match="NUL"):
            a_package().revise(now=NOW, description="a\x00b")

    def test_revise_keeps_what_is_omitted(self) -> None:
        later = NOW + timedelta(hours=1)
        revised = a_package().revise(now=later, description="neuf")

        assert (revised.name, revised.description, revised.updated_at) == ("Finance", "neuf", later)

    def test_moved_to_changes_only_the_parent(self) -> None:
        parent = a_package("Racine")
        moved = a_package().moved_to(parent.id, now=NOW + timedelta(hours=1))

        assert moved.parent_id == parent.id
        assert moved.name == "Finance"

    def test_the_unset_marker_is_not_none(self) -> None:
        assert isinstance(UNSET, Unset)
        assert UNSET is not None
        assert PackageCounts() == PackageCounts(elements=0, diagrams=0, children=0)


class TestPaths:
    def test_a_path_reads_from_the_root_down(self) -> None:
        finance = a_package("Finance")
        budget = a_package("Budget", finance)

        assert path_of(budget, tree(finance, budget)) == "Finance / Budget"

    def test_descendants_are_every_depth_below(self) -> None:
        a = a_package("A")
        b = a_package("B", a)
        c = a_package("C", b)
        d = a_package("D")

        assert descendants_of(a.id, tree(a, b, c, d)) == {b.id, c.id}
        assert descendants_of(c.id, tree(a, b, c, d)) == frozenset()


class TestTheShapeOfTheTree:
    def test_a_chain_at_the_maximum_depth_is_accepted(self) -> None:
        chain = [a_package("p0")]
        for level in range(1, MAX_PACKAGE_DEPTH):
            chain.append(a_package(f"p{level}", chain[-1]))

        check_tree(tree(*chain))

    def test_one_level_deeper_is_refused(self) -> None:
        chain = [a_package("p0")]
        for level in range(1, MAX_PACKAGE_DEPTH + 1):
            chain.append(a_package(f"p{level}", chain[-1]))

        with pytest.raises(PackageTooDeepError):
            check_tree(tree(*chain))

    def test_a_package_under_itself_is_a_cycle(self) -> None:
        a = a_package("A")

        with pytest.raises(CyclicPackageError):
            check_tree(tree(replace(a, parent_id=a.id)))

    def test_two_packages_under_each_other_are_a_cycle(self) -> None:
        a, b = a_package("A"), a_package("B")
        a = replace(a, parent_id=b.id)
        b = replace(b, parent_id=a.id)

        with pytest.raises(CyclicPackageError):
            check_tree(tree(a, b))

    def test_a_dangling_parent_is_treated_as_a_root(self) -> None:
        orphan = a_package("Orpheline", a_package("Disparu"))

        check_tree(tree(orphan))
