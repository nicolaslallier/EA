"""Reading an exchange file: what is kept, what is reported, and what is refused outright."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import InvalidExchangeFileError
from ea.domain.exchange import Category, ExchangeModel, Severity
from ea.formats.archimate_xml import parse_model
from tests.exchange_files import DATA, el, exchange_xml, rel


def codes(model: ExchangeModel) -> list[str]:
    return [issue.code for issue in model.issues]


class TestAWellFormedFile:
    def test_the_minimal_model_is_read(self) -> None:
        model = parse_model((DATA / "minimal-model.xml").read_bytes())

        assert [e.name for e in model.elements] == ["Customer", "Shop", "Ordering"]
        shop = model.elements[1]
        assert shop.element_type is ElementType.APPLICATION_COMPONENT
        assert dict(shop.properties) == {"owner": "retail"}
        assert model.elements[0].documentation == "Buys things"
        assert [r.relationship_type for r in model.relationships] == [
            RelationshipType.ASSOCIATION,
            RelationshipType.SERVING,
            RelationshipType.FLOW,
        ]
        assert model.relationships[1].name == "serves"

    def test_a_junction_is_set_aside_not_read(self) -> None:
        model = parse_model((DATA / "minimal-model.xml").read_bytes())

        (junction,) = model.unreadable
        assert (junction.category, junction.identifier, junction.ignored) == (
            Category.ELEMENTS,
            "id-and",
            True,
        )
        assert "junction_not_supported" in codes(model)

    def test_a_path_locates_each_element(self) -> None:
        model = parse_model((DATA / "minimal-model.xml").read_bytes())

        assert model.elements[1].path == "elements/element[2]"

    def test_the_declared_encoding_is_honoured(self) -> None:
        text = '<?xml version="1.0" encoding="ISO-8859-1"?>'
        raw = (
            exchange_xml(el("a", "Node", "Café"))
            .decode()
            .replace('<?xml version="1.0" encoding="UTF-8"?>', text)
        )

        model = parse_model(raw.encode("latin-1"))

        assert model.elements[0].name == "Café"

    def test_several_languages_keep_the_first_and_warn(self) -> None:
        xml = exchange_xml(
            '<element identifier="a" xsi:type="Node"><name xml:lang="en">One</name>'
            '<name xml:lang="fr">Un</name></element>'
        )

        model = parse_model(xml)

        assert model.elements[0].name == "One"
        assert "multiple_languages" in codes(model)


class TestRefusedOutright:
    @pytest.mark.parametrize("raw", [b"", b"   \n  "])
    def test_an_empty_file(self, raw: bytes) -> None:
        with pytest.raises(InvalidExchangeFileError, match="empty"):
            parse_model(raw)

    def test_malformed_xml_names_a_line_and_never_the_content(self) -> None:
        with pytest.raises(InvalidExchangeFileError) as raised:
            parse_model(
                b'<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/">\n<oops-secret></model>'
            )

        assert "line 2" in str(raised.value)
        assert "oops-secret" not in str(raised.value)

    def test_the_wrong_root(self) -> None:
        with pytest.raises(InvalidExchangeFileError, match="not an ArchiMate exchange file"):
            parse_model(b'<other xmlns="urn:x"/>')

    def test_an_unknown_namespace_version(self) -> None:
        with pytest.raises(InvalidExchangeFileError, match="namespace"):
            parse_model(b'<model xmlns="http://www.opengroup.org/xsd/archimate/9.9/"/>')

    def test_too_many_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_EXCHANGE_ITEMS", 2)
        xml = exchange_xml(el("a", "Node", "A") + el("b", "Node", "B") + el("c", "Node", "C"))

        with pytest.raises(InvalidExchangeFileError, match="at most 2"):
            parse_model(xml)

    def test_relationships_count_in_the_same_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_EXCHANGE_ITEMS", 2)
        xml = exchange_xml(el("a", "Node", "A") + el("b", "Node", "B"), rel("r1", "Flow", "a", "b"))

        with pytest.raises(InvalidExchangeFileError, match="at most 2"):
            parse_model(xml)

    def test_property_definitions_are_bounded_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_EXCHANGE_ITEMS", 2)
        definitions = "".join(
            f'<propertyDefinition identifier="d{i}" type="string"><name>k{i}</name>'
            "</propertyDefinition>"
            for i in range(3)
        )

        with pytest.raises(InvalidExchangeFileError, match="at most 2"):
            parse_model(
                exchange_xml(extra=f"<propertyDefinitions>{definitions}</propertyDefinitions>")
            )

    def test_nesting_beyond_the_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_NESTING_DEPTH", 10)
        xml = exchange_xml(
            extra="<organizations>" + "<item>" * 20 + "</item>" * 20 + "</organizations>"
        )

        with pytest.raises(InvalidExchangeFileError, match="nested"):
            parse_model(xml)

    def test_a_processing_instruction_is_refused_without_echo(self) -> None:
        xml = exchange_xml(el("a", "Node", "A"), extra="<?exotic payload-secret?>")

        with pytest.raises(InvalidExchangeFileError) as raised:
            parse_model(xml)

        assert "payload-secret" not in str(raised.value)
        assert "exotic" not in str(raised.value)


class TestHostileDocuments:
    """Each payload is real: it would do harm if the parser were not strict."""

    @pytest.mark.parametrize("name", ["billion-laughs.xml", "xxe-file.xml"])
    def test_a_dtd_or_entity_is_refused_without_echo(self, name: str) -> None:
        with pytest.raises(InvalidExchangeFileError) as raised:
            parse_model((DATA / name).read_bytes())

        message = str(raised.value)
        assert "lol" not in message and "hostname" not in message and "secret" not in message

    def test_a_larger_entity_expansion_is_refused_before_any_expansion(self) -> None:
        # Ten levels of ten: a hundred thousand "lol" if it were ever expanded.
        entities = '<!ENTITY a0 "lol">' + "".join(
            f'<!ENTITY a{i} "' + f"&a{i - 1};" * 10 + '">' for i in range(1, 6)
        )
        xml = (
            f'<?xml version="1.0"?><!DOCTYPE m [{entities}]>'
            '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"><name>&a5;</name></model>'
        ).encode()

        with pytest.raises(InvalidExchangeFileError):
            parse_model(xml)

    def test_an_external_entity_reads_no_file_and_leaks_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("TOPSECRET-CONTENT")
        opened: list[str] = []
        real_open = builtins.open

        def spy(file: object, *args: object, **kwargs: object) -> object:
            opened.append(str(file))
            return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr(builtins, "open", spy)
        xml = (
            f'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY s SYSTEM "file://{secret}">]>'
            '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"><name>&s;</name></model>'
        ).encode()

        with pytest.raises(InvalidExchangeFileError) as raised:
            parse_model(xml)

        chain = [raised.value, raised.value.__cause__, raised.value.__context__]
        assert all("TOPSECRET" not in str(error) for error in chain)
        assert str(secret) not in " ".join(str(error) for error in chain)
        assert str(secret) not in opened

    def test_a_parameter_entity_and_an_external_dtd_are_refused(self) -> None:
        xml = (
            b'<?xml version="1.0"?><!DOCTYPE m SYSTEM "http://127.0.0.1:9/evil.dtd">'
            b'<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"/>'
        )

        with pytest.raises(InvalidExchangeFileError):
            parse_model(xml)


class TestRowsThatCannotBeRead:
    def test_a_repeated_identifier_keeps_the_first_and_flags_the_second(self) -> None:
        model = parse_model(exchange_xml(el("a", "Node", "One") + el("a", "Node", "Two")))

        assert [e.name for e in model.elements] == ["One"]
        (duplicate,) = model.unreadable
        assert duplicate.cascade is False
        assert "duplicate_identifier" in codes(model)

    def test_a_missing_identifier_and_an_unknown_type_are_rejected_rows(self) -> None:
        xml = exchange_xml(
            '<element xsi:type="Node"><name>X</name></element>' + el("b", "Spaceship", "Y")
        )

        model = parse_model(xml)

        assert model.elements == ()
        assert {i.code for i in model.issues} == {"missing_identifier", "unknown_type"}
        assert all(i.severity is Severity.ERROR for i in model.issues)
        assert len(model.unreadable) == 2

    def test_a_relationship_without_an_end_is_rejected(self) -> None:
        xml = exchange_xml(
            el("a", "Node", "A"), '<relationship identifier="r" source="a" xsi:type="Flow"/>'
        )

        model = parse_model(xml)

        assert model.relationships == ()
        assert "missing_end" in codes(model)
        assert model.unreadable[0].category is Category.RELATIONSHIPS

    def test_an_object_with_too_many_properties_is_a_rejected_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_PROPERTIES_PER_OBJECT", 2)
        props = (
            "<properties>"
            + "".join(
                f'<property propertyDefinitionRef="d"><value>{i}</value></property>'
                for i in range(3)
            )
            + "</properties>"
        )
        xml = exchange_xml(
            el("a", "Node", "A", props) + el("b", "Node", "B"),
            extra='<propertyDefinitions><propertyDefinition identifier="d" type="string">'
            "<name>k</name></propertyDefinition></propertyDefinitions>",
        )

        model = parse_model(xml)

        assert [e.name for e in model.elements] == ["B"]
        assert "too_many_properties" in codes(model)
        assert model.unreadable[0].identifier == "a"


class TestRelationshipDetails:
    def parse_one(self, kind: str, attributes: str = "", body: str = "") -> ExchangeModel:
        link = (
            f'<relationship identifier="r" source="a" target="b" xsi:type="{kind}" '
            f"{attributes}>{body}</relationship>"
        )
        return parse_model(exchange_xml(el("a", "Node", "A") + el("b", "Node", "B"), link))

    def test_access_defaults_to_write_and_reads_the_attribute(self) -> None:
        assert self.parse_one("Access").relationships[0].access_type is AccessType.WRITE
        read = self.parse_one("Access", 'accessType="ReadWrite"')
        assert read.relationships[0].access_type is AccessType.READ_WRITE

    def test_an_unknown_access_type_falls_back_with_a_warning(self) -> None:
        model = self.parse_one("Access", 'accessType="Teleport"')

        assert model.relationships[0].access_type is AccessType.WRITE
        assert "unknown_access_type" in codes(model)

    def test_association_direction_and_influence_modifier(self) -> None:
        assert self.parse_one("Association", 'isDirected="true"').relationships[0].directed
        assert not self.parse_one("Association").relationships[0].directed
        influence = self.parse_one("Influence", 'modifier="++"')
        assert dict(influence.relationships[0].properties) == {"modifier": "++"}

    def test_a_relationship_documentation_is_dropped_with_a_note(self) -> None:
        model = self.parse_one("Flow", body="<documentation>why</documentation>")

        assert "documentation_dropped" in codes(model)

    def test_a_relationship_keeps_its_properties(self) -> None:
        body = (
            '<properties><property propertyDefinitionRef="d"><value>v</value></property>'
            "</properties>"
        )
        xml = exchange_xml(
            el("a", "Node", "A") + el("b", "Node", "B"),
            rel("r", "Flow", "a", "b", body),
            extra='<propertyDefinitions><propertyDefinition identifier="d" type="string">'
            "<name>k</name></propertyDefinition></propertyDefinitions>",
        )

        assert dict(parse_model(xml).relationships[0].properties) == {"k": "v"}


class TestProperties:
    def definitions(self, *rows: tuple[str, str]) -> str:
        return (
            "<propertyDefinitions>"
            + "".join(
                f'<propertyDefinition identifier="{i}" type="string"><name>{n}</name>'
                "</propertyDefinition>"
                for i, n in rows
            )
            + "</propertyDefinitions>"
        )

    def prop(self, ref: str, value: str) -> str:
        return f'<property propertyDefinitionRef="{ref}"><value>{value}</value></property>'

    def test_reserved_invalid_and_repeated_keys_are_handled(self) -> None:
        extra = (
            "<properties>"
            + self.prop("d1", "keep")
            + self.prop("d2", "text")
            + self.prop("d3", "lost")
            + self.prop("d4", "lost")
            + self.prop("d5", "first")
            + self.prop("d5", "second")
            + self.prop("dx", "orphan")
            + "</properties>"
        )
        xml = exchange_xml(
            el("a", "Node", "A", extra),
            extra=self.definitions(
                ("d1", "owner"),
                ("d2", "ea.description"),
                ("d3", "Owner name"),
                ("d4", "source_id"),
                ("d5", "tier"),
            ),
        )

        model = parse_model(xml)
        element = model.elements[0]

        assert dict(element.properties) == {"owner": "keep", "tier": "second"}
        assert element.description == "text"
        assert set(codes(model)) >= {
            "invalid_property_key",
            "reserved_property_dropped",
            "duplicate_property",
            "unknown_property_definition",
        }


def a_view(body: str, name: str = "V", identifier: str = "v") -> str:
    return f'<view identifier="{identifier}" xsi:type="Diagram"><name>{name}</name>{body}</view>'


def views_of(*views: str, elements: str = "") -> bytes:
    return exchange_xml(
        elements or el("a", "Node", "A"),
        extra=f"<views><diagrams>{''.join(views)}</diagrams></views>",
    )


def box(identifier: str, ref: str, x: str, y: str, w: str = "100", h: str = "50") -> str:
    return (
        f'<node identifier="{identifier}" elementRef="{ref}" xsi:type="Element" '
        f'x="{x}" y="{y}" w="{w}" h="{h}"/>'
    )


class TestViews:
    def test_boxes_are_flattened_to_absolute_coordinates_and_shapes_are_counted(self) -> None:
        model = parse_model((DATA / "views-model.xml").read_bytes())

        (view,) = model.views
        assert (view.identifier, view.name, view.description) == ("v1", "Overview", "Main picture")
        by_element = {box.element: box for box in reversed(view.boxes)}  # the first one wins
        # n1 sits inside the container at (100, 100): 10 + 100, 20 + 100
        assert (by_element["e1"].x, by_element["e1"].y) == (110.0, 120.0)
        assert (by_element["e1"].width, by_element["e1"].height) == (120.0, 55.0)
        assert [b.element for b in view.boxes] == ["e1", "e2", "ghost", "e1"]
        assert (view.connections_ignored, view.shapes_ignored) == (1, 2)

    def test_a_box_with_a_coordinate_that_is_not_a_number_is_skipped_with_a_warning(self) -> None:
        model = parse_model(views_of(a_view(box("n", "a", "oops", "0"))))

        assert model.views[0].boxes == ()
        assert "invalid_box" in codes(model)

    def test_the_numbers_python_would_accept_are_left_to_the_domain_to_judge(self) -> None:
        model = parse_model(
            views_of(a_view(box("n", "a", "nan", "1e308") + box("m", "a", "inf", "0")))
        )

        assert len(model.views[0].boxes) == 2  # read as they are; `validate_box` refuses them

    def test_a_view_without_an_identifier_or_with_a_repeated_one_is_rejected(self) -> None:
        xml = exchange_xml(
            extra='<views><diagrams><view identifier="v" xsi:type="Diagram"><name>A</name></view>'
            '<view identifier="v" xsi:type="Diagram"><name>B</name></view>'
            '<view xsi:type="Diagram"><name>C</name></view></diagrams></views>'
        )

        model = parse_model(xml)

        assert [v.name for v in model.views] == ["A"]
        assert {u.category for u in model.unreadable} == {Category.VIEWS}
        assert {"duplicate_identifier", "missing_identifier"} <= set(codes(model))

    def test_a_view_cannot_take_the_identifier_of_an_element(self) -> None:
        model = parse_model(views_of(a_view("", identifier="a")))

        assert model.views == () and "duplicate_identifier" in codes(model)

    def test_views_after_the_elements_do_not_empty_the_model(self) -> None:
        model = parse_model(
            views_of(
                a_view(box("n", "a", "1", "2")), a_view("", "W", "w"), elements=el("a", "Node", "A")
            )
        )

        assert [e.name for e in model.elements] == ["A"] and len(model.views) == 2

    def test_nodes_nested_beyond_the_ceiling_are_refused(self) -> None:
        inner = "".join(
            f'<node identifier="n{i}" xsi:type="Container" x="0" y="0" w="1" h="1">'
            for i in range(201)
        )
        xml = views_of(a_view(inner + "</node>" * 201))

        with pytest.raises(InvalidExchangeFileError, match="nested"):
            parse_model(xml)

    def test_a_view_with_too_many_nodes_is_refused_before_it_is_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_NODES_PER_VIEW", 3)
        boxes = "".join(box(f"n{i}", "a", "0", "0") for i in range(4))

        with pytest.raises(InvalidExchangeFileError, match="nodes in one view"):
            parse_model(views_of(a_view(boxes)))

    def test_too_many_views_are_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_EXCHANGE_VIEWS", 2)

        with pytest.raises(InvalidExchangeFileError, match="at most 2 views"):
            parse_model(views_of(*[a_view("", f"V{i}", f"v{i}") for i in range(3)]))

    def test_too_many_boxes_in_all_are_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ea.formats.archimate_xml.MAX_FILE_VIEW_BOXES", 3)
        boxes = "".join(box(f"n{i}", "a", "0", "0") for i in range(2))

        with pytest.raises(InvalidExchangeFileError, match="at most 3 boxes"):
            parse_model(views_of(a_view(boxes, "A", "v1"), a_view(boxes, "B", "v2")))

    def test_the_views_memory_is_let_go_as_it_is_read(self) -> None:
        many = [a_view(box(f"n{i}", "a", "0", "0"), f"V{i}", f"v{i}") for i in range(200)]

        assert len(parse_model(views_of(*many)).views) == 200
