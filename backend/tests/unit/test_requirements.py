"""The coverage of requirements, as a pure reading of a graph."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.domain.requirements import (
    RESULT_PROPERTY,
    UNSET,
    Gap,
    compute_coverage,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(element_type: E, name: str, **properties: str) -> Element:
    return Element.create(element_type=element_type, name=name, now=NOW, properties=properties)


def a_requirement(name: str = "R1", **properties: str) -> Element:
    return an_element(E.REQUIREMENT, name, **properties)


def a_design(name: str = "Billing") -> Element:
    return an_element(E.APPLICATION_COMPONENT, name)


def a_test(name: str = "T1", result: str = "passed") -> Element:
    return an_element(E.DELIVERABLE, name, **{RESULT_PROPERTY: result})


def realizes(source: Element, target: Element) -> Relationship:
    return Relationship.between(R.REALIZATION, source, target, now=NOW)


def gaps(coverage: object) -> dict[str, Gap]:
    return {trace.requirement.name: trace.gap for trace in coverage.traces}  # type: ignore[attr-defined]


class TestGaps:
    def test_a_requirement_nothing_realizes_is_a_design_gap(self) -> None:
        requirement = a_requirement()

        coverage = compute_coverage([requirement], [])

        assert gaps(coverage) == {"R1": Gap.DESIGN}
        assert coverage.design_rate == 0.0

    def test_a_designed_requirement_without_a_test_is_a_verification_gap(self) -> None:
        requirement, design = a_requirement(), a_design()

        coverage = compute_coverage([requirement, design], [realizes(design, requirement)])

        (trace,) = coverage.traces
        assert trace.gap is Gap.VERIFICATION
        assert [element.name for element in trace.designed_by] == ["Billing"]

    def test_a_designed_and_tested_requirement_has_no_gap(self) -> None:
        requirement, design, test = a_requirement(), a_design(), a_test()

        coverage = compute_coverage(
            [requirement, design, test],
            [realizes(design, requirement), realizes(test, requirement)],
        )

        (trace,) = coverage.traces
        assert trace.gap is Gap.NONE
        assert [element.name for element in trace.tested_by] == ["T1"]
        assert coverage.design_rate == 1.0
        assert coverage.verification_rate == 1.0

    def test_a_deliverable_without_a_result_is_design_not_a_test(self) -> None:
        requirement = a_requirement()
        deliverable = an_element(E.DELIVERABLE, "Spec technique")

        coverage = compute_coverage(
            [requirement, deliverable], [realizes(deliverable, requirement)]
        )

        (trace,) = coverage.traces
        assert [element.name for element in trace.designed_by] == ["Spec technique"]
        assert trace.tested_by == ()
        assert trace.gap is Gap.VERIFICATION

    def test_a_realization_from_a_goal_side_element_is_neither_design_nor_test(self) -> None:
        requirement = a_requirement()
        stakeholder = an_element(E.STAKEHOLDER, "DSI")

        coverage = compute_coverage(
            [requirement, stakeholder], [realizes(stakeholder, requirement)]
        )

        assert gaps(coverage) == {"R1": Gap.DESIGN}

    def test_design_and_tests_are_found_through_a_chain_of_requirements(self) -> None:
        parent, child = a_requirement("Parent"), a_requirement("Child")
        design, test = a_design(), a_test()

        coverage = compute_coverage(
            [parent, child, design, test],
            [realizes(child, parent), realizes(design, child), realizes(test, child)],
        )

        assert gaps(coverage) == {"Child": Gap.NONE, "Parent": Gap.NONE}


class TestLoops:
    def test_a_realization_loop_terminates_and_stays_a_gap(self) -> None:
        a, b = a_requirement("A"), a_requirement("B")

        coverage = compute_coverage([a, b], [realizes(a, b), realizes(b, a)])

        assert gaps(coverage) == {"A": Gap.DESIGN, "B": Gap.DESIGN}

    def test_a_design_reaching_a_loop_covers_every_requirement_on_it(self) -> None:
        a, b, design = a_requirement("A"), a_requirement("B"), a_design()

        coverage = compute_coverage(
            [a, b, design], [realizes(a, b), realizes(b, a), realizes(design, b)]
        )

        assert gaps(coverage) == {"A": Gap.VERIFICATION, "B": Gap.VERIFICATION}


class TestStatus:
    def test_a_requirement_without_a_usable_status_is_counted_as_unset(self) -> None:
        coverage = compute_coverage(
            [
                a_requirement("None"),
                a_requirement("Garbled", req_status="banana"),
                a_requirement("Fine", req_status="approved"),
            ],
            [],
        )

        assert coverage.by_status[UNSET] == 2
        assert coverage.by_status["approved"] == 1
        assert sum(coverage.by_status.values()) == coverage.total == 3

    def test_every_status_is_listed_even_when_empty(self) -> None:
        coverage = compute_coverage([a_requirement()], [])

        assert set(coverage.by_status) == {
            "proposed",
            "approved",
            "implemented",
            "verified",
            "rejected",
            UNSET,
        }

    def test_a_rejected_requirement_is_in_no_rate_and_no_gap(self) -> None:
        rejected = a_requirement("Old", req_status="rejected")
        live, design = a_requirement("Live"), a_design()

        coverage = compute_coverage([rejected, live, design], [realizes(design, live)])

        assert coverage.total == 2
        assert coverage.active == 1
        assert coverage.design_rate == 1.0
        assert gaps(coverage)["Old"] is Gap.NONE

    def test_the_catalogue_can_be_narrowed_by_status_and_priority(self) -> None:
        approved = a_requirement("A", req_status="approved", req_priority="must")
        proposed = a_requirement("B", req_status="proposed", req_priority="could")

        by_status = compute_coverage([approved, proposed], [], status="approved")
        by_priority = compute_coverage([approved, proposed], [], priority="could")
        unset = compute_coverage([approved, a_requirement("C")], [], status=UNSET)

        assert [t.requirement.name for t in by_status.traces] == ["A"]
        assert [t.requirement.name for t in by_priority.traces] == ["B"]
        assert [t.requirement.name for t in unset.traces] == ["C"]

    def test_an_unknown_filter_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown status: expected"):
            compute_coverage([], [], status="banana")
        with pytest.raises(ValueError, match="unknown priority: expected"):
            compute_coverage([], [], priority="urgent")


class TestEmptyAndOrder:
    def test_an_empty_graph_has_no_rate(self) -> None:
        coverage = compute_coverage([], [])

        assert coverage.total == 0
        assert coverage.active == 0
        assert coverage.design_rate is None
        assert coverage.verification_rate is None
        assert coverage.traces == ()
        assert set(coverage.by_status.values()) == {0}

    def test_only_rejected_requirements_have_no_rate_either(self) -> None:
        coverage = compute_coverage([a_requirement(req_status="rejected")], [])

        assert coverage.active == 0
        assert coverage.design_rate is None

    def test_the_most_important_requirements_come_first(self) -> None:
        coverage = compute_coverage(
            [
                a_requirement("Z-must", req_priority="must"),
                a_requirement("A-could", req_priority="could"),
                a_requirement("M-none"),
                a_requirement("B-should", req_priority="should"),
            ],
            [],
        )

        assert [t.requirement.name for t in coverage.traces] == [
            "Z-must",
            "B-should",
            "A-could",
            "M-none",
        ]

    def test_a_constraint_is_a_requirement_too(self) -> None:
        constraint = an_element(E.CONSTRAINT, "Hébergement en France")

        coverage = compute_coverage([constraint], [])

        assert coverage.total == 1
        assert coverage.traces[0].identifier == ""


class TestOnlyAPassingTestVerifies:
    @pytest.mark.parametrize(
        ("result", "state", "gap"),
        [
            ("passed", "verified", Gap.NONE),
            ("failed", "failing", Gap.VERIFICATION),
            ("not_run", "unverified", Gap.VERIFICATION),
            ("blocked", "unverified", Gap.VERIFICATION),
            ("banana", "unverified", Gap.VERIFICATION),
        ],
    )
    def test_each_result_is_judged_by_itself(self, result: str, state: str, gap: Gap) -> None:
        requirement, design, test = a_requirement(), a_design(), a_test(result=result)

        coverage = compute_coverage(
            [requirement, design, test],
            [realizes(design, requirement), realizes(test, requirement)],
        )

        (trace,) = coverage.traces
        assert (trace.verification.value, trace.gap) == (state, gap)
        assert [coverage.verified, coverage.failing, coverage.unverified] == [
            int(state == "verified"),
            int(state == "failing"),
            int(state == "unverified"),
        ]
        assert coverage.verification_rate == (1.0 if state == "verified" else 0.0)

    def test_one_red_test_among_green_ones_makes_it_failing(self) -> None:
        requirement, design = a_requirement(), a_design()
        green, red = a_test("T1", "passed"), a_test("T2", "failed")

        coverage = compute_coverage(
            [requirement, design, green, red],
            [
                realizes(design, requirement),
                realizes(green, requirement),
                realizes(red, requirement),
            ],
        )

        assert coverage.failing == 1 and coverage.verified == 0

    def test_the_three_counts_always_add_up_to_the_active_requirements(self) -> None:
        coverage = compute_coverage(
            [a_requirement("A"), a_requirement("B", req_status="rejected")], []
        )

        assert coverage.verified + coverage.failing + coverage.unverified == coverage.active == 1


class TestJunctions:
    def junction(self, name: str = "j") -> Element:
        return an_element(E.JUNCTION, name)

    def test_a_junction_alone_is_not_a_design(self) -> None:
        requirement, gate = a_requirement(), self.junction()

        coverage = compute_coverage([requirement, gate], [realizes(gate, requirement)])

        (trace,) = coverage.traces
        assert trace.designed_by == () and trace.gap is Gap.DESIGN

    def test_the_design_behind_a_junction_is_the_design_and_the_junction_is_not(self) -> None:
        requirement, gate, design = a_requirement(), self.junction(), a_design()

        coverage = compute_coverage(
            [requirement, gate, design], [realizes(gate, requirement), realizes(design, gate)]
        )

        (trace,) = coverage.traces
        assert [e.name for e in trace.designed_by] == ["Billing"]

    def test_a_chain_of_junctions_is_crossed(self) -> None:
        requirement, one, two = a_requirement(), self.junction("j1"), self.junction("j2")
        design, test = a_design(), a_test()

        coverage = compute_coverage(
            [requirement, one, two, design, test],
            [
                realizes(one, requirement),
                realizes(two, one),
                realizes(design, two),
                realizes(test, two),
            ],
        )

        (trace,) = coverage.traces
        assert [e.name for e in trace.designed_by] == ["Billing"]
        assert [e.name for e in trace.tested_by] == ["T1"]

    def test_a_cycle_of_junctions_terminates_and_stays_a_gap(self) -> None:
        requirement, one, two = a_requirement(), self.junction("j1"), self.junction("j2")

        coverage = compute_coverage(
            [requirement, one, two],
            [realizes(one, requirement), realizes(two, one), realizes(one, two)],
        )

        assert gaps(coverage) == {"R1": Gap.DESIGN}


class TestOneDefinitionOfTheVocabulary:
    @pytest.mark.parametrize("written", ["Passed", " passed ", "PASSED"])
    def test_a_spelling_the_write_canonicalises_is_read_the_same_way(self, written: str) -> None:
        from ea.domain.requirements import validate_requirement_properties

        stored = validate_requirement_properties(E.DELIVERABLE, {RESULT_PROPERTY: written})
        assert stored == {RESULT_PROPERTY: "passed"}

        requirement, design, test = a_requirement(), a_design(), a_test(result=written)
        coverage = compute_coverage(
            [requirement, design, test],
            [realizes(design, requirement), realizes(test, requirement)],
        )
        assert coverage.verified == 1  # legacy data written before the rule reads alike

    @pytest.mark.parametrize("synonym", ["PASS", "ok", "success", "green", "1"])
    def test_a_synonym_is_refused_at_the_write_and_never_verifies(self, synonym: str) -> None:
        from ea.domain.errors import InvalidRequirementError
        from ea.domain.requirements import validate_requirement_properties

        with pytest.raises(InvalidRequirementError) as raised:
            validate_requirement_properties(E.DELIVERABLE, {RESULT_PROPERTY: synonym})
        assert synonym not in str(raised.value)  # the key is named, not the value

        requirement, design, test = a_requirement(), a_design(), a_test(result=synonym)
        coverage = compute_coverage(
            [requirement, design, test],
            [realizes(design, requirement), realizes(test, requirement)],
        )
        assert (coverage.verified, coverage.unverified) == (0, 1)  # fail closed

    def test_status_and_priority_follow_the_same_rule(self) -> None:
        from ea.domain.errors import InvalidRequirementError
        from ea.domain.requirements import validate_requirement_properties

        assert validate_requirement_properties(
            E.REQUIREMENT, {"req_status": " Approved", "req_priority": "MUST"}
        ) == {"req_status": "approved", "req_priority": "must"}
        with pytest.raises(InvalidRequirementError):
            validate_requirement_properties(E.CONSTRAINT, {"req_status": "done"})

    def test_other_elements_and_blank_values_pass_untouched(self) -> None:
        from ea.domain.requirements import validate_requirement_properties

        node = {RESULT_PROPERTY: "whatever"}
        assert validate_requirement_properties(E.NODE, node) is node
        assert validate_requirement_properties(E.REQUIREMENT, None) is None
        assert validate_requirement_properties(E.REQUIREMENT, {"req_status": ""}) == {
            "req_status": ""
        }
