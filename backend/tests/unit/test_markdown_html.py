"""Markdown becomes HTML a browser or a PDF engine can be handed, and nothing more."""

from __future__ import annotations

from ea.domain.markdown_html import render_markdown


def test_ordinary_markdown_is_rendered() -> None:
    html = render_markdown("# Titre\n\nUn *mot* et `du code`.\n\n- a\n- b\n")

    assert "<h1>Titre</h1>" in html
    assert "<em>mot</em>" in html
    assert "<code>du code</code>" in html
    assert "<li>a</li>" in html


def test_raw_html_is_text_not_markup() -> None:
    html = render_markdown("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>")

    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_dangerous_link_schemes_are_not_links() -> None:
    html = render_markdown(
        "[a](javascript:alert(1)) [b](JavaScript:alert(1)) [c](data:text/html,x) "
        "[d](vbscript:x) <javascript:alert(1)>"
    )

    assert 'href="javascript' not in html.lower()
    assert 'href="data' not in html.lower()
    assert 'href="vbscript' not in html.lower()


def test_a_normal_link_survives() -> None:
    assert 'href="https://example.org/x"' in render_markdown("[x](https://example.org/x)")
    assert 'href="mailto:a@b.example"' in render_markdown("[m](mailto:a@b.example)")
    assert 'href="#section"' in render_markdown("[s](#section)")


def test_only_web_and_mail_links_are_links() -> None:
    html = render_markdown("[i](data:image/png;base64,AAAA) [f](ftp://h/x) [t](tel:112)")

    assert "href=" not in html


def test_images_are_not_loaded() -> None:
    html = render_markdown(
        "![logo](https://evil.example/pixel.png) ![d](data:image/png;base64,AAAA)"
    )

    assert "<img" not in html


def test_a_fenced_block_shows_its_markup_as_text() -> None:
    html = render_markdown("```html\n<script>x</script>\n```")

    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_the_empty_document_is_the_empty_string() -> None:
    assert render_markdown("") == ""


def test_a_protocol_relative_link_is_not_a_link() -> None:
    html = render_markdown("[a](//evil.example/x) [b](  //evil.example) [c](/local) [d](x/y)")

    assert 'href="//' not in html
    assert html.count("<a ") == 2
    assert 'href="/local"' in html
    assert 'href="x/y"' in html
