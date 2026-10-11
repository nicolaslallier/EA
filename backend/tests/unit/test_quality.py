"""The quality rules, one series per rule: positive, negative, edge."""

from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.quality import (
    QualityParams,
    RuleCode,
    Severity,
    check_descriptions,
    check_duplicates,
    check_naming,
    check_orphans,
    check_required_properties,
)
from tests.support_analysis import a_link, an_element


class TestOrphans:
    def test_an_element_with_no_link_is_flagged(self) -> None:
        loner = an_element(E.NODE, "Loner")

        findings = check_orphans([loner], [])

        assert [(f.rule, f.severity, f.element_id) for f in findings] == [
            (RuleCode.ORPHAN, Severity.WARNING, loner.id)
        ]

    def test_a_linked_element_is_not_flagged_at_either_end(self) -> None:
        api = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        assert check_orphans([api, process], [a_link(R.SERVING, api, process)]) == []

    @pytest.mark.parametrize("exempt", [E.GROUPING, E.LOCATION, E.PLATEAU])
    def test_containers_are_not_expected_to_be_linked(self, exempt: E) -> None:
        assert check_orphans([an_element(exempt, "Paris")], []) == []

    def test_being_alone_of_its_type_changes_nothing(self) -> None:
        only_one = an_element(E.DATA_OBJECT, "Invoice")

        assert len(check_orphans([only_one], [])) == 1


class TestDescriptions:
    def test_an_empty_description_is_flagged_as_information(self) -> None:
        bare = an_element(E.NODE, "srv-01")

        findings = check_descriptions([bare, an_element(E.NODE, "srv-02", description="Linux")])

        assert [(f.element_id, f.rule, f.severity) for f in findings] == [
            (bare.id, RuleCode.NO_DESCRIPTION, Severity.INFO)
        ]


class TestNaming:
    def test_a_clean_name_passes(self) -> None:
        assert check_naming([an_element(E.APPLICATION_COMPONENT, "Facturation client")]) == []

    def test_a_blank_name_is_flagged(self) -> None:
        blank = replace(an_element(E.NODE, "x"), name="   ")

        assert [f.rule for f in check_naming([blank])] == [RuleCode.NAMING]

    def test_a_double_space_is_flagged(self) -> None:
        assert len(check_naming([an_element(E.NODE, "srv  01")])) == 1

    def test_a_shouting_name_is_flagged_but_a_short_acronym_is_not(self) -> None:
        assert len(check_naming([an_element(E.APPLICATION_COMPONENT, "FACTURATION")])) == 1
        assert check_naming([an_element(E.APPLICATION_COMPONENT, "CRM")]) == []

    def test_a_name_repeating_the_type_is_flagged(self) -> None:
        assert (
            len(check_naming([an_element(E.APPLICATION_COMPONENT, "Application Component")])) == 1
        )

    def test_several_problems_make_one_finding(self) -> None:
        findings = check_naming([an_element(E.NODE, "NODE  ONE")])

        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO


class TestRequiredProperties:
    REQUIRED: ClassVar[dict[E, frozenset[str]]] = {E.APPLICATION_COMPONENT: frozenset({"owner"})}

    def test_a_missing_key_is_flagged_as_a_warning(self) -> None:
        app = an_element(E.APPLICATION_COMPONENT, "Billing")

        findings = check_required_properties([app], self.REQUIRED)

        assert [(f.rule, f.severity) for f in findings] == [
            (RuleCode.MISSING_PROPERTY, Severity.WARNING)
        ]
        assert "owner" in findings[0].message

    def test_an_empty_value_counts_as_missing(self) -> None:
        app = an_element(E.APPLICATION_COMPONENT, "Billing", properties={"owner": "  "})

        assert len(check_required_properties([app], self.REQUIRED)) == 1

    def test_a_filled_key_and_another_type_pass(self) -> None:
        filled = an_element(E.APPLICATION_COMPONENT, "Billing", properties={"owner": "finance"})
        other = an_element(E.NODE, "srv-01")

        assert check_required_properties([filled, other], self.REQUIRED) == []


class TestTheParameters:
    def test_they_are_read_from_settings_shaped_values(self) -> None:
        params = QualityParams.from_settings(0.8, {"application_component": ["owner", "tier"]})

        assert params.similarity == 0.8
        assert params.required_properties == {E.APPLICATION_COMPONENT: frozenset({"owner", "tier"})}

    def test_an_unknown_element_type_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="bogus"):
            QualityParams.from_settings(0.9, {"bogus": ["owner"]})

    def test_a_property_key_that_is_not_an_identifier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1bad"):
            QualityParams.from_settings(0.9, {"node": ["1bad"]})

    def test_a_similarity_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="similarity"):
            QualityParams.from_settings(1.5, {})


class TestDuplicates:
    def pair(self, one: str, other: str, *, type_: E = E.APPLICATION_COMPONENT):  # type: ignore[no-untyped-def]
        return an_element(type_, one), an_element(type_, other)

    def flagged(self, a, b, similarity: float = 0.9):  # type: ignore[no-untyped-def]
        findings = check_duplicates([a, b], similarity)
        assert len(findings) == 1
        assert {findings[0].element_id, findings[0].related_id} == {a.id, b.id}
        assert findings[0].rule is RuleCode.DUPLICATE_PROBABLE
        assert findings[0].severity is Severity.WARNING

    def test_names_equal_after_case_are_flagged(self) -> None:
        self.flagged(*self.pair("Facturation", "facturation"))

    def test_names_equal_after_accents_are_flagged(self) -> None:
        self.flagged(*self.pair("Café", "Cafe"))

    def test_names_equal_after_punctuation_are_flagged(self) -> None:
        self.flagged(*self.pair("Billing API", "Billing-API"))

    def test_very_similar_names_are_flagged_over_the_threshold(self) -> None:
        self.flagged(*self.pair("Facturation client", "Facturation clients"))

    def test_the_threshold_is_a_parameter(self) -> None:
        a, b = self.pair("Facturation client", "Facturation clients")

        assert check_duplicates([a, b], 0.999) == []

    def test_different_names_are_not_flagged(self) -> None:
        a, b = self.pair("Billing", "Backup")

        assert check_duplicates([a, b], 0.9) == []

    def test_the_same_name_under_two_types_is_not_a_duplicate(self) -> None:
        a = an_element(E.APPLICATION_COMPONENT, "Billing")
        b = an_element(E.BUSINESS_PROCESS, "Billing")

        assert check_duplicates([a, b], 0.9) == []

    def test_an_element_alone_of_its_type_has_no_duplicate(self) -> None:
        assert check_duplicates([an_element(E.NODE, "srv-01")], 0.9) == []

    def test_three_copies_make_two_findings_and_never_flag_the_first(self) -> None:
        copies = [an_element(E.NODE, name) for name in ("srv-01", "SRV-01", "srv 01")]

        findings = check_duplicates(copies, 0.9)

        assert len(findings) == 2
        first = min(copies, key=lambda e: (e.name, str(e.id)))
        assert first.id not in {f.element_id for f in findings}
