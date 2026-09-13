"""The `alimenter-catalogue` flow: source files in S3 become elements in EA.

Each file under the prefix is read, handed to the LLM with the ArchiMate types
`GET /metamodel` allows right now, and what comes back is written through
`EaClient` — idempotently, so a second run over the same files creates
nothing. What the API refuses is recorded, not raised; only a file that could
not be extracted at all fails the run. Every outcome lands in one table
artifact, so a run is read in the Prefect UI rather than in its logs.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

import httpx
from minio import Minio
from minio.error import S3Error
from prefect import flow, get_run_logger, task
from prefect.artifacts import create_table_artifact
from prefect.cache_policies import NONE
from pydantic import BaseModel, ConfigDict

from pipelines.ea import EaClient, EaRefused, Metamodel, Written, ea_client
from pipelines.llm import ExtractionFailed, extract, llm_client
from pipelines.settings import Settings
from pipelines.storage import list_keys, read_text, s3_client

_RETRY_DELAYS: list[float] = [2, 10]


class ExtractedElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_type: str
    name: str
    description: str


class ExtractedRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_type: str
    source_name: str
    source_type: str
    target_name: str
    target_type: str


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elements: list[ExtractedElement]
    relationships: list[ExtractedRelationship]


@dataclass(frozen=True)
class Refused:
    """A write the catalogue declined; recorded in the artifact, never raised."""

    key: str
    subject: str
    code: str
    detail: str


class _Run(NamedTuple):
    settings: Settings
    ea: EaClient
    llm: httpx.Client
    s3: Minio


#: The clients of the flow run in progress — built once per run, read by its tasks.
_run: ContextVar[_Run] = ContextVar("alimenter_catalogue_run")

_PROMPT = """\
Voici un document d'architecture d'entreprise, le fichier « {key} ».

Relève les éléments ArchiMate 3.2 que ce document décrit : pour chacun, son type, son nom \
exactement tel qu'il est écrit dans le document, et une description courte tirée du texte \
(vide si le document n'en dit rien).

Relève ensuite les relations ArchiMate entre ces éléments : pour chacune, son type, puis le \
nom et le type de sa source et de sa cible, qui doivent être des éléments relevés ci-dessus.

N'invente rien que le document ne dise pas.

--- Début du document ---
{text}
--- Fin du document ---"""


def clients() -> tuple[EaClient, httpx.Client, Minio]:
    """The EA, LLM and S3 clients, from `Settings` — the one seam tests replace."""
    settings = Settings()  # type: ignore[call-arg]  # the secrets come from the environment
    return ea_client(settings), llm_client(settings), s3_client(settings)


def _element_subject(el: ExtractedElement) -> str:
    return f"{el.element_type} {el.name}"


def _relationship_subject(rel: ExtractedRelationship) -> str:
    return f"{rel.source_name} {rel.relationship_type} {rel.target_name}"


@task(retries=2, retry_delay_seconds=_RETRY_DELAYS)
def read_metamodel() -> Metamodel:
    return _run.get().ea.metamodel()


@task
def extract_architecture(key: str, metamodel: Metamodel) -> Extraction:
    """Read one file and ask the LLM for its elements and relationships.

    A file that is not UTF-8, or longer than `max_source_chars`, raises
    `ExtractionFailed` without reaching the LLM: truncating would extract an
    architecture from half a document and record it as the whole.
    """
    run = _run.get()
    try:
        text = read_text(run.s3, run.settings.s3_bucket, key)
    except UnicodeDecodeError as error:
        raise ExtractionFailed(f"{key} is not UTF-8 text") from error
    if len(text) > run.settings.max_source_chars:
        msg = f"{key} holds {len(text)} characters, over {run.settings.max_source_chars}"
        raise ExtractionFailed(msg)
    return extract(
        run.llm,
        _PROMPT.format(key=key, text=text),
        Extraction,
        model="smart",
        enums={
            "element_type": metamodel.element_types,
            "source_type": metamodel.element_types,
            "target_type": metamodel.element_types,
            "relationship_type": metamodel.relationship_types,
        },
    )


# ponytail: duplicates are caught on the exact name only — if the LLM keeps
# writing variants ("Billing" / "Billing app"), give the prompt the names the
# catalogue already holds.
@task(retries=2, retry_delay_seconds=_RETRY_DELAYS, cache_policy=NONE)
def write_element(el: ExtractedElement, source: str) -> Written | Refused:
    try:
        return _run.get().ea.ensure_element(el.element_type, el.name, el.description, source)
    except EaRefused as refusal:
        return Refused(source, _element_subject(el), refusal.code, refusal.detail)


@task(retries=2, retry_delay_seconds=_RETRY_DELAYS, cache_policy=NONE)
def write_relationship(
    rel: ExtractedRelationship, ids: Mapping[tuple[str, str], UUID], source: str
) -> str | Refused:
    subject = _relationship_subject(rel)
    source_id = ids.get((rel.source_name, rel.source_type))
    target_id = ids.get((rel.target_name, rel.target_type))
    if source_id is None or target_id is None:
        return Refused(source, subject, "unknown_endpoint", "an endpoint is not in this file")
    try:
        return _run.get().ea.ensure_relationship(rel.relationship_type, source_id, target_id)
    except EaRefused as refusal:
        return Refused(source, subject, refusal.code, refusal.detail)


@contextmanager
def _clients_for_this_run() -> Iterator[_Run]:
    ea, llm, s3 = clients()
    token = _run.set(_Run(Settings(), ea, llm, s3))  # type: ignore[call-arg]
    try:
        with ea.http, llm:
            yield _run.get()
    finally:
        _run.reset(token)


def _row(key: str, subject: str, outcome: str, detail: str = "") -> dict[str, str]:
    return {"key": key, "subject": subject, "outcome": outcome, "detail": detail}


@flow(name="alimenter-catalogue")
def alimenter_catalogue(prefix: str = "inbox/") -> None:
    """Feed the EA catalogue from every file under `prefix`."""
    logger = get_run_logger()
    rows: list[dict[str, str]] = []
    failed: list[str] = []
    with _clients_for_this_run() as run:
        metamodel = read_metamodel()
        # ponytail: files in series — ThreadPoolTaskRunner when volume demands it
        for key in list_keys(run.s3, run.settings.s3_bucket, prefix):
            source = f"s3://{run.settings.s3_bucket}/{key}"
            try:
                extraction = extract_architecture(key, metamodel)
            except (ExtractionFailed, httpx.HTTPError, S3Error) as error:
                logger.error("extraction of %s failed: %s", key, error)
                failed.append(key)
                rows.append(_row(key, "", "extraction_failed", str(error)))
                continue
            ids: dict[tuple[str, str], UUID] = {}
            for element in extraction.elements:
                written = write_element(element, source)
                if isinstance(written, Refused):
                    rows.append(_row(key, written.subject, written.code, written.detail))
                else:
                    ids[element.name, element.element_type] = written.id
                    rows.append(_row(key, _element_subject(element), written.outcome))
            for relationship in extraction.relationships:
                linked = write_relationship(relationship, ids, source)
                if isinstance(linked, Refused):
                    rows.append(_row(key, linked.subject, linked.code, linked.detail))
                else:
                    rows.append(_row(key, _relationship_subject(relationship), linked))
    create_table_artifact(
        rows, key="alimenter-catalogue", description=f"Alimentation depuis `{prefix}`"
    )
    if failed:
        raise RuntimeError(f"{len(failed)} file(s) could not be extracted: {', '.join(failed)}")
