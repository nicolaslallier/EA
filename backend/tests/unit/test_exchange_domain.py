"""The vocabulary of an exchange: names, identifiers, and what an element becomes."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ea.api.schemas import ElementCreate
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import RowRefusedError
from ea.domain.exchange import (
    ACCESS_BY_NAME,
    ACCESS_NAMES,
    DESCRIPTION_KEY,
    JUNCTION_NAMES,
    MAX_DESCRIPTION_LENGTH,
    MAX_DOCUMENTATION_LENGTH,
    MAX_NAME_LENGTH,
    SOURCE_ID,
    Category,
    Counts,
    ExchangeElement,
    ExchangeFormat,
    ExchangeRelationship,
    ExportScope,
    Issue,
    Outcome,
    ReportBuilder,
    Severity,
    accept_element,
    accept_link,
    digest_of,
    element_type_of,
    exchange_element_of,
    exchange_name,
    exchange_relationship_of,
    identifier_of,
    pascal_case,
    relationship_type_of,
    validate_element,
    validate_link,
    xml_identifier,
)
from ea.domain.model import Element, Relationship, is_property_key

NOW = datetime(2026, 10, 10, tzinfo=UTC)
MODELLED = [t for t in ElementType if not t.is_connector]


def test_names_are_the_pascal_case_of_the_value() -> None:
    assert exchange_name(ElementType.BUSINESS_ACTOR) == "BusinessActor"
    assert exchange_name(ElementType.COURSE_OF_ACTION) == "CourseOfAction"
    assert exchange_name(RelationshipType.SPECIALIZATION) == "Specialization"
    assert pascal_case("read_write") == "ReadWrite"


@pytest.mark.parametrize("element_type", MODELLED, ids=lambda t: t.value)
def test_every_modelled_element_type_survives_the_round_trip(element_type: ElementType) -> None:
    assert element_type_of(exchange_name(element_type)) is element_type


@pytest.mark.parametrize("relationship", list(RelationshipType), ids=lambda t: t.value)
def test_every_relationship_type_survives_the_round_trip(relationship: RelationshipType) -> None:
    assert relationship_type_of(exchange_name(relationship)) is relationship


def test_two_types_never_share_an_exchange_name() -> None:
    names = [exchange_name(t) for t in MODELLED]

    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[A-Z][A-Za-z]+", name) for name in names)


def test_the_only_connector_is_the_junction_and_it_is_not_a_modelled_type() -> None:
    connectors = [t for t in ElementType if t.is_connector]

    assert connectors == [ElementType.JUNCTION]
    assert element_type_of("AndJunction") is None
    assert frozenset({"AndJunction", "OrJunction"}) == JUNCTION_NAMES


def test_unknown_names_are_none() -> None:
    assert element_type_of("Spaceship") is None
    assert relationship_type_of("Telepathy") is None


def test_access_names_cover_every_access_type_both_ways() -> None:
    assert ACCESS_NAMES[AccessType.READ_WRITE] == "ReadWrite"
    assert {ACCESS_BY_NAME[name] for name in ACCESS_NAMES.values()} == set(AccessType)


def test_a_valid_identifier_is_kept_and_an_invalid_one_is_normalised() -> None:
    assert xml_identifier("id-123") == "id-123"
    assert xml_identifier("1") == "id-1"
    assert xml_identifier("a b/c") == "id-a_b_c"
    assert xml_identifier(xml_identifier("1")) == "id-1"  # idempotent


def test_an_element_without_a_source_id_is_named_after_its_uuid() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")

    assert identifier_of(None, uid) == f"id-{uid}"
    assert identifier_of("src-9", uid) == "src-9"


def test_the_exchange_form_of_an_element_drops_source_id_and_keeps_the_rest() -> None:
    element = Element.create(
        element_type=ElementType.APPLICATION_COMPONENT,
        name="Billing",
        description="Invoices",
        documentation="Long text",
        properties={SOURCE_ID: "app-1", "owner": "finance"},
        now=NOW,
    )

    item = exchange_element_of(element)

    assert item.identifier == "app-1"
    assert dict(item.properties) == {"owner": "finance"}
    assert (item.description, item.documentation) == ("Invoices", "Long text")
    assert DESCRIPTION_KEY == "ea.description"


def test_the_exchange_form_of_a_relationship_uses_the_ends_identifiers() -> None:
    a = Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW)
    b = Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="B", now=NOW)
    link = Relationship.between(
        RelationshipType.FLOW, a, b, now=NOW, name="sends", properties={SOURCE_ID: "r-1"}
    )

    item = exchange_relationship_of(link, source="a-1", target="b-1")

    assert (item.identifier, item.source, item.target) == ("r-1", "a-1", "b-1")
    assert dict(item.properties) == {}
    assert item.relationship_type is RelationshipType.FLOW


def test_the_scope_label_names_the_kind() -> None:
    assert ExportScope().label == "model"


def test_a_property_key_is_a_plain_identifier() -> None:
    assert is_property_key("owner_name")
    assert not is_property_key("ea.description")
    assert not is_property_key("Owner name")
    assert not is_property_key("")


def test_the_limits_are_the_ones_the_http_schema_declares() -> None:
    schema = ElementCreate.model_json_schema()["properties"]

    assert schema["name"]["maxLength"] == MAX_NAME_LENGTH
    assert schema["description"]["maxLength"] == MAX_DESCRIPTION_LENGTH
    assert schema["documentation"]["maxLength"] == MAX_DOCUMENTATION_LENGTH


def an_item(**fields: object) -> ExchangeElement:
    return ExchangeElement("a", ElementType.NODE, **{"name": "ok", **fields})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"name": "  "}, "blank_name"),
        ({"name": "x" * (MAX_NAME_LENGTH + 1)}, "name_too_long"),
        ({"description": "x" * (MAX_DESCRIPTION_LENGTH + 1)}, "description_too_long"),
        ({"documentation": "x" * (MAX_DOCUMENTATION_LENGTH + 1)}, "documentation_too_long"),
        ({"name": "a\x00b"}, "nul_character"),
        ({"documentation": "a\x00b"}, "nul_character"),
        ({"properties": {"k": "a\x00b"}}, "nul_character"),
    ],
)
def test_a_row_outside_the_bounds_is_refused_with_its_code(
    fields: dict[str, object], code: str
) -> None:
    with pytest.raises(RowRefusedError) as raised:
        validate_element(an_item(**fields))

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("bound", "fields", "code"),
    [
        ("MAX_NAME_LENGTH", {"name": "123456"}, "name_too_long"),
        ("MAX_DESCRIPTION_LENGTH", {"description": "123456"}, "description_too_long"),
        ("MAX_DOCUMENTATION_LENGTH", {"documentation": "123456"}, "documentation_too_long"),
        ("MAX_PROPERTY_VALUE_LENGTH", {"properties": {"k": "123456"}}, "property_value_too_long"),
    ],
)
def test_each_length_bound_refuses_one_unit_over_it_and_accepts_the_bound_itself(
    monkeypatch: pytest.MonkeyPatch, bound: str, fields: dict[str, object], code: str
) -> None:
    monkeypatch.setattr(f"ea.domain.exchange.{bound}", 5)
    (field,) = fields

    with pytest.raises(RowRefusedError) as raised:
        validate_element(an_item(**fields))
    assert raised.value.code == code

    at_the_bound = fields[field]
    at_the_bound = {"k": "12345"} if isinstance(at_the_bound, dict) else "12345"
    validate_element(an_item(**{field: at_the_bound}))


def test_a_link_is_refused_by_the_matrix_before_any_write() -> None:
    link = ExchangeRelationship("r", RelationshipType.SPECIALIZATION, "a", "b")

    with pytest.raises(RowRefusedError) as raised:
        validate_link(link, ElementType.BUSINESS_ACTOR, ElementType.NODE, same_element=False)
    assert raised.value.code == "relationship_not_allowed"

    validate_link(link, ElementType.NODE, ElementType.NODE, same_element=False)
    with pytest.raises(RowRefusedError):  # an element cannot specialise itself
        validate_link(link, ElementType.NODE, ElementType.NODE, same_element=True)


def test_a_link_name_is_bounded_like_an_element_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ea.domain.exchange.MAX_NAME_LENGTH", 5)
    link = ExchangeRelationship("r", RelationshipType.FLOW, "a", "b", name="123456")

    with pytest.raises(RowRefusedError) as raised:
        validate_link(link, ElementType.NODE, ElementType.NODE, same_element=False)

    assert raised.value.code == "name_too_long"


def test_a_single_file_is_digested_like_shasum_and_several_are_length_prefixed() -> None:
    import hashlib

    assert digest_of(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert digest_of(b"a", b"bc") != digest_of(b"ab", b"c")


def test_the_report_counts_and_truncates_but_keeps_the_counts_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ea.domain.exchange.MAX_REPORT_ISSUES", 2)
    builder = ReportBuilder()
    for _ in range(5):
        builder.count(Category.ELEMENTS, Outcome.REJECTED)
        builder.add(Issue(Severity.ERROR, "x", "m"))
    builder.add(Issue(Severity.ERROR, "strict_refused", "m"), always=True)

    report = builder.build(
        dry_run=True, applied=False, format=ExchangeFormat.ARCHIMATE_XML, sha256="0"
    )

    assert report.counts[Category.ELEMENTS] == Counts(rejected=5)
    assert len(report.issues) == 3 and report.truncated
    assert report.issues[-1].code == "strict_refused"
    assert report.issue_count == 6  # exact, whatever was kept
    assert builder.has_rejections()


def test_the_report_keeps_its_first_lines_per_category(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ea.domain.exchange.MAX_REPORT_ISSUES", 2)
    builder = ReportBuilder()
    for _ in range(4):
        builder.add(Issue(Severity.WARNING, "e", "m"), Category.ELEMENTS)
    builder.add(Issue(Severity.ERROR, "r", "m"), Category.RELATIONSHIPS)

    report = builder.build(
        dry_run=True, applied=False, format=ExchangeFormat.ARCHIMATE_XML, sha256="0"
    )

    assert [i.code for i in report.issues] == ["e", "e", "r"]  # a flood of one kind hides no other


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"name": "a\nb"}, "control_character"),
        ({"name": "a\x07b"}, "control_character"),
        ({"properties": {"a\n": "v"}}, "invalid_property_key"),
        ({"properties": {"Owner name": "v"}}, "invalid_property_key"),
        ({"properties": {SOURCE_ID: "v"}}, "reserved_property_key"),
        ({"properties": {"ea.thing": "v"}}, "invalid_property_key"),
        ({"identifier": "x" * 5_000}, "identifier_too_long"),
        ({"properties": {"cidr": "10.0.0.0/99"}}, "invalid_ipam_property"),
        ({"properties": {"ip_address": "10.0.0.300"}}, "invalid_ipam_property"),
    ],
)
def test_accepting_an_element_refuses_what_the_domain_refuses_with_a_stable_code(
    fields: dict[str, object], code: str
) -> None:
    item = replace(an_item(), **fields)  # type: ignore[arg-type]

    with pytest.raises(RowRefusedError) as raised:
        accept_element(item)

    assert raised.value.code == code


def test_accepting_an_element_hands_back_a_clean_copy_with_the_canonical_address() -> None:
    item = an_item(
        name="  srv  ",
        description=" d ",
        properties={"ip_address": "10.000.0.5".replace("000", "0")},
    )

    accepted = accept_element(item)

    assert (accepted.name, accepted.description) == ("srv", "d")
    assert accepted.properties["ip_address"] == "10.0.0.5"
    assert type(accepted.properties) is not dict  # read-only: nobody edits it on the way


def test_the_number_of_properties_is_bounded_in_the_domain_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ea.domain.exchange.MAX_PROPERTIES_PER_OBJECT", 2)

    accept_element(an_item(properties={"a": "1", "b": "2"}))
    with pytest.raises(RowRefusedError) as raised:
        accept_element(an_item(properties={"a": "1", "b": "2", "c": "3"}))

    assert raised.value.code == "too_many_properties"


def test_accepting_a_link_refuses_a_long_identifier_and_keeps_the_matrix() -> None:
    link = ExchangeRelationship("r" * 5_000, RelationshipType.FLOW, "a", "b")
    with pytest.raises(RowRefusedError) as raised:
        accept_link(link, ElementType.NODE, ElementType.NODE, same_element=False)
    assert raised.value.code == "identifier_too_long"

    ok = ExchangeRelationship("r", RelationshipType.FLOW, "a", "b", name=" n ")
    assert accept_link(ok, ElementType.NODE, ElementType.NODE, same_element=False).name == "n"
