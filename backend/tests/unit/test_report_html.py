"""The self-contained HTML report: escaped text, inline SVG, no external reference."""

from __future__ import annotations

from ea.domain.report_html import to_html
from ea.domain.reports import (
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)

NASTY = "<script>alert(1)</script> & \"q\" 's'"


def a_model(*blocks: object, title: str = "Rapport") -> ReportModel:
    return ReportModel(title=title, subtitle="Généré", blocks=tuple(blocks))  # type: ignore[arg-type]


def test_a_document_is_complete_and_declares_its_encoding_and_policy() -> None:
    html = to_html(a_model())

    assert html.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert "<h1>Rapport</h1>" in html


def test_no_external_reference_is_ever_emitted() -> None:
    html = to_html(a_model(Heading(2, "A"), Paragraph("b"), Table(("h",), (("c",),))))

    for forbidden in ("http://", "https://", "<link", "src=", "@import"):
        assert forbidden not in html


def test_every_text_from_a_person_is_escaped() -> None:
    model = a_model(
        Heading(2, NASTY),
        Paragraph(NASTY),
        Table((NASTY,), ((NASTY,),)),
        Figure("<svg></svg>", NASTY),
        title=NASTY,
    )

    html = to_html(model)

    assert "<script>" not in html
    assert html.count("&lt;script&gt;") >= 5


def test_a_figure_inlines_the_svg_untouched_with_an_escaped_caption() -> None:
    html = to_html(a_model(Figure('<svg id="d"></svg>', "Légende <b>")))

    assert '<figure><svg id="d"></svg>' in html
    assert "<figcaption>Légende &lt;b&gt;</figcaption>" in html


def test_markdown_goes_through_the_safe_renderer() -> None:
    html = to_html(a_model(MarkdownBlock("# T\n\n<script>x</script>")))

    assert "<h1>T</h1>" in html
    assert "<script>" not in html


def test_a_table_has_header_cells_and_body_rows() -> None:
    html = to_html(a_model(Table(("Nom", "Type"), (("A", "x"), ("B", "y")))))

    assert "<th>Nom</th><th>Type</th>" in html
    assert "<tr><td>A</td><td>x</td></tr>" in html


def test_heading_levels_stay_within_html() -> None:
    html = to_html(a_model(Heading(0, "a"), Heading(9, "b")))

    assert "<h1>a</h1>" in html
    assert "<h6>b</h6>" in html
