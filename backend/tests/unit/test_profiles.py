"""Profiles and stereotypes: pure rules, no store."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType
from ea.domain.profiles import (
    MAX_PROPERTY_VALUE_LENGTH,
    IconKey,
    Profile,
    PropertyKind,
    Stereotype,
    StereotypeProperty,
    check_pattern,
    missing_required,
    profile_from_document,
    profile_to_document,
    validate_stereotype_properties,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_property(**overrides: object) -> StereotypeProperty:
    fields: dict[str, object] = {"key": "owner", "label": "Responsable"}
    return StereotypeProperty(**{**fields, **overrides})  # type: ignore[arg-type]


def a_stereotype(*properties: StereotypeProperty, **overrides: object) -> Stereotype:
    fields: dict[str, object] = {
        "id": uuid4(),
        "profile_id": uuid4(),
        "name": "database",
        "base_type": ElementType.APPLICATION_COMPONENT,
        "colour": "#ffe0e0",
        "icon_key": IconKey.DATABASE,
        "properties": properties,
    }
    return Stereotype(**{**fields, **overrides})  # type: ignore[arg-type]


class TestDeclaring:
    def test_a_key_is_a_plain_identifier_and_never_an_ip_addressing_key(self) -> None:
        with pytest.raises(ValueError, match="identifier"):
            a_property(key="1 owner")
        for reserved in ("ip_address", "cidr", "vrf", "ip_reserved"):
            with pytest.raises(ValueError, match="reserved"):
                a_property(key=reserved)

    def test_an_enumeration_needs_values_and_a_pattern_needs_a_pattern(self) -> None:
        with pytest.raises(ValueError, match="allowed"):
            a_property(kind=PropertyKind.ENUM)
        with pytest.raises(ValueError, match="pattern"):
            a_property(kind=PropertyKind.PATTERN)
        with pytest.raises(ValueError, match="free"):
            a_property(allowed_values=("a",))

    def test_a_stereotype_extends_a_real_type_and_never_a_junction(self) -> None:
        with pytest.raises(ValueError, match="connector"):
            a_stereotype(base_type=ElementType.JUNCTION)

    def test_a_colour_is_rrggbb_or_nothing(self) -> None:
        assert a_stereotype(colour=None).colour is None
        for bad in ("red", "#fff", '#ffee00" onload="x'):
            with pytest.raises(ValueError, match="colour"):
                a_stereotype(colour=bad)

    def test_two_properties_cannot_share_a_key_within_a_stereotype(self) -> None:
        with pytest.raises(ValueError, match="twice"):
            a_stereotype(a_property(), a_property(label="Autre"))

    def test_a_profile_cannot_hold_two_stereotypes_of_one_name(self) -> None:
        profile_id = uuid4()
        with pytest.raises(ValueError, match="twice"):
            Profile(
                id=profile_id,
                name="SI",
                description="",
                created_at=NOW,
                updated_at=NOW,
                stereotypes=(
                    a_stereotype(profile_id=profile_id),
                    a_stereotype(profile_id=profile_id),
                ),
            )


class TestThePattern:
    @pytest.mark.parametrize(
        "pattern", [r"^[A-Z]{3}-\d{4}$", r"(ab)+", r"[a-z]+\.[a-z]+", r"(?:oui|non)"]
    )
    def test_a_linear_pattern_is_accepted(self, pattern: str) -> None:
        check_pattern(pattern)

    @pytest.mark.parametrize(
        "pattern", [r"(a+)+$", r"(a|b)*c", r"(.*a){10}", r"(a)\1", r"((a+)b)+"]
    )
    def test_a_pattern_that_can_backtrack_catastrophically_is_refused(self, pattern: str) -> None:
        with pytest.raises(ValueError, match="pattern"):
            check_pattern(pattern)

    def test_a_pattern_is_bounded_and_must_compile(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            check_pattern("a" * 201)
        with pytest.raises(ValueError, match="pattern"):
            check_pattern("[unclosed")


class TestValidatingValues:
    def test_an_enumeration_accepts_its_values_and_refuses_others(self) -> None:
        tier = a_property(
            key="tier", label="Niveau", kind=PropertyKind.ENUM, allowed_values=("1", "2", "3")
        )
        stereotype = a_stereotype(tier)

        assert validate_stereotype_properties({"tier": "2"}, [stereotype]) == {"tier": "2"}
        with pytest.raises(ValueError, match="tier"):
            validate_stereotype_properties({"tier": "9"}, [stereotype])

    def test_a_pattern_must_match_the_whole_value(self) -> None:
        code = a_property(
            key="code", label="Code", kind=PropertyKind.PATTERN, pattern=r"[A-Z]{3}-\d{4}"
        )
        stereotype = a_stereotype(code)

        validate_stereotype_properties({"code": "ABC-1234"}, [stereotype])
        with pytest.raises(ValueError, match="code"):
            validate_stereotype_properties({"code": "ABC-1234 et plus"}, [stereotype])

    def test_a_value_longer_than_the_bound_is_refused(self) -> None:
        stereotype = a_stereotype(a_property())

        with pytest.raises(ValueError, match="characters"):
            validate_stereotype_properties(
                {"owner": "x" * (MAX_PROPERTY_VALUE_LENGTH + 1)}, [stereotype]
            )

    def test_a_key_nobody_declared_stays_free_and_a_blank_value_is_not_validated(self) -> None:
        tier = a_property(key="tier", label="Niveau", kind=PropertyKind.ENUM, allowed_values=("1",))
        stereotype = a_stereotype(tier)

        result = validate_stereotype_properties({"tier": "  ", "note libre": "ok"}, [stereotype])

        assert result["tier"] == "  "
        assert result["note libre"] == "ok"

    def test_two_stereotypes_may_not_declare_the_same_key(self) -> None:
        first = a_stereotype(a_property(), name="un")
        second = a_stereotype(a_property(), name="deux")

        with pytest.raises(ValueError, match="declared by both"):
            validate_stereotype_properties({}, [first, second])

    def test_required_is_reported_not_enforced(self) -> None:
        stereotype = a_stereotype(
            a_property(required=True), a_property(key="env", label="Env", required=True)
        )

        assert validate_stereotype_properties({}, [stereotype]) == {}
        assert missing_required({"env": "prod"}, [stereotype]) == ("owner",)
        assert missing_required({"owner": " ", "env": "prod"}, [stereotype]) == ("owner",)
        assert missing_required({"owner": "ops", "env": "prod"}, [stereotype]) == ()


class TestTheDocument:
    def test_a_profile_survives_export_and_import(self) -> None:
        profile_id = uuid4()
        tier = a_property(
            key="tier",
            label="Niveau",
            kind=PropertyKind.ENUM,
            allowed_values=("1", "2"),
            help="1 = critique",
        )
        profile = Profile(
            id=profile_id,
            name="SI",
            description="Profil du SI",
            created_at=NOW,
            updated_at=NOW,
            stereotypes=(a_stereotype(a_property(required=True), tier, profile_id=profile_id),),
        )

        document = profile_to_document(profile)
        imported = profile_from_document(document, now=NOW)

        assert document["profile_format"] == 1
        assert imported.name == "SI"
        assert imported.id != profile.id
        again = profile_to_document(imported)
        assert again == document

    def test_a_document_of_another_format_or_with_a_dangerous_pattern_is_refused(self) -> None:
        profile = Profile(id=uuid4(), name="SI", description="", created_at=NOW, updated_at=NOW)
        document = profile_to_document(profile)

        with pytest.raises(ValueError, match="profile_format"):
            profile_from_document({**document, "profile_format": 2}, now=NOW)
        dangerous = {
            **document,
            "stereotypes": [
                {
                    "name": "x",
                    "base_type": "node",
                    "colour": None,
                    "icon_key": "none",
                    "properties": [
                        {
                            "key": "k",
                            "label": "K",
                            "required": False,
                            "kind": "pattern",
                            "allowed_values": [],
                            "pattern": "(a+)+",
                            "help": "",
                        }
                    ],
                }
            ],
        }
        with pytest.raises(ValueError, match="pattern"):
            profile_from_document(dangerous, now=NOW)

    def test_an_unknown_base_type_is_refused(self) -> None:
        document = {
            "profile_format": 1,
            "name": "SI",
            "description": "",
            "stereotypes": [
                {
                    "name": "x",
                    "base_type": "spaceship",
                    "colour": None,
                    "icon_key": "none",
                    "properties": [],
                }
            ],
        }

        with pytest.raises(ValueError, match="spaceship"):
            profile_from_document(document, now=NOW)


class TestHardening:
    def test_valid_from_and_until_are_reserved_for_the_time_convention(self) -> None:
        for reserved in ("valid_from", "valid_until"):
            with pytest.raises(ValueError, match="reserved"):
                a_property(key=reserved)

    def test_a_pattern_cannot_stack_unbounded_repetitions(self) -> None:
        with pytest.raises(ValueError, match="repetitions"):
            check_pattern(r"a*a*a*a*b")
        check_pattern(r"^[A-Z]{3}-\d{4}-[a-z]+$")

    def test_a_value_checked_by_pattern_is_short(self) -> None:
        code = a_property(key="code", label="Code", kind=PropertyKind.PATTERN, pattern=r"a+")
        with pytest.raises(ValueError, match="at most 200"):
            validate_stereotype_properties({"code": "a" * 201}, [a_stereotype(code)])
