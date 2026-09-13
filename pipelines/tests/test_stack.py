"""Guards on the Docker stack (compose, Dockerfile, LiteLLM) of docs/adr/0028.

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

from pipelines.settings import Settings

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


def _environment(service: str) -> dict[str, object]:
    environment = _services()[service].get("environment", {})
    assert isinstance(environment, dict), f"{service}: environment must be a mapping"
    return environment


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
        "PIPELINES_LITELLM_API_KEY",
        "PIPELINES_S3_ACCESS_KEY",
        "PIPELINES_S3_SECRET_KEY",
        "PIPELINES_EA_CLIENT_SECRET",
        "INFRA_CA_CERT",
    ):
        assert f"${{{variable}:?" in compose, variable
        assert f"${{{variable}:-" not in compose, variable


# --- The worker gets what `Settings` reads, and no one else's secrets -------

INFRA_CA_IN_CONTAINER = "/etc/ssl/certs/infra-ca.pem"


def test_the_worker_reads_no_env_file() -> None:
    """`env_file: .env` would hand it every secret of the stack, not only its own."""
    assert "env_file" not in _services()["worker"]


def test_the_worker_receives_no_secret_of_the_other_services() -> None:
    environment = _environment("worker")
    for variable in (
        "ANTHROPIC_API_KEY",
        "LITELLM_MASTER_KEY",
        "LITELLM_DATABASE_URL",
        "PREFECT_DATABASE_URL",
    ):
        assert variable not in environment, variable
        for name, value in environment.items():
            assert variable not in str(value), (name, variable)


def test_the_worker_receives_every_setting() -> None:
    expected = {f"PIPELINES_{name.upper()}" for name in Settings.model_fields}
    assert expected <= set(_environment("worker"))


def test_the_infra_ca_is_mandatory_and_mounted_where_settings_reads_it() -> None:
    """An empty CA file (`/dev/null` mounted in its place) fails every HTTPS call to MinIO."""
    assert "/dev/null" not in _text(COMPOSE)
    volumes = _services()["worker"]["volumes"]
    assert isinstance(volumes, list)
    (mount,) = [str(volume) for volume in volumes if INFRA_CA_IN_CONTAINER in str(volume)]
    assert mount.startswith("${INFRA_CA_CERT:?"), mount
    assert mount.endswith(f":{INFRA_CA_IN_CONTAINER}:ro"), mount
    assert _environment("worker")["PIPELINES_S3_CA_CERT"] == INFRA_CA_IN_CONTAINER
    assert not re.search(r"^PIPELINES_S3_CA_CERT=", _text(ENV_EXAMPLE), re.M)


def test_the_prefect_ui_calls_the_api_on_loopback() -> None:
    """With `--host 0.0.0.0` the UI would otherwise call `http://0.0.0.0:4200/api`."""
    assert _environment("prefect-server")["PREFECT_UI_API_URL"] == "http://127.0.0.1:4200/api"


# --- LiteLLM exposes exactly the two aliases the pipeline is allowed to use --


def test_the_litellm_aliases_are_exactly_smart_and_fast() -> None:
    config = yaml.safe_load(_text(LITELLM_CONFIG))
    names = {entry["model_name"] for entry in config["model_list"]}
    assert names == {"smart", "fast"}


def test_litellm_settings_retry_and_timeout_and_master_key() -> None:
    config = yaml.safe_load(_text(LITELLM_CONFIG))
    assert config["litellm_settings"]["num_retries"] == 2
    assert config["litellm_settings"]["request_timeout"] == 300
    # A `response_format` LiteLLM silently dropped for a model it does not know
    # would hand the pipeline free text instead of the strict schema it asked for.
    assert config["litellm_settings"]["drop_params"] is False
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


# --- No real model id ever appears where the code lives -------------------


def test_no_real_model_id_appears_under_src() -> None:
    forbidden = ("claude-", "gpt-", "anthropic/", "openai/")
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (path, needle)
