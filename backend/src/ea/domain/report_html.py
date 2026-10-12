"""The self-contained HTML report: the one serialiser PDF reuses as its input.

Everything is inline (style, SVG) and a `<meta>` policy forbids any fetch, so a
report opened from a download or printed to PDF reaches nothing. Every text from
a person is escaped here; markdown goes through `render_markdown`; the SVG of a
figure is trusted because `render_svg` escaped it.
"""

from __future__ import annotations

import html
from typing import Final

from ea.domain.markdown_html import render_markdown
from ea.domain.reports import (
    Block,
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)

_STYLE: Final = """
@page { size: A4; margin: 18mm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10pt; color: #24202b; line-height: 1.45; }
h1 { font-size: 20pt; margin-bottom: 0; }
h2 { font-size: 14pt; border-bottom: 1px solid #c9c4d0; padding-bottom: 2pt; margin-top: 22pt; }
h3 { font-size: 11pt; margin-top: 14pt; }
.meta { color: #6a6475; margin-top: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #c9c4d0; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #f0eef4; }
figure { margin: 10pt 0; }
figure svg { max-width: 100%; height: auto; }
figcaption { color: #6a6475; font-size: 9pt; }
pre, code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; }
pre { background: #f6f5f8; padding: 6pt; white-space: pre-wrap; }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _block(block: Block) -> str:
    match block:
        case Heading():
            level = min(max(block.level, 1), 6)
            return f"<h{level}>{_e(block.text)}</h{level}>"
        case Paragraph():
            return f"<p>{_e(block.text)}</p>"
        case MarkdownBlock():
            return render_markdown(block.source)
        case Table():
            head = "".join(f"<th>{_e(h)}</th>" for h in block.headers)
            body = "".join(
                "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in block.rows
            )
            return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        case Figure():
            return f"<figure>{block.svg}<figcaption>{_e(block.caption)}</figcaption></figure>"


def to_html(model: ReportModel) -> str:
    body = "\n".join(_block(block) for block in model.blocks)
    return (
        "<!doctype html>\n"
        '<html lang="fr"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        f"<title>{_e(model.title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head><body>\n"
        f"<h1>{_e(model.title)}</h1>\n"
        f'<p class="meta">{_e(model.subtitle)}</p>\n'
        f"{body}\n"
        "</body></html>\n"
    )
