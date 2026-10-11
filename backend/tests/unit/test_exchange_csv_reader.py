"""Reading the Archi-style CSV files: what is accepted, reported, and refused."""

from __future__ import annotations

import csv
import io

import pytest

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import InvalidExchangeFileError
from ea.domain.exchange import Category, ExchangeModel
from ea.formats.csv_files import parse_csv

ELEMENT_HEADER = ["ID", "Type", "Name", "Documentation", "Specialization"]
RELATION_HEADER = ["ID", "Type", "Name", "Documentation", "Source", "Target", "Specialization"]
PROPERTY_HEADER = ["ID", "Key", "Value"]


def table(*rows: list[str]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


def elements(*rows: list[str]) -> bytes:
    return table(ELEMENT_HEADER, *rows)


def codes(model: ExchangeModel) -> list[str]:
    return [issue.code for issue in model.issues]


class TestElements:
    def test_both_spellings_of_a_type_and_the_line_of_each_row(self) -> None:
        model = parse_csv(
            elements(
                ["a", "BusinessActor", "Alice", "doc", ""],
                ["b", "application_component", "CRM", "", ""],
            )
        )

        assert [(e.identifier, e.element_type, e.name, e.line) for e in model.elements] == [
            ("a", ElementType.BUSINESS_ACTOR, "Alice", 2),
            ("b", ElementType.APPLICATION_COMPONENT, "CRM", 3),
        ]
        assert model.elements[0].documentation == "doc"

    def test_the_utf8_bom_is_accepted(self) -> None:
        raw = b"\xef\xbb\xbf" + elements(["a", "Node", "Café", "", ""])

        assert parse_csv(raw).elements[0].name == "Café"

    def test_a_quoted_cell_may_hold_commas_quotes_and_every_kind_of_newline(self) -> None:
        model = parse_csv(elements(["a", "Node", "N", 'one, "two"\nthree\r\nfour\rfive', ""]))

        assert model.elements[0].documentation == 'one, "two"\nthree\r\nfour\rfive'

    def test_the_line_of_a_row_is_where_its_record_starts(self) -> None:
        raw = elements(["a", "Node", "A", "x\ny\nz", ""], ["b", "Node", "B", "", ""])

        assert [e.line for e in parse_csv(raw).elements] == [2, 5]

    def test_a_neutralised_formula_is_restored_unless_safe_cells_is_off(self) -> None:
        raw = elements(["a", "Node", "'=cmd", "", ""])

        assert parse_csv(raw).elements[0].name == "=cmd"
        assert parse_csv(raw, safe_cells=False).elements[0].name == "'=cmd"

    def test_a_name_that_is_not_neutralised_is_left_alone(self) -> None:
        assert parse_csv(elements(["a", "Node", "-Legacy", "", ""])).elements[0].name == "-Legacy"
        assert parse_csv(elements(["a", "Node", "'quoted", "", ""])).elements[0].name == "'quoted"

    def test_a_specialization_is_ignored_with_a_warning(self) -> None:
        model = parse_csv(elements(["a", "Node", "N", "", "Server"]))

        assert "specialization_ignored" in codes(model)

    def test_a_junction_is_set_aside_and_an_unknown_type_is_rejected(self) -> None:
        model = parse_csv(
            elements(["j", "AndJunction", "", "", ""], ["x", "Spaceship", "S", "", ""])
        )

        assert model.elements == ()
        assert [(u.identifier, u.ignored) for u in model.unreadable] == [("j", True), ("x", False)]

    def test_a_row_without_an_identifier_is_rejected_and_nothing_can_point_at_it(self) -> None:
        model = parse_csv(elements(["", "Node", "N", "", ""]))

        assert model.elements == () and "missing_identifier" in codes(model)
        assert [u.cascade for u in model.unreadable] == [False]

    def test_a_repeated_identifier_keeps_the_first(self) -> None:
        model = parse_csv(elements(["a", "Node", "One", "", ""], ["a", "Node", "Two", "", ""]))

        assert [e.name for e in model.elements] == ["One"]
        assert "duplicate_identifier" in codes(model)
        assert [u.cascade for u in model.unreadable] == [False]

    def test_a_short_row_is_read_with_empty_cells(self) -> None:
        model = parse_csv(table(ELEMENT_HEADER, ["a", "Node", "N"]))

        assert model.elements[0].documentation == ""

    def test_a_blank_line_is_skipped(self) -> None:
        raw = (
            elements(["a", "Node", "A", "", ""])
            + b"\r\n"
            + elements(["b", "Node", "B", "", ""])[len(table(ELEMENT_HEADER)) :]
        )

        assert [e.identifier for e in parse_csv(raw).elements] == ["a", "b"]

    def test_the_columns_may_come_in_any_order_and_in_any_case(self) -> None:
        raw = table(["NAME", "id", "extra", "Type"], ["N", "a", "ignored", "Node"])

        assert [(e.identifier, e.name) for e in parse_csv(raw).elements] == [("a", "N")]

    def test_the_error_text_never_quotes_the_type_it_does_not_know(self) -> None:
        model = parse_csv(elements(["a", "Leaky-Type", "N", "", ""]))

        assert "Leaky" not in " ".join(issue.message for issue in model.issues)

    def test_a_line_break_alone_ends_a_row_outside_quotes(self) -> None:
        raw = b'"ID","Type","Name"\r"a","Node","A"\r"b","Node","B"\r'

        assert [e.identifier for e in parse_csv(raw).elements] == ["a", "b"]


class TestRelationsAndProperties:
    def test_relations_read_both_type_spellings_and_both_ends(self) -> None:
        relations = table(
            RELATION_HEADER,
            ["r1", "CompositionRelationship", "", "", "a", "b", ""],
            ["r2", "serving", "serves", "doc", "a", "b", ""],
        )

        model = parse_csv(
            elements(["a", "Node", "A", "", ""], ["b", "Node", "B", "", ""]), relations
        )

        assert [(r.relationship_type, r.source, r.target) for r in model.relationships] == [
            (RelationshipType.COMPOSITION, "a", "b"),
            (RelationshipType.SERVING, "a", "b"),
        ]
        assert model.relationships[1].name == "serves"
        assert "documentation_dropped" in codes(model)

    def test_the_issues_of_a_relation_row_name_its_file_and_line(self) -> None:
        relations = table(RELATION_HEADER, ["r1", "Flow", "", "doc", "a", "b", ""])

        (issue,) = parse_csv(elements(["a", "Node", "A", "", ""]), relations).issues

        assert (issue.path, issue.line, issue.source_id) == ("relations.csv", 2, "r1")

    def test_properties_reach_elements_and_relations_by_id(self) -> None:
        props = table(
            PROPERTY_HEADER,
            ["a", "owner", "finance"],
            ["a", "ea.description", "Who pays"],
            ["r1", "ea.access_type", "read_write"],
            ["r1", "why", "x"],
            ["r2", "ea.directed", "true"],
            ["r3", "ea.access_type", "read"],  # r3 is not an access relationship
        )
        relations = table(
            RELATION_HEADER,
            ["r1", "Access", "", "", "a", "a", ""],
            ["r2", "Association", "", "", "a", "a", ""],
        )

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations, props)

        assert dict(model.elements[0].properties) == {"owner": "finance"}
        assert model.elements[0].description == "Who pays"
        access, association = model.relationships
        assert access.access_type is AccessType.READ_WRITE
        assert dict(access.properties) == {"why": "x"}
        assert association.directed is True
        assert "unknown_property_owner" in codes(model)

    def test_a_user_property_named_like_a_relationship_attribute_stays_a_property(self) -> None:
        props = table(PROPERTY_HEADER, ["r1", "directed", "true"], ["r1", "access_type", "read"])
        relations = table(RELATION_HEADER, ["r1", "Association", "", "", "a", "a", ""])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations, props)

        (link,) = model.relationships
        assert link.directed is False
        assert dict(link.properties) == {"directed": "true", "access_type": "read"}

    def test_an_access_without_a_stated_type_defaults_to_write(self) -> None:
        relations = table(RELATION_HEADER, ["r1", "Access", "", "", "a", "a", ""])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations)

        assert model.relationships[0].access_type is AccessType.WRITE

    def test_an_unknown_access_type_is_write_with_a_warning(self) -> None:
        relations = table(RELATION_HEADER, ["r1", "Access", "", "", "a", "a", ""])
        props = table(PROPERTY_HEADER, ["r1", "ea.access_type", "sometimes"])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations, props)

        assert model.relationships[0].access_type is AccessType.WRITE
        assert "unknown_access_type" in codes(model)

    def test_a_relation_without_an_end_is_rejected(self) -> None:
        relations = table(RELATION_HEADER, ["r1", "Flow", "", "", "a", "", ""])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations)

        assert model.relationships == ()
        assert model.unreadable[0].category is Category.RELATIONSHIPS

    def test_a_relation_with_an_unknown_type_or_a_repeated_id_is_rejected(self) -> None:
        relations = table(
            RELATION_HEADER,
            ["r1", "Telepathy", "", "", "a", "a", ""],
            ["a", "Flow", "", "", "a", "a", ""],  # the id of an element
        )

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations)

        assert model.relationships == ()
        assert sorted(codes(model)) == ["duplicate_identifier", "unknown_type"]

    def test_a_relation_pointing_nowhere_is_read_and_left_to_the_import_to_refuse(self) -> None:
        relations = table(RELATION_HEADER, ["r1", "Flow", "", "", "a", "ghost", ""])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), relations)

        assert [r.target for r in model.relationships] == ["ghost"]

    def test_an_object_with_too_many_properties_is_rejected(self) -> None:
        props = table(PROPERTY_HEADER, *[["a", f"k{i}", "v"] for i in range(101)])

        model = parse_csv(elements(["a", "Node", "A", "", ""]), None, props)

        assert model.elements == () and "too_many_properties" in codes(model)

    def test_a_formula_in_a_property_value_or_an_id_is_restored(self) -> None:
        props = table(PROPERTY_HEADER, ["'=a", "k", "'+1"])

        model = parse_csv(elements(["'=a", "Node", "A", "", ""]), None, props)

        assert model.elements[0].identifier == "=a"
        assert dict(model.elements[0].properties) == {"k": "+1"}

    def test_the_ends_of_a_relation_are_restored_like_the_ids(self) -> None:
        relations = table(RELATION_HEADER, ["r", "Flow", "", "", "'=a", "'=a", ""])

        (link,) = parse_csv(elements(["'=a", "Node", "A", "", ""]), relations).relationships

        assert (link.source, link.target) == ("=a", "=a")


class TestRefusedFiles:
    SECRET = "SECRETCONTENT"

    def refused(self, *args: bytes | None, match: str) -> None:
        with pytest.raises(InvalidExchangeFileError, match=match) as raised:
            parse_csv(*args)  # type: ignore[arg-type]
        assert self.SECRET not in str(raised.value)

    def test_an_empty_file(self) -> None:
        with pytest.raises(InvalidExchangeFileError, match="empty"):
            parse_csv(b"")
        with pytest.raises(InvalidExchangeFileError, match="empty"):
            parse_csv(b" \r\n\t")

    def test_a_file_with_nothing_but_a_bom_has_no_header(self) -> None:
        self.refused(b"\xef\xbb\xbf", match="no header")

    def test_a_nul_byte(self) -> None:
        self.refused(
            f'"ID","Type","Name"\r\n"a","Node","{self.SECRET}\x00y"\r\n'.encode(), match="NUL"
        )

    def test_a_nul_byte_in_a_companion_file(self) -> None:
        self.refused(
            elements(["a", "Node", "A", "", ""]),
            None,
            f'"ID","Key","Value"\r\n"a","k","{self.SECRET}\x00"\r\n'.encode(),
            match=r"properties\.csv holds a NUL",
        )

    def test_a_file_that_is_not_utf8(self) -> None:
        self.refused(
            f'"ID","Type","Name"\r\n"a","Node","{self.SECRET} Caf\xe9"\r\n'.encode("latin-1"),
            match="UTF-8",
        )

    def test_utf16_is_not_utf8(self) -> None:
        self.refused('"ID","Type","Name"\r\n'.encode("utf-16"), match="UTF-8")

    def test_a_header_missing_a_required_column(self) -> None:
        self.refused(table(["ID", "Name"], ["a", self.SECRET]), match="header")

    def test_a_repeated_column_is_ambiguous(self) -> None:
        self.refused(
            table(["ID", "Type", "Name", "name"], ["a", "Node", self.SECRET, "x"]),
            match="repeats",
        )

    def test_empty_header_cells_may_repeat(self) -> None:
        raw = b'"ID","Type","Name","",""\r\n"a","Node","A","",""\r\n'

        assert parse_csv(raw).elements[0].name == "A"

    def test_a_row_wider_than_its_header(self) -> None:
        self.refused(table(["ID", "Type", "Name"], ["a", "Node", "N", self.SECRET]), match="line 2")

    def test_a_trailing_empty_cell_is_tolerated(self) -> None:
        assert parse_csv(table(["ID", "Type", "Name"], ["a", "Node", "N", ""])).elements

    def test_an_unclosed_quote(self) -> None:
        self.refused(
            f'"ID","Type","Name"\r\n"a","Node","{self.SECRET}\r\n"b","Node","B"\r\n'.encode(),
            match="not valid CSV",
        )

    def test_a_quote_closed_too_early(self) -> None:
        self.refused(
            f'"ID","Type","Name"\r\n"a","Node","x"{self.SECRET}"\r\n'.encode(),
            match="line 2",
        )

    def test_a_field_over_the_record_limit_is_a_line_not_a_crash(self) -> None:
        huge = elements(["a", "Node", "N", "x" * 200_000, ""])

        with pytest.raises(InvalidExchangeFileError, match="line 2") as raised:
            parse_csv(huge)

        assert "xxxx" not in str(raised.value)

    def test_a_record_split_over_many_short_lines_is_bounded_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.formats.csv_files.MAX_CSV_RECORD_CHARS", 80)
        doc = "\n".join(["0123456789"] * 10)

        with pytest.raises(InvalidExchangeFileError, match="line 2"):
            parse_csv(elements(["a", "Node", "N", doc, ""]))

    def test_a_long_unterminated_line_is_cut_before_it_is_read_whole(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.formats.csv_files.MAX_CSV_RECORD_CHARS", 64)
        seen: list[int] = []
        original = io.TextIOWrapper.readline

        class Spy(io.TextIOWrapper):
            def readline(self, size: int | None = -1, /) -> str:
                line = original(self, size)
                seen.append(len(line))
                return line

        monkeypatch.setattr("ea.formats.csv_files.io.TextIOWrapper", Spy)

        with pytest.raises(InvalidExchangeFileError):
            parse_csv(b'"ID","Type","Name"\r\n' + b"x" * 5_000_000)

        assert max(seen) <= 65  # never more than the bound plus one character at a time

    def test_the_default_csv_field_limit_is_left_alone(self) -> None:
        before = csv.field_size_limit()
        parse_csv(elements(["a", "Node", "N", "", ""]))

        assert csv.field_size_limit() == before == 131072

    def test_too_many_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.csv_files.MAX_CSV_ROWS", 2)

        with pytest.raises(InvalidExchangeFileError, match="at most 2"):
            parse_csv(elements(*[[f"e{i}", "Node", f"n{i}", "", ""] for i in range(3)]))

    def test_the_rows_limit_counts_blank_lines_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.csv_files.MAX_CSV_ROWS", 2)

        with pytest.raises(InvalidExchangeFileError, match="at most 2"):
            parse_csv(elements(["a", "Node", "A", "", ""]) + b"\r\n" * 5)

    def test_the_real_rows_limit_is_one_hundred_thousand(self) -> None:
        header = table(ELEMENT_HEADER)

        ok = parse_csv(header + b"\r\n" * 100_000)
        assert ok.elements == ()
        with pytest.raises(InvalidExchangeFileError, match="at most 100000"):
            parse_csv(header + b"\r\n" * 100_001)

    def test_too_many_elements_and_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.csv_files.MAX_EXCHANGE_ITEMS", 3)
        relations = table(RELATION_HEADER, ["r", "Flow", "", "", "a", "b", ""])

        with pytest.raises(InvalidExchangeFileError, match="at most 3"):
            parse_csv(elements(*[[f"e{i}", "Node", f"n{i}", "", ""] for i in range(3)]), relations)

    def test_a_relations_file_has_its_own_header_check(self) -> None:
        with pytest.raises(InvalidExchangeFileError, match=r"relations\.csv"):
            parse_csv(elements(["a", "Node", "A", "", ""]), table(["ID", "Type"], ["r", "Flow"]))

    def test_the_properties_file_has_its_own_header_check(self) -> None:
        with pytest.raises(InvalidExchangeFileError, match=r"properties\.csv"):
            parse_csv(elements(["a", "Node", "A", "", ""]), None, table(["ID", "Key"], ["a", "k"]))

    def test_a_single_file_is_enough(self) -> None:
        model = parse_csv(elements(["a", "Node", "A", "", ""]))

        assert len(model.elements) == 1 and model.relationships == ()
