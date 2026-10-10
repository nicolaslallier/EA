"""A saved query: a named filter, shared, with whoever wrote it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ea.domain import query as query_module
from ea.domain.archimate import ElementType as E
from ea.domain.errors import InvalidQueryError
from ea.domain.query import ElementQuery
from ea.domain.saved_queries import SavedQuery

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_saved_query(**overrides: object) -> SavedQuery:
    fields: dict[str, object] = {
        "name": "Les noeuds",
        "query": ElementQuery(element_types=(E.NODE,)),
        "created_by": "editor",
        "now": NOW,
    }
    return SavedQuery.create(**{**fields, **overrides})  # type: ignore[arg-type]


class TestCreating:
    def test_the_name_and_description_are_trimmed_and_the_author_recorded(self) -> None:
        saved = a_saved_query(name="  Les noeuds ", description=" Tous ")

        assert (saved.name, saved.description, saved.created_by) == ("Les noeuds", "Tous", "editor")
        assert saved.created_at == saved.updated_at == NOW

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_a_blank_or_long_name_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="name"):
            a_saved_query(name=name)

    def test_a_long_description_is_refused(self) -> None:
        with pytest.raises(ValueError, match="description"):
            a_saved_query(description="x" * 2001)

    @pytest.mark.parametrize("field", ["name", "description"])
    def test_a_nul_character_is_refused_not_left_for_postgresql(self, field: str) -> None:
        with pytest.raises(ValueError, match="NUL"):
            a_saved_query(**{field: "a\x00b"})

    def test_a_nul_character_is_refused_on_a_revision_too(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            a_saved_query().revise(now=NOW, name="a\x00b")

    def test_a_query_too_large_to_keep_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(query_module, "MAX_STORED_QUERY_BYTES", 50)

        with pytest.raises(InvalidQueryError):
            a_saved_query()


class TestRevising:
    def test_an_omitted_field_is_kept(self) -> None:
        saved = a_saved_query()
        later = NOW + timedelta(hours=1)

        revised = saved.revise(now=later, description="neuf")

        assert (revised.name, revised.query, revised.created_by) == (
            saved.name,
            saved.query,
            saved.created_by,
        )
        assert (revised.description, revised.updated_at, revised.created_at) == ("neuf", later, NOW)

    def test_the_query_can_be_replaced(self) -> None:
        replacement = ElementQuery(text="db")

        assert a_saved_query().revise(now=NOW, query=replacement).query == replacement

    def test_the_author_is_never_rewritten(self) -> None:
        assert a_saved_query().revise(now=NOW, name="Autre").created_by == "editor"

    def test_the_summary_leaves_the_query_out(self) -> None:
        saved = a_saved_query()

        assert not hasattr(saved.summary, "query")
        assert saved.summary.id == saved.id
