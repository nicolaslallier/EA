"""The report engines that need native libraries, and the policy that closes them."""

from __future__ import annotations

import io
import logging
import zipfile

import pytest

import ea.repositories.report_renderers as renderers
from ea.domain.diagram_render import render_svg
from ea.domain.reports import (
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)
from ea.repositories.report_renderers import (
    available_renderers,
    docx_renderer,
    pdf_renderer,
    refuse_every_url,
)
from tests.unit.test_diagram_render import a_detail, an_element


def a_pdf_engine():  # type: ignore[no-untyped-def]
    try:
        return pdf_renderer()
    except (ImportError, OSError) as error:
        pytest.skip(f"WeasyPrint is not loadable here ({error}); see docs/adr/0062")


def model(*blocks: object) -> ReportModel:
    return ReportModel(title="Rapport", subtitle="Généré", blocks=tuple(blocks))  # type: ignore[arg-type]


class TestTheClosedFetcher:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",
            "https://example.org/pixel.png",
            "file:///etc/passwd",
            "data:text/plain;base64,AAAA",
            "ftp://host/x",
        ],
    )
    def test_nothing_is_ever_fetched(self, url: str) -> None:
        with pytest.raises(ValueError, match="refused"):
            refuse_every_url(url)


class TestThePdf:
    def test_a_report_becomes_a_pdf(self) -> None:
        engine = a_pdf_engine()
        svg = render_svg(a_detail([(an_element(), 40, 40, 132, 46)]))

        pdf = engine(
            model(
                Heading(2, "Titre"),
                Paragraph("Texte"),
                Table(("Nom", "Type"), (("A", "x"),)),
                Figure(svg, "Vente"),
                MarkdownBlock("# Titre\n\n- a\n- b\n"),
            )
        )

        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000

    def test_an_empty_report_is_still_a_pdf(self) -> None:
        assert a_pdf_engine()(model()).startswith(b"%PDF")

    def test_non_latin_names_do_not_break_the_rendering(self) -> None:
        engine = a_pdf_engine()

        pdf = engine(
            model(
                Heading(2, "Ελληνικά Кириллица 日本語"),
                Table(("Nom",), (("Σύστημα ΕΑ",), ("Система",), ("システム",))),  # noqa: RUF001 - non-Latin on purpose
            )
        )

        assert pdf.startswith(b"%PDF")

    def test_markup_and_remote_references_in_the_content_fetch_nothing(self) -> None:
        engine = a_pdf_engine()

        pdf = engine(
            model(
                Paragraph('<img src="http://127.0.0.1:9/x.png"> <script>1</script> & "q"'),
                MarkdownBlock("![x](http://127.0.0.1:9/y.png)\n\n<img src=file:///etc/passwd>"),
            )
        )

        assert pdf.startswith(b"%PDF")


class TestWhatIsOffered:
    def test_a_loadable_engine_is_offered(self) -> None:
        a_pdf_engine()

        assert "pdf" in available_renderers()

    def test_an_engine_that_cannot_load_is_left_out_and_said_in_the_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken() -> None:
            raise OSError("cannot load library 'libgobject-2.0-0'")

        monkeypatch.setattr(renderers, "pdf_renderer", broken)

        with caplog.at_level(logging.WARNING):
            offered = available_renderers()

        assert "pdf" not in offered
        assert any(getattr(r, "action", "") == "report_format_unavailable" for r in caplog.records)


def document_xml(docx: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        return archive.read("word/document.xml").decode()


class TestTheDocx:
    def test_a_report_becomes_a_zip_with_a_document(self) -> None:
        docx = docx_renderer()(
            model(
                Heading(2, "Titre"),
                Paragraph("Texte"),
                Table(("Nom", "Type"), (("Facturation", "Application Component"),)),
                MarkdownBlock("# Doc\n\n- a\n"),
            )
        )

        xml = document_xml(docx)
        assert docx.startswith(b"PK")
        assert "Facturation" in xml and "Titre" in xml

    def test_markup_in_a_name_is_text_in_the_document(self) -> None:
        xml = document_xml(
            docx_renderer()(model(Paragraph("<w:p>&</w:p>"), Table(("<b>",), (("</w:t>",),))))
        )

        assert "&lt;w:p&gt;&amp;&lt;/w:p&gt;" in xml
        assert "&lt;/w:t&gt;" in xml

    def test_characters_xml_forbids_do_not_break_it(self) -> None:
        xml = document_xml(docx_renderer()(model(Paragraph("A\x00B\x0bC"), Heading(2, "D\x1fE"))))

        assert "ABC" in xml and "DE" in xml

    def test_a_diagram_is_replaced_by_a_note_not_embedded(self) -> None:
        svg = render_svg(a_detail([(an_element("Facturation"), 40, 40, 132, 46)]))

        xml = document_xml(docx_renderer()(model(Figure(svg, "Vente"))))

        assert "Vente" in xml
        assert "<svg" not in xml and "&lt;svg" not in xml

    def test_an_empty_report_is_still_a_valid_document(self) -> None:
        assert document_xml(docx_renderer()(model())).startswith("<?xml")

    def test_non_latin_text_survives(self) -> None:
        xml = document_xml(docx_renderer()(model(Paragraph("Ελληνικά Кириллица 日本語"))))

        assert "Ελληνικά Кириллица 日本語" in xml


def test_docx_is_offered_with_the_pdf_engine_when_it_loads() -> None:
    assert "docx" in available_renderers()
