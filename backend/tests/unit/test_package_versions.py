"""What publishing a package means, as pure rules (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.auth import APPROVER_ROLE, EDITOR_ROLE, Caller
from ea.domain.errors import AdrRequiredError, InvalidPublicationError
from ea.domain.package_versions import (
    MAX_NOTES_LENGTH,
    Bump,
    FrozenBy,
    allowed_actions,
    digest,
    effective_freezes,
    latest_version,
    next_version,
    publication,
    reopening_reason,
)
from ea.domain.packages import Package

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def a_package(name: str, parent: Package | None = None) -> Package:
    return Package.create(
        name=name, description="", parent_id=parent.id if parent else None, now=NOW
    )


class TestTheNextVersion:
    @pytest.mark.parametrize("bump", list(Bump))
    def test_the_first_publication_is_v1_whatever_the_level(self, bump: Bump) -> None:
        assert next_version(None, bump) == "v1.0.0"

    @pytest.mark.parametrize(
        ("bump", "expected"),
        [(Bump.PATCH, "v1.2.4"), (Bump.MINOR, "v1.3.0"), (Bump.MAJOR, "v2.0.0")],
    )
    def test_each_level_moves_its_number_and_resets_the_ones_below(
        self, bump: Bump, expected: str
    ) -> None:
        assert next_version("v1.2.3", bump) == expected

    @pytest.mark.parametrize("last", ["1.2.3", "v1.2", "v01.2.3", "v1.2.3-rc1", "", "v1.2.3\n"])
    def test_a_stored_version_that_does_not_read_is_refused_not_guessed(self, last: str) -> None:
        with pytest.raises(InvalidPublicationError):
            next_version(last, Bump.PATCH)


class TestWhatIsAskedOfAPublication:
    def test_notes_are_required_whatever_the_level(self) -> None:
        for bump in (Bump.PATCH, Bump.MINOR):
            with pytest.raises(InvalidPublicationError):
                publication(bump=bump, notes="   ", adr=None)

    def test_a_major_names_its_adr(self) -> None:
        with pytest.raises(AdrRequiredError):
            publication(bump=Bump.MAJOR, notes="Retrait de STD-APP-002", adr=None)
        with pytest.raises(AdrRequiredError):
            publication(bump=Bump.MAJOR, notes="Retrait", adr="  ")
        assert publication(bump=Bump.MAJOR, notes="Retrait", adr=" ADR-0042 ").adr == "ADR-0042"

    def test_notes_are_trimmed_and_kept_whole(self) -> None:
        text = 'Ligne « un »\nLigne "deux" — é' + "x" * 100
        assert publication(bump=Bump.MINOR, notes=f"  {text}  ", adr=None).notes == text

    def test_the_longest_notes_pass_and_one_more_does_not(self) -> None:
        publication(bump=Bump.MINOR, notes="n" * MAX_NOTES_LENGTH, adr=None)
        with pytest.raises(InvalidPublicationError):
            publication(bump=Bump.MINOR, notes="n" * (MAX_NOTES_LENGTH + 1), adr=None)

    def test_a_refusal_never_echoes_what_was_refused(self) -> None:
        with pytest.raises(InvalidPublicationError) as raised:
            publication(bump=Bump.MINOR, notes="secret-zzz\x00", adr=None)
        assert "secret-zzz" not in str(raised.value)

    def test_a_reason_is_required_and_bounded(self) -> None:
        assert reopening_reason("  correction d'une coquille ") == "correction d'une coquille"
        with pytest.raises(InvalidPublicationError):
            reopening_reason(" ")
        with pytest.raises(InvalidPublicationError):
            reopening_reason("x" * 501)

    def test_the_digest_is_a_stable_sha256(self) -> None:
        assert digest("abc") == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


class TestWhatIsFrozenUnder:
    def test_a_published_package_freezes_itself_and_everything_below_it(self) -> None:
        root = a_package("Finance")
        child = a_package("Budget", root)
        leaf = a_package("2027", child)
        other = a_package("RH")
        tree = {p.id: p for p in (root, child, leaf, other)}

        frozen = effective_freezes(tree, {root.id: "v1.0.0"})

        assert set(frozen) == {root.id, child.id, leaf.id}
        assert frozen[leaf.id] == FrozenBy(root.id, "Finance", "v1.0.0")

    def test_the_nearest_published_ancestor_is_named(self) -> None:
        root = a_package("Finance")
        child = a_package("Budget", root)
        leaf = a_package("2027", child)
        tree = {p.id: p for p in (root, child, leaf)}

        frozen = effective_freezes(tree, {root.id: "v1.0.0", child.id: "v2.1.0"})

        assert frozen[leaf.id].package_id == child.id
        assert frozen[root.id].package_id == root.id

    def test_nothing_published_nothing_frozen(self) -> None:
        root = a_package("Finance")
        assert effective_freezes({root.id: root}, {}) == {}


class TestWhatACallerMayDo:
    approver = Caller("a", "a", frozenset({APPROVER_ROLE}))
    editor = Caller("e", "e", frozenset({EDITOR_ROLE}))
    here = FrozenBy(uuid4(), "Finance", "v1.0.0")

    def test_an_approver_publishes_an_open_package(self) -> None:
        assert allowed_actions(self.approver, frozen_by=None, own_version=None) == ("publish",)

    def test_an_approver_reopens_a_package_published_on_its_own(self) -> None:
        assert allowed_actions(self.approver, frozen_by=self.here, own_version="v1.0.0") == (
            "reopen",
        )

    def test_nothing_is_offered_under_an_ancestor_that_is_published(self) -> None:
        assert allowed_actions(self.approver, frozen_by=self.here, own_version=None) == ()

    def test_an_editor_without_the_approver_role_is_offered_nothing(self) -> None:
        assert allowed_actions(self.editor, frozen_by=None, own_version=None) == ()


class TestTheLatestVersion:
    def test_it_compares_numbers_not_strings(self) -> None:
        assert latest_version(["v1.9.0", "v1.10.0", "v1.2.0"]) == "v1.10.0"
        assert latest_version(["v2.0.0", "v10.0.0", "v9.9.9"]) == "v10.0.0"

    def test_no_version_is_none(self) -> None:
        assert latest_version([]) is None

    def test_an_unreadable_version_is_refused(self) -> None:
        with pytest.raises(InvalidPublicationError):
            latest_version(["v1.0.0", "nonsense"])


class TestAPartPastTheCeiling:
    @pytest.mark.parametrize(
        ("last", "bump"),
        [("v9999.0.0", Bump.MAJOR), ("v1.9999.0", Bump.MINOR), ("v1.0.9999", Bump.PATCH)],
    )
    def test_a_number_the_column_would_refuse_is_refused_here_first(
        self, last: str, bump: Bump
    ) -> None:
        with pytest.raises(InvalidPublicationError):
            next_version(last, bump)

    def test_the_last_number_below_the_ceiling_still_goes_up(self) -> None:
        assert next_version("v1.0.9998", Bump.PATCH) == "v1.0.9999"
