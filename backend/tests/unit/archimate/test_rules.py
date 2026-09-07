"""Which relationships the metamodel allows between which elements.

The cases below are taken from the worked examples of the ArchiMate 3.2
specification, so a regression here means the model has stopped being ArchiMate.
"""

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.archimate import permits, validate_relationship
from ea.domain.errors import IllegalRelationshipError


class TestAssociation:
    def test_association_connects_anything_to_anything(self) -> None:
        assert permits(R.ASSOCIATION, E.GOAL, E.ARTIFACT)
        assert permits(R.ASSOCIATION, E.MATERIAL, E.STAKEHOLDER)


class TestSpecialization:
    def test_an_element_specialises_its_own_type(self) -> None:
        assert permits(R.SPECIALIZATION, E.APPLICATION_COMPONENT, E.APPLICATION_COMPONENT)

    def test_an_element_does_not_specialise_another_type(self) -> None:
        assert not permits(R.SPECIALIZATION, E.APPLICATION_COMPONENT, E.NODE)


class TestAssignment:
    def test_an_actor_is_assigned_to_a_process(self) -> None:
        assert permits(R.ASSIGNMENT, E.BUSINESS_ACTOR, E.BUSINESS_PROCESS)

    def test_a_component_is_assigned_to_its_interface(self) -> None:
        assert permits(R.ASSIGNMENT, E.APPLICATION_COMPONENT, E.APPLICATION_INTERFACE)

    def test_a_node_is_assigned_to_the_artifact_it_deploys(self) -> None:
        assert permits(R.ASSIGNMENT, E.NODE, E.ARTIFACT)

    def test_a_process_is_not_assigned_to_an_actor(self) -> None:
        """Assignment runs from active structure to behavior, never the other way."""
        assert not permits(R.ASSIGNMENT, E.BUSINESS_PROCESS, E.BUSINESS_ACTOR)


class TestRealization:
    def test_a_component_realises_an_application_service(self) -> None:
        assert permits(R.REALIZATION, E.APPLICATION_COMPONENT, E.APPLICATION_SERVICE)

    def test_an_artifact_realises_the_component_it_packages(self) -> None:
        assert permits(R.REALIZATION, E.ARTIFACT, E.APPLICATION_COMPONENT)

    def test_any_element_realises_a_requirement(self) -> None:
        assert permits(R.REALIZATION, E.NODE, E.REQUIREMENT)
        assert permits(R.REALIZATION, E.BUSINESS_PROCESS, E.GOAL)

    def test_a_requirement_does_not_realise_a_node(self) -> None:
        assert not permits(R.REALIZATION, E.REQUIREMENT, E.NODE)


class TestServing:
    def test_an_application_service_serves_a_business_process(self) -> None:
        """The canonical cross-layer link of every application landscape map."""
        assert permits(R.SERVING, E.APPLICATION_SERVICE, E.BUSINESS_PROCESS)

    def test_a_service_does_not_serve_a_data_object(self) -> None:
        assert not permits(R.SERVING, E.APPLICATION_SERVICE, E.DATA_OBJECT)


class TestAccess:
    def test_a_function_accesses_a_data_object(self) -> None:
        assert permits(R.ACCESS, E.APPLICATION_FUNCTION, E.DATA_OBJECT)

    def test_a_data_object_does_not_access_a_function(self) -> None:
        assert not permits(R.ACCESS, E.DATA_OBJECT, E.APPLICATION_FUNCTION)

    def test_a_component_does_not_access_a_data_object_directly(self) -> None:
        """Access starts from behavior; the component acts through its function."""
        assert not permits(R.ACCESS, E.APPLICATION_COMPONENT, E.DATA_OBJECT)


class TestInfluence:
    def test_a_driver_influences_a_goal(self) -> None:
        assert permits(R.INFLUENCE, E.DRIVER, E.GOAL)

    def test_influence_only_targets_motivation(self) -> None:
        assert not permits(R.INFLUENCE, E.DRIVER, E.APPLICATION_COMPONENT)


class TestTriggeringAndFlow:
    @pytest.mark.parametrize("relationship", [R.TRIGGERING, R.FLOW])
    def test_behavior_triggers_behavior(self, relationship: R) -> None:
        assert permits(relationship, E.BUSINESS_PROCESS, E.BUSINESS_EVENT)

    @pytest.mark.parametrize("relationship", [R.TRIGGERING, R.FLOW])
    def test_behavior_does_not_trigger_a_passive_object(self, relationship: R) -> None:
        assert not permits(relationship, E.BUSINESS_PROCESS, E.BUSINESS_OBJECT)


class TestCompositionAndAggregation:
    @pytest.mark.parametrize("relationship", [R.COMPOSITION, R.AGGREGATION])
    def test_a_grouping_contains_anything(self, relationship: R) -> None:
        assert permits(relationship, E.GROUPING, E.NODE)

    @pytest.mark.parametrize("relationship", [R.COMPOSITION, R.AGGREGATION])
    def test_a_component_composes_a_component(self, relationship: R) -> None:
        assert permits(relationship, E.APPLICATION_COMPONENT, E.APPLICATION_COMPONENT)

    def test_a_product_aggregates_the_services_it_bundles(self) -> None:
        assert permits(R.AGGREGATION, E.PRODUCT, E.BUSINESS_SERVICE)

    @pytest.mark.parametrize("relationship", [R.COMPOSITION, R.AGGREGATION, R.SPECIALIZATION])
    def test_an_element_never_contains_or_specialises_itself(self, relationship: R) -> None:
        """Self-loops here would make the containment and inheritance trees cyclic."""
        assert not permits(relationship, E.NODE, E.NODE, same_element=True)


class TestJunction:
    def test_a_junction_joins_relationships_of_any_type(self) -> None:
        assert permits(R.FLOW, E.BUSINESS_PROCESS, E.JUNCTION)
        assert permits(R.TRIGGERING, E.JUNCTION, E.BUSINESS_PROCESS)

    def test_a_junction_is_not_specialised(self) -> None:
        assert not permits(R.SPECIALIZATION, E.JUNCTION, E.JUNCTION)


class TestValidation:
    def test_validate_is_silent_when_the_relationship_is_legal(self) -> None:
        validate_relationship(R.SERVING, E.APPLICATION_SERVICE, E.BUSINESS_PROCESS)

    def test_validate_names_the_offending_triple(self) -> None:
        with pytest.raises(IllegalRelationshipError) as caught:
            validate_relationship(R.ACCESS, E.DATA_OBJECT, E.APPLICATION_FUNCTION)

        message = str(caught.value)
        assert "access" in message
        assert "data_object" in message
        assert "application_function" in message


class TestImpactDirection:
    def test_impact_of_a_service_runs_to_what_it_serves(self) -> None:
        assert R.SERVING.impact_follows_direction

    def test_impact_of_a_part_runs_back_to_the_whole_that_contains_it(self) -> None:
        """Composition points whole -> part, but it is the whole that breaks."""
        assert not R.COMPOSITION.impact_follows_direction

    def test_impact_of_a_data_object_runs_back_to_what_reads_it(self) -> None:
        assert not R.ACCESS.impact_follows_direction
