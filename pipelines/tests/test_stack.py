"""Guards on the Docker stack (compose, Dockerfile, LiteLLM) of docs/adr/0027.

Mirrors `backend/tests/unit/test_postgres_stack.py` and
`test_deploy_stack.py`: every invariant here is one whose breakage is silent —
the stack builds, starts, and is wrong.

* an unpinned image turns a rebuild into an unplanned upgrade nobody decided;
* a port published beyond loopback puts a secretless local stack on the LAN;
* a `prefect-server` tag that drifts from the client `prefect` version talks
  to a server the client was never tested against;
* a real model id in `pipelines/src/` is a second place a model name could be
  changed, and the one the alias (`Literal["smart", "fast"]`) exists to avoid.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES = REPO_ROOT / "pipelines"
COMPOSE = PIPELINES / "docker-compose.yml"
DOCKERFILE = PIPELINES / "Dockerfile"
LITELLM_CONFIG = PIPELINES / "litellm.yaml"
UV_LOCK = PIPELINES / "uv.lock"
ENV_EXAMPLE = PIPELINES / ".env.example"
SRC = PIPELINES / "src"

PINNED_IMAGE = re.compile(r"^\S+:\S+@sha256:[0-9a-f]{64}$")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose() -> dict[str, object]:
    return yaml.safe_load(_text(COMPOSE))


def _services() -> dict[str, dict[str, object]]:
    services = _compose()["services"]
    assert isinstance(services, dict)
    return services


def _locked_prefect_version() -> str:
    lock = tomllib.loads(_text(UV_LOCK))
    for package in lock["package"]:
        if package["name"] == "prefect":
            version = package["version"]
            assert isinstance(version, str)
            return version
    raise AssertionError("prefect is not in pipelines/uv.lock")


def _published_ports(service: dict[str, object]) -> list[str]:
    ports = service.get("ports", [])
    assert isinstance(ports, list)
    return [str(port) for port in ports]


# --- Files exist -------------------------------------------------------


def test_the_stack_files_are_committed() -> None:
    for path in (COMPOSE, DOCKERFILE, LITELLM_CONFIG, ENV_EXAMPLE, PIPELINES / ".dockerignore"):
        assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"


def test_the_compose_project_is_named() -> None:
    assert _compose()["name"] == "ea-pipelines"


# --- Every image is pinned by tag and digest ----------------------------


def test_every_compose_image_is_pinned_by_tag_and_digest_except_the_local_build() -> None:
    services = _services()
    for name, service in services.items():
        if "image" not in service:
            assert "build" in service, f"{name} declares neither image nor build"
            continue
        image = service["image"]
        assert isinstance(image, str)
        assert PINNED_IMAGE.match(image), (name, image)


def test_the_worker_is_built_locally_rather_than_pulled() -> None:
    worker = _services()["worker"]
    assert worker.get("build") == "." or worker.get("build") == {"context": "."}
    assert "image" not in worker


def test_the_dockerfile_base_images_are_pinned_by_tag_and_digest() -> None:
    froms = re.findall(r"^FROM\s+(\S+)", _text(DOCKERFILE), re.M)
    assert froms, "the Dockerfile declares no FROM"
    for image in froms:
        assert PINNED_IMAGE.match(image), image


# --- Ports never leave loopback ------------------------------------------


def test_every_published_port_is_loopback_only() -> None:
    services = _services()
    any_port = False
    for name, service in services.items():
        for port in _published_ports(service):
            any_port = True
            assert port.startswith("127.0.0.1:"), (name, port)
    assert any_port, "no service publishes a port"


def test_the_worker_publishes_no_port() -> None:
    assert _published_ports(_services()["worker"]) == []


# --- The server tag follows the locked client version --------------------


def test_the_prefect_server_tag_matches_the_locked_client_version() -> None:
    image = _services()["prefect-server"]["image"]
    assert isinstance(image, str)
    match = re.match(r"^prefecthq/prefect:([0-9.]+)-python3\.12@sha256:[0-9a-f]{64}$", image)
    assert match, image
    assert match.group(1) == _locked_prefect_version()


# --- Secrets have no default, so a missing one refuses rather than invents ---


def test_the_required_secrets_have_no_default() -> None:
    compose = _text(COMPOSE)
    for variable in (
        "PREFECT_DATABASE_URL",
        "PREFECT_AUTH_STRING",
        "LITELLM_DATABASE_URL",
        "LITELLM_MASTER_KEY",
    ):
        assert f"${{{variable}:?" in compose, variable
        assert f"${{{variable}:-" not in compose, variable


# --- LiteLLM exposes exactly the two aliases the pipeline is allowed to use --


def test_the_litellm_aliases_are_exactly_smart_and_fast() -> None:
    config = yaml.safe_load(_text(LITELLM_CONFIG))
    names = {entry["model_name"] for entry in config["model_list"]}
    assert names == {"smart", "fast"}


def test_litellm_settings_retry_and_timeout_and_master_key() -> None:
    config = yaml.safe_load(_text(LITELLM_CONFIG))
    assert config["litellm_settings"]["num_retries"] == 2
    assert config["litellm_settings"]["request_timeout"] == 300
    assert config["litellm_settings"]["drop_params"] is True
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


# --- No real model id ever appears where the code lives -------------------


def test_no_real_model_id_appears_under_src() -> None:
    forbidden = ("claude-", "gpt-", "anthropic/", "openai/")
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (path, needle)
