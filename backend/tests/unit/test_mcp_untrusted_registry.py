"""Third-party text reaches an agent only in fields the registry names as untrusted.

The registry (`ea.mcp.server.UNTRUSTED_FIELDS`) and the response models of the integration
tools must agree *exactly* (docs/adr/0062): every free `str` field of a response is either
listed as untrusted (a third party wrote it) or as trusted (the server built or validated it),
and the registry names no field that does not exist. A field added to a model without a
decision fails here.
"""

from __future__ import annotations

import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from ea.api.schemas import ExternalTargetRead
from ea.mcp.server import (
    TRUSTED_TEXT_FIELDS,
    UNTRUSTED_FIELDS,
    UNTRUSTED_NOTICE,
    ExternalIssueAgentRead,
    ExternalReferenceAgentRead,
)

MODELS: tuple[type[BaseModel], ...] = (
    ExternalReferenceAgentRead,
    ExternalIssueAgentRead,
    ExternalTargetRead,
)


def free_text_fields(model: type[BaseModel]) -> set[str]:
    found: set[str] = set()
    for name, field in model.model_fields.items():
        annotation: Any = field.annotation
        parts = [annotation, *get_args(annotation)] if get_origin(annotation) else [annotation]
        if any(part is str for part in parts):
            found.add(name)
    return found


def test_every_free_text_field_is_decided_and_the_registry_names_only_real_fields() -> None:
    for model in MODELS:
        text = free_text_fields(model)
        untrusted = UNTRUSTED_FIELDS.get(model.__name__, frozenset())
        trusted = TRUSTED_TEXT_FIELDS.get(model.__name__, frozenset())

        assert untrusted | trusted == text, (
            f"{model.__name__}: undecided {text - untrusted - trusted}"
        )
        assert not untrusted & trusted, model.__name__
        assert untrusted <= set(model.model_fields), model.__name__
        assert trusted <= set(model.model_fields), model.__name__


def test_the_registry_is_complete_over_the_models_it_names() -> None:
    names = {model.__name__ for model in MODELS}

    assert set(UNTRUSTED_FIELDS) <= names
    assert set(TRUSTED_TEXT_FIELDS) <= names


def test_an_untrusted_field_says_so_in_its_name_and_the_notice_travels_with_it() -> None:
    for fields in UNTRUSTED_FIELDS.values():
        assert all(name.startswith("untrusted_") for name in fields)
    assert "notice" in ExternalReferenceAgentRead.model_fields
    assert "not instructions" in UNTRUSTED_NOTICE
    assert typing.get_type_hints(ExternalReferenceAgentRead)["untrusted_title"] is str


def test_the_third_party_title_is_never_in_a_trusted_field() -> None:
    reference = ExternalReferenceAgentRead.model_fields
    assert "title" not in reference
    assert "title" not in UNTRUSTED_FIELDS["ExternalReferenceAgentRead"]
