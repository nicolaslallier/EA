"""An application assembled without a service fails loudly, never with an empty answer.

Each `*_service_of` documents the same promise: a missing service is a wiring
fault and a 500 — a client must not read an empty list as "nothing there".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI

from ea.api import dependencies

PROVIDERS = [
    dependencies.package_service_of,
    dependencies.ipam_service_of,
    dependencies.audit_service_of,
    dependencies.baseline_service_of,
    dependencies.file_service_of,
    dependencies.saved_query_service_of,
    dependencies.document_service_of,
    dependencies.diagram_service_of,
    dependencies.profile_service_of,
    dependencies.template_service_of,
    dependencies.exchange_service_of,
    dependencies.session_factory_of,
    dependencies.analysis_service_of,
    dependencies.external_service_of,
    dependencies.timeline_service_of,
    dependencies.report_service_of,
    dependencies.requirement_service_of,
]


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.__name__)
def test_a_missing_service_is_a_runtime_error_naming_what_to_check(
    provider: Callable[[FastAPI], Any],
) -> None:
    with pytest.raises(RuntimeError, match=r"no .* on the application"):
        provider(FastAPI())


def test_a_service_that_is_there_is_handed_back() -> None:
    app = FastAPI()
    app.state.audit_service = object()

    assert dependencies.audit_service_of(app) is app.state.audit_service
