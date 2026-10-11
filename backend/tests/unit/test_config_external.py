"""The named targets third-party calls go to — declared in configuration, never by a caller.

What these tests protect: a target's host and scope are fixed by whoever deploys; a secret
is read from the environment, masked, and never echoed by an error; a typo does not
silently widen anything (docs/adr/0063).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ea.core.config import Settings
from ea.domain.external import ExternalKind

TOKEN = "ghp_this_must_never_be_printed"


def github(**over: Any) -> dict[str, Any]:
    return {
        "kind": "github_issue",
        "api_url": "https://api.github.test",
        "web_url": "https://github.test",
        "allow": ["Acme/Billing"],
        **over,
    }


def settings(
    targets: dict[str, Any] | None = None, tokens: dict[str, str] | None = None, **over: Any
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        debug=True,
        external_targets=targets if targets is not None else {"main": github()},
        external_tokens=tokens if tokens is not None else {"main": TOKEN},
        **over,
    )


class TestActiveTargets:
    def test_a_target_is_active_with_a_token_and_somewhere_it_may_read(self) -> None:
        active = settings().external_active_targets()

        assert list(active) == ["main"]
        assert active["main"].kind is ExternalKind.GITHUB_ISSUE
        assert active["main"].allow == ["acme/billing"]

    def test_no_token_or_no_allow_list_is_a_declared_but_inactive_target(self) -> None:
        assert settings(tokens={}).external_active_targets() == {}
        assert settings({"main": github(allow=[])}).external_active_targets() == {}

    def test_jira_and_servicenow_also_need_the_account_they_authenticate_as(self) -> None:
        jira = {
            "kind": "jira_issue",
            "api_url": "https://acme.atlassian.test",
            "web_url": "https://acme.atlassian.test",
            "allow": ["proj"],
        }
        snow = {
            "kind": "servicenow_ticket",
            "api_url": "https://acme.service-now.test",
            "web_url": "https://acme.service-now.test",
            "allow": ["INC"],
        }
        bare = settings({"jira": jira, "snow": snow}, {"jira": "t", "snow": "t"})
        named = settings(
            {"jira": {**jira, "user": "bot@acme.test"}, "snow": {**snow, "user": "svc"}},
            {"jira": "t", "snow": "t"},
        )

        assert bare.external_active_targets() == {}
        assert sorted(named.external_active_targets()) == ["jira", "snow"]
        assert named.external_active_targets()["jira"].allow == ["PROJ"]

    def test_nothing_declared_is_the_default_and_the_api_boots_without_any(self) -> None:
        assert Settings(_env_file=None, debug=True).external_active_targets() == {}  # type: ignore[call-arg]


class TestWhatCannotBeDeclared:
    @pytest.mark.parametrize("name", ["Main", "a b", "-x", "x" * 40, "a/b", ""])
    def test_a_target_name_is_a_short_lowercase_slug(self, name: str) -> None:
        with pytest.raises(ValueError, match="target name"):
            settings({name: github()}, {})

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.github.test",
            "ftp://api.github.test",
            "https://user:pw@api.github.test",
            "https://api.github.test/?x=1",
            "https://api.github.test/#f",
            "https:///nohost",
            "api.github.test",
        ],
    )
    def test_the_hosts_are_https_without_credentials_query_or_fragment(self, url: str) -> None:
        with pytest.raises(ValueError, match="api_url"):
            settings({"main": github(api_url=url)})
        with pytest.raises(ValueError, match="web_url"):
            settings({"main": github(web_url=url)})

    def test_plain_http_is_for_the_loopback_of_a_debug_run_and_nothing_else(self) -> None:
        local = {
            "main": github(api_url="http://127.0.0.1:9", web_url="http://127.0.0.1:9", ports=[9])
        }
        settings(local)  # debug on: fine
        with pytest.raises(ValueError, match="api_url"):
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                debug=False,
                postgres_password="x",
                external_targets=local,
                external_tokens={"main": TOKEN},
            )
        with pytest.raises(ValueError, match="api_url"):
            settings({"main": github(api_url="http://api.github.test")})

    def test_the_port_of_a_host_is_a_declared_port(self) -> None:
        settings({"main": github(api_url="https://api.github.test:8443", ports=[443, 8443])})
        with pytest.raises(ValueError, match="port"):
            settings({"main": github(api_url="https://api.github.test:8443")})

    @pytest.mark.parametrize(
        "allow",
        [["*"], ["acme"], ["acme/.."], ["acme/billing/x"], [" "]],
    )
    def test_an_allow_list_names_things_in_the_shape_of_the_kind_never_a_wildcard(
        self, allow: list[str]
    ) -> None:
        with pytest.raises(ValueError, match="allow"):
            settings({"main": github(allow=allow)})

    def test_a_url_target_does_not_exist_a_link_has_no_connector(self) -> None:
        with pytest.raises(ValueError):
            settings({"main": github(kind="url")})

    def test_a_private_network_can_be_declared_but_not_loopback_or_everything(self) -> None:
        settings({"main": github(allow_networks=["10.1.0.0/16"])})
        for bad in ("127.0.0.0/8", "0.0.0.0/0", "169.254.0.0/16"):
            with pytest.raises(ValueError, match="network"):
                settings({"main": github(allow_networks=[bad])})

    def test_a_token_for_a_target_nobody_declared_is_a_typo_not_a_silent_ignore(self) -> None:
        with pytest.raises(ValueError, match="external_tokens"):
            settings(tokens={"mian": TOKEN})

    @pytest.mark.parametrize("token", ["a\nb", "a\rb", "a\x00b", "", "x" * 5000])
    def test_a_token_that_could_split_a_header_is_refused(self, token: str) -> None:
        with pytest.raises(ValueError, match="external_tokens"):
            settings(tokens={"main": token})


class TestSecrets:
    def test_the_token_does_not_show_in_the_settings(self) -> None:
        config = settings()

        assert TOKEN not in repr(config)
        assert TOKEN not in str(config.model_dump())
        assert TOKEN not in config.model_dump_json()

    def test_a_malformed_environment_value_never_echoes_what_it_held(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EA_EXTERNAL_TOKENS", '{"main": "' + TOKEN + '", oops')

        with pytest.raises(ValueError) as raised:
            Settings(_env_file=None, debug=True)  # type: ignore[call-arg]

        assert TOKEN not in str(raised.value)
        assert TOKEN not in repr(raised.value)

    def test_a_token_of_the_wrong_type_never_echoes_it_either(self) -> None:
        with pytest.raises(ValidationError) as raised:
            settings(tokens={"main": {"secret": TOKEN}})  # type: ignore[dict-item]

        assert TOKEN not in str(raised.value)

    def test_the_environment_carries_both_as_json_and_empty_means_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EA_EXTERNAL_TARGETS", json.dumps({"main": github()}))
        monkeypatch.setenv("EA_EXTERNAL_TOKENS", json.dumps({"main": TOKEN}))
        assert list(Settings(_env_file=None, debug=True).external_active_targets()) == ["main"]  # type: ignore[call-arg]

        monkeypatch.setenv("EA_EXTERNAL_TARGETS", "")
        monkeypatch.setenv("EA_EXTERNAL_TOKENS", " ")
        assert Settings(_env_file=None, debug=True).external_active_targets() == {}  # type: ignore[call-arg]


class TestLimits:
    def test_the_defaults_are_bounded(self) -> None:
        config = settings()

        assert config.external_timeout_seconds <= config.external_total_timeout_seconds
        assert 0 < config.external_max_response_bytes <= 5_000_000
        assert config.external_per_caller <= config.external_max_in_flight

    def test_a_caller_never_has_more_places_than_exist(self) -> None:
        with pytest.raises(ValueError, match="external_per_caller"):
            settings(external_per_caller=9, external_max_in_flight=2)
