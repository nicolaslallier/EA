"""The report formats that need native libraries: PDF and DOCX.

Each engine is imported on demand and an engine that cannot load is *left out*,
not fatal: HTML is always served, and `GET /report-templates` lists the formats
this deployment really produces (docs/adr/0062).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from typing import NoReturn

from ea.domain.diagram_render import xml_safe
from ea.domain.report_html import to_html
from ea.domain.reports import (
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    Renderer,
    ReportFormat,
    ReportModel,
    Table,
)

logger = logging.getLogger(__name__)


class RefusingFetcher:
    """WeasyPrint's URL fetcher, closed: a report is self-contained.

    WeasyPrint 70 reads attributes of its fetcher (`_fail_on_errors`), so a bare function is not
    enough. This one inherits the interface lazily (the import loads Pango) and refuses every
    URL with a `ValueError`, which WeasyPrint catches and logs: the resource is ignored and the
    PDF is still made. Neither the network (SSRF toward the Infra's hosts or a metadata
    endpoint) nor the local disk (`file:///etc/passwd` through an `<img>`) is ever read, and
    `data:` is refused too: the HTML this engine receives has none.
    """

    _fail_on_errors = False

    def fetch(self, url: str, headers: object = None) -> NoReturn:
        msg = f"fetching {url!r} is refused: a report is self-contained"
        raise ValueError(msg)

    def __call__(self, url: str, *args: object, **kwargs: object) -> NoReturn:
        self.fetch(url)


def pdf_renderer() -> Renderer:
    """The PDF engine. Raises `ImportError` or `OSError` when WeasyPrint or Pango is missing."""
    from weasyprint import HTML

    def render(model: ReportModel) -> bytes:
        pdf = HTML(string=to_html(model), url_fetcher=RefusingFetcher()).write_pdf()
        if pdf is None:  # only when a `target` is given, never here
            msg = "WeasyPrint returned no bytes"
            raise RuntimeError(msg)
        return bytes(pdf)

    return render


def docx_renderer() -> Renderer:
    """The DOCX engine (python-docx). Raises `ImportError` when it is not installed."""
    from docx import Document

    def render(model: ReportModel) -> bytes:
        document = Document()
        document.core_properties.title = xml_safe(model.title)[:255]
        document.add_heading(xml_safe(model.title), level=0)
        document.add_paragraph(xml_safe(model.subtitle))
        for block in model.blocks:
            match block:
                case Heading():
                    document.add_heading(xml_safe(block.text), level=min(max(block.level, 1), 9))
                case Paragraph():
                    document.add_paragraph(xml_safe(block.text))
                case MarkdownBlock():
                    # The markdown source as plain lines: Word has no markdown. The
                    # syntax stays visible; a faithful conversion is a later lot.
                    for line in block.source.splitlines():
                        if line.strip():
                            document.add_paragraph(xml_safe(line))
                case Table():
                    table = document.add_table(rows=1, cols=max(len(block.headers), 1))
                    table.style = "Table Grid"
                    for cell, text in zip(table.rows[0].cells, block.headers, strict=False):
                        cell.text = xml_safe(text)
                    for row in block.rows:
                        for cell, text in zip(table.add_row().cells, row, strict=False):
                            cell.text = xml_safe(text)
                case Figure():
                    document.add_paragraph(
                        f"Diagramme « {xml_safe(block.caption)} » : le dessin figure dans le "
                        "rapport HTML ou PDF; ses éléments et ses relations sont listés "
                        "dans les tableaux de ce document."
                    )
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    return render


def available_renderers() -> dict[ReportFormat, Renderer]:
    """Every extra format whose engine loads on this machine."""
    engines: tuple[tuple[ReportFormat, Callable[[], Renderer]], ...] = (
        ("pdf", pdf_renderer),
        ("docx", docx_renderer),
    )
    found: dict[ReportFormat, Renderer] = {}
    for name, factory in engines:
        try:
            found[name] = factory()
        except (ImportError, OSError) as error:
            logger.warning(
                "%s reports are unavailable: %s",
                name,
                error,
                extra={"action": "report_format_unavailable", "report_format": name},
            )
    return found
