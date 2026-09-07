"""The ArchiMate 3.2 element catalogue, as pure data.

Nothing here imports FastAPI, SQLAlchemy or the Neo4j driver: the metamodel is
the one part of this codebase that must outlive any storage decision.

The members carry only their wire value. Their classification lives in
`_CLASSIFICATION` below, which keeps `ElementType("application_component")` a
plain one-argument lookup and gives each element the three coordinates every
rule in `rules.py` is expressed against:

* its **layer** — where it sits in the layered view (Business, Application, ...);
* its **aspect** — active structure (who), behavior (what happens),
  passive structure (what is acted upon), or motivation (why);
* its **kind** — the refinement inside an aspect (an interface and a component
  are both active structure, but only one of them can be served *by* the other).

`test_taxonomy.py` fails if a member is ever added without a classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Layer(StrEnum):
    """The layers of ArchiMate 3.2, plus the cross-layer `OTHER` grouping."""

    MOTIVATION = "motivation"
    STRATEGY = "strategy"
    BUSINESS = "business"
    APPLICATION = "application"
    TECHNOLOGY = "technology"
    PHYSICAL = "physical"
    IMPLEMENTATION_MIGRATION = "implementation_migration"
    OTHER = "other"


class Aspect(StrEnum):
    """The aspect an element belongs to — the column of the ArchiMate framework."""

    ACTIVE_STRUCTURE = "active_structure"
    BEHAVIOR = "behavior"
    PASSIVE_STRUCTURE = "passive_structure"
    MOTIVATION = "motivation"
    COMPOSITE = "composite"
    CONNECTOR = "connector"


class ElementKind(StrEnum):
    """Refinement within an aspect, used by the relationship rules."""

    PLAIN = "plain"
    INTERFACE = "interface"
    SERVICE = "service"
    EVENT = "event"
    COLLABORATION = "collaboration"
    CONNECTOR = "connector"


class ElementType(StrEnum):
    """Every element type ArchiMate 3.2 defines.

    The member value is the wire and storage form: it appears in the OpenAPI
    schema and in the graph, so renaming one is a breaking change.
    """

    # --- Motivation ------------------------------------------------------
    STAKEHOLDER = "stakeholder"
    DRIVER = "driver"
    ASSESSMENT = "assessment"
    GOAL = "goal"
    OUTCOME = "outcome"
    PRINCIPLE = "principle"
    REQUIREMENT = "requirement"
    CONSTRAINT = "constraint"
    MEANING = "meaning"
    VALUE = "value"

    # --- Strategy --------------------------------------------------------
    RESOURCE = "resource"
    CAPABILITY = "capability"
    COURSE_OF_ACTION = "course_of_action"
    VALUE_STREAM = "value_stream"

    # --- Business --------------------------------------------------------
    BUSINESS_ACTOR = "business_actor"
    BUSINESS_ROLE = "business_role"
    BUSINESS_COLLABORATION = "business_collaboration"
    BUSINESS_INTERFACE = "business_interface"
    BUSINESS_PROCESS = "business_process"
    BUSINESS_FUNCTION = "business_function"
    BUSINESS_INTERACTION = "business_interaction"
    BUSINESS_EVENT = "business_event"
    BUSINESS_SERVICE = "business_service"
    BUSINESS_OBJECT = "business_object"
    CONTRACT = "contract"
    REPRESENTATION = "representation"
    PRODUCT = "product"

    # --- Application -----------------------------------------------------
    APPLICATION_COMPONENT = "application_component"
    APPLICATION_COLLABORATION = "application_collaboration"
    APPLICATION_INTERFACE = "application_interface"
    APPLICATION_FUNCTION = "application_function"
    APPLICATION_INTERACTION = "application_interaction"
    APPLICATION_PROCESS = "application_process"
    APPLICATION_EVENT = "application_event"
    APPLICATION_SERVICE = "application_service"
    DATA_OBJECT = "data_object"

    # --- Technology ------------------------------------------------------
    NODE = "node"
    DEVICE = "device"
    SYSTEM_SOFTWARE = "system_software"
    TECHNOLOGY_COLLABORATION = "technology_collaboration"
    TECHNOLOGY_INTERFACE = "technology_interface"
    PATH = "path"
    COMMUNICATION_NETWORK = "communication_network"
    TECHNOLOGY_FUNCTION = "technology_function"
    TECHNOLOGY_PROCESS = "technology_process"
    TECHNOLOGY_INTERACTION = "technology_interaction"
    TECHNOLOGY_EVENT = "technology_event"
    TECHNOLOGY_SERVICE = "technology_service"
    ARTIFACT = "artifact"

    # --- Physical --------------------------------------------------------
    EQUIPMENT = "equipment"
    FACILITY = "facility"
    DISTRIBUTION_NETWORK = "distribution_network"
    MATERIAL = "material"

    # --- Implementation & Migration --------------------------------------
    WORK_PACKAGE = "work_package"
    DELIVERABLE = "deliverable"
    IMPLEMENTATION_EVENT = "implementation_event"
    PLATEAU = "plateau"
    GAP = "gap"

    # --- Cross-layer -----------------------------------------------------
    LOCATION = "location"
    GROUPING = "grouping"
    JUNCTION = "junction"

    @property
    def layer(self) -> Layer:
        return _CLASSIFICATION[self].layer

    @property
    def aspect(self) -> Aspect:
        return _CLASSIFICATION[self].aspect

    @property
    def kind(self) -> ElementKind:
        return _CLASSIFICATION[self].kind

    @property
    def is_connector(self) -> bool:
        """A junction wires relationships together; it models nothing by itself."""
        return self.kind is ElementKind.CONNECTOR

    @property
    def label(self) -> str:
        """The display form of the type, e.g. `Application Component`."""
        return " ".join(part.capitalize() for part in self.value.split("_"))

    @classmethod
    def in_layer(cls, layer: Layer) -> tuple[ElementType, ...]:
        return tuple(member for member in cls if member.layer is layer)

    @classmethod
    def with_aspect(cls, aspect: Aspect) -> tuple[ElementType, ...]:
        return tuple(member for member in cls if member.aspect is aspect)


@dataclass(frozen=True, slots=True)
class Classification:
    """Where an element type sits in the ArchiMate framework."""

    layer: Layer
    aspect: Aspect
    kind: ElementKind = ElementKind.PLAIN


_E = ElementType
_L = Layer
_A = Aspect
_K = ElementKind

_CLASSIFICATION: Final[dict[ElementType, Classification]] = {
    # --- Motivation: every element answers "why" -------------------------
    _E.STAKEHOLDER: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.DRIVER: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.ASSESSMENT: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.GOAL: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.OUTCOME: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.PRINCIPLE: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.REQUIREMENT: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.CONSTRAINT: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.MEANING: Classification(_L.MOTIVATION, _A.MOTIVATION),
    _E.VALUE: Classification(_L.MOTIVATION, _A.MOTIVATION),
    # --- Strategy --------------------------------------------------------
    _E.RESOURCE: Classification(_L.STRATEGY, _A.ACTIVE_STRUCTURE),
    _E.CAPABILITY: Classification(_L.STRATEGY, _A.BEHAVIOR),
    _E.COURSE_OF_ACTION: Classification(_L.STRATEGY, _A.BEHAVIOR),
    _E.VALUE_STREAM: Classification(_L.STRATEGY, _A.BEHAVIOR),
    # --- Business --------------------------------------------------------
    _E.BUSINESS_ACTOR: Classification(_L.BUSINESS, _A.ACTIVE_STRUCTURE),
    _E.BUSINESS_ROLE: Classification(_L.BUSINESS, _A.ACTIVE_STRUCTURE),
    _E.BUSINESS_COLLABORATION: Classification(_L.BUSINESS, _A.ACTIVE_STRUCTURE, _K.COLLABORATION),
    _E.BUSINESS_INTERFACE: Classification(_L.BUSINESS, _A.ACTIVE_STRUCTURE, _K.INTERFACE),
    _E.BUSINESS_PROCESS: Classification(_L.BUSINESS, _A.BEHAVIOR),
    _E.BUSINESS_FUNCTION: Classification(_L.BUSINESS, _A.BEHAVIOR),
    _E.BUSINESS_INTERACTION: Classification(_L.BUSINESS, _A.BEHAVIOR),
    _E.BUSINESS_EVENT: Classification(_L.BUSINESS, _A.BEHAVIOR, _K.EVENT),
    _E.BUSINESS_SERVICE: Classification(_L.BUSINESS, _A.BEHAVIOR, _K.SERVICE),
    _E.BUSINESS_OBJECT: Classification(_L.BUSINESS, _A.PASSIVE_STRUCTURE),
    _E.CONTRACT: Classification(_L.BUSINESS, _A.PASSIVE_STRUCTURE),
    _E.REPRESENTATION: Classification(_L.BUSINESS, _A.PASSIVE_STRUCTURE),
    _E.PRODUCT: Classification(_L.BUSINESS, _A.PASSIVE_STRUCTURE),
    # --- Application -----------------------------------------------------
    _E.APPLICATION_COMPONENT: Classification(_L.APPLICATION, _A.ACTIVE_STRUCTURE),
    _E.APPLICATION_COLLABORATION: Classification(
        _L.APPLICATION, _A.ACTIVE_STRUCTURE, _K.COLLABORATION
    ),
    _E.APPLICATION_INTERFACE: Classification(_L.APPLICATION, _A.ACTIVE_STRUCTURE, _K.INTERFACE),
    _E.APPLICATION_FUNCTION: Classification(_L.APPLICATION, _A.BEHAVIOR),
    _E.APPLICATION_INTERACTION: Classification(_L.APPLICATION, _A.BEHAVIOR),
    _E.APPLICATION_PROCESS: Classification(_L.APPLICATION, _A.BEHAVIOR),
    _E.APPLICATION_EVENT: Classification(_L.APPLICATION, _A.BEHAVIOR, _K.EVENT),
    _E.APPLICATION_SERVICE: Classification(_L.APPLICATION, _A.BEHAVIOR, _K.SERVICE),
    _E.DATA_OBJECT: Classification(_L.APPLICATION, _A.PASSIVE_STRUCTURE),
    # --- Technology ------------------------------------------------------
    _E.NODE: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE),
    _E.DEVICE: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE),
    _E.SYSTEM_SOFTWARE: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE),
    _E.TECHNOLOGY_COLLABORATION: Classification(
        _L.TECHNOLOGY, _A.ACTIVE_STRUCTURE, _K.COLLABORATION
    ),
    _E.TECHNOLOGY_INTERFACE: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE, _K.INTERFACE),
    _E.PATH: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE),
    _E.COMMUNICATION_NETWORK: Classification(_L.TECHNOLOGY, _A.ACTIVE_STRUCTURE),
    _E.TECHNOLOGY_FUNCTION: Classification(_L.TECHNOLOGY, _A.BEHAVIOR),
    _E.TECHNOLOGY_PROCESS: Classification(_L.TECHNOLOGY, _A.BEHAVIOR),
    _E.TECHNOLOGY_INTERACTION: Classification(_L.TECHNOLOGY, _A.BEHAVIOR),
    _E.TECHNOLOGY_EVENT: Classification(_L.TECHNOLOGY, _A.BEHAVIOR, _K.EVENT),
    _E.TECHNOLOGY_SERVICE: Classification(_L.TECHNOLOGY, _A.BEHAVIOR, _K.SERVICE),
    _E.ARTIFACT: Classification(_L.TECHNOLOGY, _A.PASSIVE_STRUCTURE),
    # --- Physical --------------------------------------------------------
    _E.EQUIPMENT: Classification(_L.PHYSICAL, _A.ACTIVE_STRUCTURE),
    _E.FACILITY: Classification(_L.PHYSICAL, _A.ACTIVE_STRUCTURE),
    _E.DISTRIBUTION_NETWORK: Classification(_L.PHYSICAL, _A.ACTIVE_STRUCTURE),
    _E.MATERIAL: Classification(_L.PHYSICAL, _A.PASSIVE_STRUCTURE),
    # --- Implementation & Migration --------------------------------------
    _E.WORK_PACKAGE: Classification(_L.IMPLEMENTATION_MIGRATION, _A.BEHAVIOR),
    _E.DELIVERABLE: Classification(_L.IMPLEMENTATION_MIGRATION, _A.PASSIVE_STRUCTURE),
    _E.IMPLEMENTATION_EVENT: Classification(_L.IMPLEMENTATION_MIGRATION, _A.BEHAVIOR, _K.EVENT),
    _E.PLATEAU: Classification(_L.IMPLEMENTATION_MIGRATION, _A.COMPOSITE),
    _E.GAP: Classification(_L.IMPLEMENTATION_MIGRATION, _A.PASSIVE_STRUCTURE),
    # --- Cross-layer -----------------------------------------------------
    _E.LOCATION: Classification(_L.OTHER, _A.COMPOSITE),
    _E.GROUPING: Classification(_L.OTHER, _A.COMPOSITE),
    _E.JUNCTION: Classification(_L.OTHER, _A.CONNECTOR, _K.CONNECTOR),
}
