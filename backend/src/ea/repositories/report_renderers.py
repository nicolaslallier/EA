"""The report formats that need native libraries: PDF now, DOCX in the next lot.

Each engine is imported on demand and an engine that cannot load is *left out*,
not fatal: HTML is always served, and `GET /report-templates` lists the formats
this deployment really produces (docs/adr/0062).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import NoReturn

from ea.domain.report_html import to_html
from ea.domain.reports import Renderer, ReportFormat, ReportModel

logger = logging.getLogger(__name__)


def refuse_every_url(url: str, *args: object, **kwargs: object) -> NoReturn:
    """WeasyPrint's URL fetcher, closed: a report is self-contained.

    Neither the network (SSRF toward the Infra's hosts or a metadata endpoint)
    nor the local disk (`file:///etc/passwd` through an `<img>`) is ever read,
    and `data:` is refused too: the HTML this engine receives has none.
    """
    msg = f"fetching {url!r} is refused: a report is self-contained"
    raise ValueError(msg)


def pdf_renderer() -> Renderer:
    """The PDF engine. Raises `ImportError` or `OSError` when WeasyPrint or Pango is missing."""
    from weasyprint import HTML

    def render(model: ReportModel) -> bytes:
        pdf = HTML(string=to_html(model), url_fetcher=refuse_every_url).write_pdf()
        if pdf is None:  # only when a `target` is given, never here
            msg = "WeasyPrint returned no bytes"
            raise RuntimeError(msg)
        return bytes(pdf)

    return render


def available_renderers() -> dict[ReportFormat, Renderer]:
    """Every extra format whose engine loads on this machine."""
    engines: tuple[tuple[ReportFormat, Callable[[], Renderer]], ...] = (("pdf", pdf_renderer),)
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
