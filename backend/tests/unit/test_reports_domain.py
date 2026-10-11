"""Templates, the closed substitution, and the model built from a scope."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode
from ea.domain.errors import UnknownReportTemplateError
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView
from ea.domain.reports import (
    BUILT_IN_TEMPLATES,
    DiagramSection,
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportScope,
    ReportTemplate,
    ScopedDocument,
    Table,
    TextSection,
    build_model,
    report_filename,
    report_key,
    slugify,
    substitute,
    template_by_id,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(name: str = "Facturation", description: str = "") -> Element:
    return Element.create(
        element_type=ElementType.APPLICATION_COMPONENT, name=name, now=NOW, description=description
    )


def a_scope(
    elements: tuple[Element, ...] = (),
    links: tuple[Relationship, ...] = (),
    name: str = "Vente",
    description: str = "",
) -> ReportScope:
    diagram = Diagram.create(name=name, description=description, now=NOW)
    nodes = tuple(DiagramNode(e.id, 10 * i, 10) for i, e in enumerate(elements))
    detail = DiagramDetail(diagram, nodes, GraphView(elements, links))
    return ReportScope(
        kind="diagram",
        name=name,
        description=description,
        diagrams=(detail,),
        elements=elements,
        relationships=links,
    )


SHEET = template_by_id("fiche-diagramme")


class TestTheTemplates:
    def test_the_diagram_sheet_is_built_in_and_takes_a_diagram(self) -> None:
        assert SHEET.subjects == frozenset({"diagram"})
        assert SHEET.version == 1
        assert "fiche-diagramme" in BUILT_IN_TEMPLATES

    def test_an_unknown_template_is_a_domain_refusal(self) -> None:
        with pytest.raises(UnknownReportTemplateError, match="nope"):
            template_by_id("nope")

    def test_a_template_with_an_unknown_field_cannot_exist(self) -> None:
        with pytest.raises(ValueError, match=r"subject\.secret"):
            ReportTemplate(
                id="x",
                version=1,
                title="X",
                subjects=frozenset({"diagram"}),
                sections=(TextSection("{{subject.secret}}"),),
            )


class TestTheClosedSubstitution:
    def test_known_fields_are_replaced(self) -> None:
        fields = {"name": "Vente", "description": "d", "kind": "diagram"}

        assert substitute("A {{subject.name}} / {{ subject.kind }}", fields) == "A Vente / diagram"

    def test_markdown_syntax_in_a_value_stays_literal(self) -> None:
        fields = {"name": "*x* [a](http://e) <b>", "description": "", "kind": "diagram"}

        out = substitute("{{subject.name}}", fields)

        assert out == r"\*x\* \[a\]\(http://e\) \<b\>"


class TestBuildingTheModel:
    def test_the_title_names_the_template_and_the_subject(self) -> None:
        model = build_model(SHEET, a_scope(), (), now=NOW)

        assert model.title == "Fiche de diagramme — Vente"
        assert "2026-10-10 12:00 UTC" in model.subtitle
        assert "fiche-diagramme v1" in model.subtitle

    def test_the_introduction_substitutes_the_subject_as_literal_text(self) -> None:
        model = build_model(SHEET, a_scope(name="**gras**"), (), now=NOW)

        intro = next(b for b in model.blocks if isinstance(b, MarkdownBlock))
        assert r"\*\*gras\*\*" in intro.source

    def test_an_empty_diagram_gives_honest_empty_sections(self) -> None:
        model = build_model(SHEET, a_scope(), (), now=NOW)

        texts = [b.text for b in model.blocks if isinstance(b, Paragraph)]
        assert texts == ["Aucun élément.", "Aucune relation.", "Aucun document."]
        assert any(isinstance(b, Figure) for b in model.blocks)

    def test_elements_are_tabulated_in_name_order(self) -> None:
        zebra, alpha = an_element("Zèbre"), an_element("Alpha", "premier")

        model = build_model(SHEET, a_scope((zebra, alpha)), (), now=NOW)

        table = next(b for b in model.blocks if isinstance(b, Table))
        assert table.headers == ("Nom", "Type", "Couche", "Description")
        assert [row[0] for row in table.rows] == ["Alpha", "Zèbre"]
        assert table.rows[0] == ("Alpha", "Application Component", "Application", "premier")

    def test_relations_are_grouped_by_type_with_both_ends_named(self) -> None:
        left, right = an_element("A"), an_element("B")
        link = Relationship.between(RelationshipType.ASSOCIATION, left, right, now=NOW)

        model = build_model(SHEET, a_scope((left, right), (link,)), (), now=NOW)

        assert Heading(3, "Association") in model.blocks
        tables = [b for b in model.blocks if isinstance(b, Table)]
        assert tables[-1].rows == (("A", "est associé à", "B"),)

    def test_documents_are_listed_under_their_element(self) -> None:
        docs = (ScopedDocument("Facturation", "runbook.md", "# Run\n"),)

        model = build_model(SHEET, a_scope((an_element(),)), docs, now=NOW)

        assert Heading(3, "Facturation — runbook.md") in model.blocks
        assert MarkdownBlock("# Run\n") in model.blocks

    def test_every_diagram_section_draws_each_diagram_of_the_scope(self) -> None:
        template = ReportTemplate(
            id="t",
            version=1,
            title="T",
            subjects=frozenset({"diagram"}),
            sections=(DiagramSection(),),
        )

        model = build_model(template, a_scope(), (), now=NOW)

        figures = [b for b in model.blocks if isinstance(b, Figure)]
        assert [f.caption for f in figures] == ["Vente"]
        assert figures[0].svg.startswith("<svg")


class TestNames:
    def test_a_subject_becomes_an_ascii_slug(self) -> None:
        assert slugify("Paysage d'été — SI/Finance") == "paysage-d-ete-si-finance"
        assert slugify("日本語") == "sujet"
        assert len(slugify("x" * 100)) == 40

    def test_the_key_is_under_reports_and_the_year(self) -> None:
        name = report_filename(
            now=NOW,
            subject_name="Vente",
            template_id="fiche-diagramme",
            fmt="pdf",
            unique="ab12cd34",
        )

        assert name == "2026-10-10-vente-fiche-diagramme-ab12cd34.pdf"
        assert report_key(name, NOW) == "reports/2026/2026-10-10-vente-fiche-diagramme-ab12cd34.pdf"

    def test_a_download_name_has_no_unique_part(self) -> None:
        name = report_filename(
            now=NOW, subject_name="Vente", template_id="fiche-diagramme", fmt="html"
        )

        assert name == "2026-10-10-vente-fiche-diagramme.html"


class TestThePackageTemplate:
    def test_the_inventory_takes_a_package_and_needs_no_documents(self) -> None:
        inventory = template_by_id("inventaire-paquet")

        assert inventory.subjects == frozenset({"package"})
        assert inventory.needs_documents is False

    def test_a_package_with_two_diagrams_draws_both(self) -> None:
        inventory = template_by_id("inventaire-paquet")
        scope = a_scope()
        two = ReportScope("package", "Finance", "", scope.diagrams * 2, (), ())

        model = build_model(inventory, two, (), now=NOW)

        assert len([b for b in model.blocks if isinstance(b, Figure)]) == 2
        assert model.title == "Inventaire d'un paquet — Finance"
