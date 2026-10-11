"""`rafraichir-references`: keep the cache of the external references fresh.

A second flow beside `alimenter-catalogue`, with the same rules (docs/adr/0028):

* **It writes through the EA REST API only** — never `/mcp`, never a repository. It does not
  even hold a token for GitHub, Jira or ServiceNow: it asks the API to refresh a reference, and
  the API (`POST /external-references/{id}/refresh`, an editor's call) asks the provider.
* **Each step is its own `@task`**, retried for the network between the worker and the Mac. A
  refresh is safe to repeat — it re-reads a provider and overwrites a cache — which is why it
  may be retried, unlike opening a ticket, which this flow never does.
* **A refusal is a line of the table artifact, not an exception.** And the artifact carries
  ids and outcomes only: the cached title is a third party's text and goes nowhere.
* **The run is bounded**: at most `MAX_PER_RUN` references, the API's own page maximum.
"""

from __future__ import annotations

from contextlib import closing
from contextvars import ContextVar

from prefect import flow, get_run_logger, task
from prefect.artifacts import create_table_artifact
from prefect.cache_policies import NONE

from pipelines.ea import EaClient, EaRefused, StaleReference, ea_client
from pipelines.settings import Settings

#: `api/schemas.py`: `StaleLimit` is capped at `MAX_STALE_PAGE` (100) — read from the contract.
MAX_PER_RUN = 100
_RETRY_DELAYS: list[float] = [2, 10]

_run: ContextVar[EaClient] = ContextVar("rafraichir_references_run")


def ea() -> EaClient:
    """The EA client, from `Settings` — the one seam tests replace."""
    return ea_client(Settings())  # type: ignore[call-arg]  # the secrets come from the environment


@task(retries=2, retry_delay_seconds=_RETRY_DELAYS, cache_policy=NONE)
def list_stale(limit: int) -> list[StaleReference]:
    return _run.get().stale_external_references(limit)


@task(retries=2, retry_delay_seconds=_RETRY_DELAYS, cache_policy=NONE)
def refresh_one(reference: StaleReference) -> str:
    try:
        fresh = _run.get().refresh_external_reference(reference.id)
    except EaRefused as refusal:
        return refusal.code
    return "refreshed" if fresh else "still_stale"


@flow(name="rafraichir-references")
def rafraichir_references(limit: int = MAX_PER_RUN) -> None:
    """Refresh up to `limit` references whose cache is more than a day old."""
    if not 1 <= limit <= MAX_PER_RUN:
        msg = f"limit is between 1 and {MAX_PER_RUN}"
        raise ValueError(msg)
    logger = get_run_logger()
    rows: list[dict[str, str]] = []
    try:
        with closing(ea()) as client:
            token = _run.set(client)
            try:
                for reference in list_stale(limit):
                    outcome = refresh_one(reference)
                    rows.append(
                        {
                            "reference": str(reference.id),
                            "target": reference.target,
                            "kind": reference.kind,
                            "outcome": outcome,
                        }
                    )
            finally:
                _run.reset(token)
    finally:
        logger.info("refreshed %d reference(s)", len(rows))
        create_table_artifact(
            rows,
            key="rafraichir-references",
            description="Rafraîchissement des références externes périmées",
        )
