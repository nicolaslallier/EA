"""Small builders for exchange files in tests — text, never a fixture of someone else's."""

from __future__ import annotations

from pathlib import Path

from ea.domain.exchange import NAMESPACE

DATA = Path(__file__).parent / "data" / "exchange"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"


def exchange_xml(elements: str = "", relationships: str = "", extra: str = "") -> bytes:
    body = "<name>M</name>"
    if elements:
        body += f"<elements>{elements}</elements>"
    if relationships:
        body += f"<relationships>{relationships}</relationships>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model xmlns="{NAMESPACE}" xmlns:xsi="{_XSI}" identifier="m">{body}{extra}</model>'
    ).encode()


def el(identifier: str, kind: str, name: str, extra: str = "") -> str:
    return (
        f'<element identifier="{identifier}" xsi:type="{kind}"><name>{name}</name>{extra}</element>'
    )


def rel(identifier: str, kind: str, source: str, target: str, extra: str = "") -> str:
    return (
        f'<relationship identifier="{identifier}" source="{source}" target="{target}" '
        f'xsi:type="{kind}">{extra}</relationship>'
    )
