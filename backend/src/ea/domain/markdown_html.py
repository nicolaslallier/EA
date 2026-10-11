"""Markdown to a safe HTML fragment — one renderer for reports and the reader mode.

Pure: no I/O, deterministic. The three settings that make it safe are the whole
point: raw HTML is disabled (it is shown as text), the `image` rule is disabled
(a report must not fetch anything, and the PDF engine refuses every URL anyway),
and `validateLink` is replaced by an allow-list: a link is relative, `http`,
`https` or `mailto`, and nothing else (the library's own default would let
`data:image/...` through). The SPA may therefore insert the result with `v-html`.
"""

from __future__ import annotations

import re
from typing import Final

from markdown_it import MarkdownIt

_SCHEME: Final = re.compile(r"^\s*([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SAFE_SCHEMES: Final = frozenset({"http", "https", "mailto"})


def _only_safe_links(url: str) -> bool:
    """A relative link, or one that goes to the web or to a mailbox. Nothing else.

    markdown-it's own default lets `data:image/png;...` through as a link target;
    a report has no use for any scheme beyond these three.
    """
    scheme = _SCHEME.match(url)
    return scheme is None or scheme.group(1).lower() in _SAFE_SCHEMES


_RENDERER: Final = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")
_RENDERER.validateLink = _only_safe_links  # type: ignore[method-assign]


def render_markdown(source: str) -> str:
    """The HTML fragment for `source`; text from the author never becomes markup."""
    return str(_RENDERER.render(source))
