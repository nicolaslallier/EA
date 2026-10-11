"""What a report is: a template, a scope read from the catalogue, and the neutral
document built from the two.

Pure (docs/adr/0062). Three serialisers turn the one
`ReportModel` into HTML, PDF or DOCX, so the content logic exists once. Templates
are code, not evaluated text: the only substitution is `{{subject.<field>}}`
from a closed table, and the value is escaped as markdown.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal
from uuid import UUID

from ea.domain.archimate import RelationshipType
from ea.domain.diagram_render import render_svg, verb_of
from ea.domain.diagrams import DiagramDetail
from ea.domain.errors import UnknownReportTemplateError
from ea.domain.model import Element, Relationship

MAX_REPORT_ELEMENTS: Final = 500
MAX_REPORT_RELATIONSHIPS: Final = 5000
MAX_REPORT_DIAGRAMS: Final = 20
MAX_REPORT_DOCUMENT_BYTES: Final = 2 * 1024 * 1024
REPORT_TIMEOUT_SECONDS: Final = 30.0

ReportFormat = Literal["html", "pdf", "docx"]
SubjectKind = Literal["package", "diagram", "query"]
REPORT_FORMATS: Final[tuple[ReportFormat, ...]] = ("html", "pdf", "docx")
CONTENT_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "html": "text/html; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
SUBJECT_FIELDS: Final = ("name", "description", "kind")


@dataclass(frozen=True, slots=True)
class ReportSubject:
    kind: SubjectKind
    id: UUID


@dataclass(frozen=True, slots=True)
class ReportRequest:
    template_id: str
    subject: ReportSubject
    format: ReportFormat


# --- Sections ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextSection:
    markdown: str


@dataclass(frozen=True, slots=True)
class ElementTableSection:
    columns: tuple[Literal["name", "type", "layer", "description"], ...]
    title: str = "Éléments"


@dataclass(frozen=True, slots=True)
class DiagramSection:
    """The diagram of the subject, or every diagram of a package."""


@dataclass(frozen=True, slots=True)
class RelationsSection:
    title: str = "Relations"


@dataclass(frozen=True, slots=True)
class DocumentsSection:
    title: str = "Documents"


type Section = (
    TextSection | ElementTableSection | DiagramSection | RelationsSection | DocumentsSection
)

_PLACEHOLDER: Final = re.compile(r"\{\{\s*subject\.(\w+)\s*\}\}")
_MARKDOWN_SPECIALS: Final = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~&])")


def substitute(text: str, fields: Mapping[str, str]) -> str:
    """Replace `{{subject.<field>}}` from a closed table; the value stays literal text."""

    def one(match: re.Match[str]) -> str:
        field = match.group(1)
        if field not in SUBJECT_FIELDS:
            msg = f"unknown template field subject.{field}"
            raise ValueError(msg)
        return _MARKDOWN_SPECIALS.sub(r"\\\1", fields[field])

    return _PLACEHOLDER.sub(one, text)


@dataclass(frozen=True, slots=True)
class ReportTemplate:
    """Shaped as a stored row would be (id, version, definition), so editable
    templates later add a table without changing this type."""

    id: str
    version: int
    title: str
    subjects: frozenset[SubjectKind]
    sections: tuple[Section, ...]

    def __post_init__(self) -> None:
        blank = dict.fromkeys(SUBJECT_FIELDS, "")
        for section in self.sections:
            if isinstance(section, TextSection):
                substitute(section.markdown, blank)  # raises on an unknown field

    @property
    def needs_documents(self) -> bool:
        return any(isinstance(section, DocumentsSection) for section in self.sections)


DIAGRAM_SHEET: Final = ReportTemplate(
    id="fiche-diagramme",
    version=1,
    title="Fiche de diagramme",
    subjects=frozenset({"diagram"}),
    sections=(
        TextSection(
            "Ce rapport décrit le diagramme **{{subject.name}}**.\n\n{{subject.description}}"
        ),
        DiagramSection(),
        ElementTableSection(columns=("name", "type", "layer", "description")),
        RelationsSection(),
        DocumentsSection(),
    ),
)

PACKAGE_INVENTORY: Final = ReportTemplate(
    id="inventaire-paquet",
    version=1,
    title="Inventaire d'un paquet",
    subjects=frozenset({"package"}),
    sections=(
        TextSection("Inventaire du paquet **{{subject.name}}**.\n\n{{subject.description}}"),
        ElementTableSection(columns=("name", "type", "layer", "description")),
        RelationsSection(),
        DiagramSection(),
    ),
)

BUILT_IN_TEMPLATES: Final[Mapping[str, ReportTemplate]] = MappingProxyType(
    {DIAGRAM_SHEET.id: DIAGRAM_SHEET, PACKAGE_INVENTORY.id: PACKAGE_INVENTORY}
)


def template_by_id(template_id: str) -> ReportTemplate:
    try:
        return BUILT_IN_TEMPLATES[template_id]
    except KeyError:
        msg = f"no report template {template_id!r}; GET /report-templates lists them"
        raise UnknownReportTemplateError(msg) from None


# --- Scope read by the service ---------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopedDocument:
    element_name: str
    filename: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ReportScope:
    kind: SubjectKind
    name: str
    description: str
    diagrams: tuple[DiagramDetail, ...]
    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]


# --- The neutral document ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    source: str


@dataclass(frozen=True, slots=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Figure:
    """An SVG drawing already escaped by `render_svg`, and its caption."""

    svg: str
    caption: str


type Block = Heading | Paragraph | MarkdownBlock | Table | Figure


@dataclass(frozen=True, slots=True)
class ReportModel:
    title: str
    subtitle: str
    blocks: tuple[Block, ...]


type Renderer = Callable[[ReportModel], bytes]


@dataclass(frozen=True, slots=True)
class RenderedReport:
    content: bytes
    content_type: str
    filename: str
    duration_ms: int
    subject_name: str


_COLUMN_HEADERS: Final = {
    "name": "Nom",
    "type": "Type",
    "layer": "Couche",
    "description": "Description",
}


def _layer_label(element: Element) -> str:
    return element.element_type.layer.value.replace("_", " ").capitalize()


def _cell(element: Element, column: str) -> str:
    match column:
        case "name":
            return element.name
        case "type":
            return element.element_type.label
        case "layer":
            return _layer_label(element)
        case _:
            return element.description


def build_model(
    template: ReportTemplate,
    scope: ReportScope,
    documents: Sequence[ScopedDocument],
    *,
    now: datetime,
) -> ReportModel:
    """The neutral document for `template` over `scope`: pure, ordered, deterministic."""
    fields = {"name": scope.name, "description": scope.description, "kind": scope.kind}
    names = {element.id: element.name for element in scope.elements}
    ordered = sorted(scope.elements, key=lambda e: (e.name.casefold(), str(e.id)))
    blocks: list[Block] = []
    for section in template.sections:
        match section:
            case TextSection():
                blocks.append(MarkdownBlock(substitute(section.markdown, fields)))
            case DiagramSection():
                for detail in scope.diagrams:
                    blocks += [
                        Heading(2, detail.diagram.name),
                        Figure(render_svg(detail), detail.diagram.name),
                    ]
            case ElementTableSection():
                blocks.append(Heading(2, section.title))
                if not ordered:
                    blocks.append(Paragraph("Aucun élément."))
                else:
                    blocks.append(
                        Table(
                            headers=tuple(_COLUMN_HEADERS[c] for c in section.columns),
                            rows=tuple(
                                tuple(_cell(e, c) for c in section.columns) for e in ordered
                            ),
                        )
                    )
            case RelationsSection():
                blocks.append(Heading(2, section.title))
                if not scope.relationships:
                    blocks.append(Paragraph("Aucune relation."))
                order = list(RelationshipType)
                for kind in sorted(
                    {r.relationship_type for r in scope.relationships}, key=order.index
                ):
                    rows = sorted(
                        (
                            names.get(r.source_id, "(hors périmètre)"),
                            verb_of(r),
                            names.get(r.target_id, "(hors périmètre)"),
                        )
                        for r in scope.relationships
                        if r.relationship_type is kind
                    )
                    blocks += [
                        Heading(3, kind.value.replace("_", " ").capitalize()),
                        Table(("Source", "Relation", "Cible"), tuple(rows)),
                    ]
            case DocumentsSection():
                blocks.append(Heading(2, section.title))
                if not documents:
                    blocks.append(Paragraph("Aucun document."))
                for document in documents:
                    blocks += [
                        Heading(3, f"{document.element_name} — {document.filename}"),
                        MarkdownBlock(document.markdown),
                    ]
    return ReportModel(
        title=f"{template.title} — {scope.name}",
        subtitle=(f"Généré le {now:%Y-%m-%d %H:%M} UTC — modèle {template.id} v{template.version}"),
        blocks=tuple(blocks),
    )


# --- File names -------------------------------------------------------------


def slugify(text: str, *, limit: int = 40) -> str:
    """An ASCII slug: accents folded, anything else collapsed to `-`; `sujet` if nothing is left."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:limit].strip("-")
    return slug or "sujet"


def report_filename(
    *, now: datetime, subject_name: str, template_id: str, fmt: str, unique: str = ""
) -> str:
    parts = [f"{now:%Y-%m-%d}", slugify(subject_name), template_id]
    if unique:
        parts.append(unique)
    return f"{'-'.join(parts)}.{fmt}"


def report_key(filename: str, now: datetime) -> str:
    return f"reports/{now:%Y}/{filename}"
