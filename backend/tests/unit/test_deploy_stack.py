"""Guards on the deployment files themselves.

The shared database is the `postgres` service of the Infra stack (docs/adr/0029).
What is left to guard here is worth failing a build over, because it is silent
when broken:

* `docker-compose.yml` must hold nothing but the throwaway PostgreSQL the
  integration tests migrate up and down — since docs/adr/0033 there is no
  graph database left anywhere in this repository.
* `backend/Dockerfile` copies a venv from its build stage into its runtime
  stage, and a venv only works under the Python that built it. A runtime image
  one minor version ahead builds cleanly, then dies at start on
  `No module named 'alembic'` — which is what a Dependabot bump of one `FROM`
  did.
* `deploy/ea.stack.yml` must hand the API the Infra CA, or it cannot fetch
  the realm's signing keys and refuses to boot, and must build the SPA for the
  realm — Vite writes both values into the bundle (docs/adr/0032).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
STACK = REPO_ROOT / "deploy" / "ea.stack.yml"


def _stack_service(name: str) -> str:
    """One service's block of the deployed stack, without its comments.

    From `  name:` to the next key at the same indentation, so a value found
    here belongs to that service and not to its neighbour.
    """
    lines = [
        line
        for line in STACK.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^\S|^  \S", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def api_environment() -> dict[str, str]:
    """The `api` service's `environment:` block, one key to its raw value.

    Surrounding quotes are stripped, like the inline `setting()` below; a
    `${VAR:-default}` or `${VAR:?message}` expression has none to strip.
    """
    api = _stack_service("api")
    return {
        key: value.strip("\"'") for key, value in re.findall(r"^\s+(EA_\S+):\s*(.+)$", api, re.M)
    }


def _compose_declarations() -> str:
    """The compose file without its comments, which are expected to explain."""
    return "\n".join(
        line
        for line in COMPOSE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _published_ports() -> list[str]:
    """Every `- "host:container"` entry, quotes stripped."""
    return [
        match.group(1)
        for match in re.finditer(r'^\s*-\s*"([^"]*:\d+)"\s*$', _compose_declarations(), re.M)
    ]


def test_every_local_container_listens_on_loopback_only() -> None:
    """It carries a disposable password in clear; the LAN must not reach it."""
    ports = _published_ports()

    assert ports, "the compose file publishes nothing"
    assert all(port.startswith("127.0.0.1:") for port in ports), ports


def test_no_graph_database_is_left_in_the_deployment_files() -> None:
    """The graph lives in PostgreSQL since docs/adr/0033; Neo4j must not creep back."""
    for path in (COMPOSE, STACK, REPO_ROOT / ".github" / "workflows" / "ci.yml"):
        declarations = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "neo4j" not in declarations.lower(), path


def test_the_api_image_runs_the_python_its_venv_was_built_for() -> None:
    """The build stage's `uv:…-python3.12` and the runtime's `python:3.12.x` agree.

    The venv's packages sit under `lib/python3.12/`; a runtime Python of another
    minor version does not look there, and every import fails at start.
    """
    images = re.findall(r"^FROM\s+(\S+)", BACKEND_DOCKERFILE.read_text(encoding="utf-8"), re.M)
    versions = [re.search(r"python:?(\d+\.\d+)", image) for image in images]

    assert len(images) == 2, images
    assert all(versions), f"no Python version in {images}"
    assert len({match.group(1) for match in versions if match}) == 1, images


def test_the_deployed_api_verifies_tokens_with_the_infra_ca_mounted_read_only() -> None:
    api = _stack_service("api")

    assert re.search(r"^\s+EA_AUTH_CA_CERT: /etc/ssl/certs/infra-ca\.pem\s*$", api, re.M), api
    assert re.search(r"^\s+target: /etc/ssl/certs/infra-ca\.pem\s*$", api, re.M), api
    enabled = re.search(r"^\s+EA_AUTH_ENABLED:\s*(\S+)\s*$", api, re.M)
    assert enabled is None or enabled.group(1).strip("\"'") == "true", enabled


def test_the_deployed_api_serves_mcp_under_the_vhost_it_is_reached_by() -> None:
    """LibreChat calls `/api/mcp` through the Infra NGINX (docs/adr/0034).

    Behind the proxy every peer is NGINX, so remote clients are allowed and the
    token is the barrier — auth staying on is the test above. The `Host` NGINX
    forwards and the resource the 401 advertises are both the vhost's name, the
    one `EA_CORS_ORIGINS` already names: a drift between them is a 421, or a
    login that discovers nothing.
    """
    api = _stack_service("api")

    def setting(name: str) -> str:
        found = re.search(rf"^\s+{name}:\s*(\S+)\s*$", api, re.M)
        assert found, f"{name} is not set in {api}"
        return found.group(1).strip("\"'")

    hostname = setting("EA_CORS_ORIGINS").removeprefix("https://")
    assert setting("EA_MCP_ENABLED") == "true"
    assert setting("EA_MCP_ALLOW_REMOTE_CLIENTS") == "true"
    assert setting("EA_MCP_ALLOWED_HOSTS") == hostname
    assert setting("EA_MCP_RESOURCE_URL") == f"https://{hostname}/api/mcp"


def test_the_spa_image_is_built_for_the_realm() -> None:
    web = _stack_service("web")
    args = re.search(
        r"^    build:\n(?:      .*\n)*?      args:\n((?:        .*(?:\n|$))+)", web, re.M
    )

    assert args, web
    assert re.search(r"^\s+VITE_AUTH_AUTHORITY: \S", args.group(1), re.M), args.group(1)
    assert re.search(r"^\s+VITE_AUTH_CLIENT_ID: \S", args.group(1), re.M), args.group(1)


def test_the_deployed_api_keeps_its_files_in_the_infra_seaweedfs_on_infra_net() -> None:
    """Inside `infra-net` the object store is the service `s3` (SeaweedFS), in plain HTTP.

    No public name has an alias there (only Keycloak's does), so
    `minio.famillelallier.net` would not resolve from the container.
    See docs/adr/0036 and docs/adr/0056.
    """
    env = api_environment()

    assert env["EA_S3_ENABLED"] == "true"
    assert env["EA_S3_ENDPOINT"] == "${EA_S3_ENDPOINT:-s3:8333}"
    assert env["EA_S3_SECURE"] == "false"
    assert env["EA_S3_BUCKET"] == "${EA_S3_BUCKET:-ea-catalogue}"
    assert env["EA_S3_ACCESS_KEY"].startswith("${EA_S3_ACCESS_KEY:?")
    assert env["EA_S3_SECRET_KEY"].startswith("${EA_S3_SECRET_KEY:?")


def test_the_deployed_api_embeds_with_the_ollama_of_the_cluster() -> None:
    """The stack's own default is Ollama on 192.168.2.10:11434 (docs/adr/0040).

    A wrong default is what made `deploy/ea.env` — not versioned, one per
    machine — the only place the real address was written, and then the only
    place it could drift. docs/adr/0038 fixed the machine and left the port
    wrong, which cost a second failed deployment: the stack's default was
    finally right about *where*, so `deploy/ea.env` stopped overriding it, and
    every container then probed a closed port.
    """
    env = api_environment()

    assert (
        env["EA_EMBEDDINGS_BASE_URL"] == "${EA_EMBEDDINGS_BASE_URL:-http://192.168.2.10:11434/v1}"
    )


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None, reason="bash ou jq absent"
)
def test_the_portainer_script_passes_its_own_selftest() -> None:
    """`scripts/portainer-stack.sh selftest` reads no file of this machine.

    It covers the two pure functions the deployment leans on: the env file it
    turns into stack variables, and the overrides it names before deploying —
    a default corrected in the repo is worth nothing while a line of
    `deploy/ea.env` quietly covers it (docs/adr/0038).
    """
    done = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "portainer-stack.sh"), "selftest"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr or done.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")
def test_the_portainer_script_knows_how_to_restart_the_stack() -> None:
    """`restart` is a command of the script, and its usage says so.

    Nothing re-probes a degraded store (docs/adr/0037): the API reads its
    configuration and probes MinIO and the embedder once, at boot. Bringing a
    fixed store back therefore means restarting the containers — an operation
    the repository named in prose and offered nowhere.
    """
    done = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "portainer-stack.sh"), "commande-inconnue"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode != 0
    assert "up|down|restart|delete|selftest" in done.stderr


def test_the_makefile_offers_that_restart() -> None:
    """`make app-restart` exists, is phony, and drives Portainer.

    Reads go through `docker compose -p ea`, writes through Portainer's API so
    it stays the stack's owner (docs/adr/0027) — restarting is a write.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert re.search(r"^app-restart:.*##", makefile, re.MULTILINE)
    assert "app-up app-down app-restart app-delete" in makefile  # la liste .PHONY
    assert re.search(r"^app-restart:.*\n\t\$\(PORTAINER_STACK\) restart$", makefile, re.MULTILINE)


# --- Le runner appelle Portainer par son URL (docs/adr/0045) -----------------

PORTAINER_SCRIPT = REPO_ROOT / "scripts" / "portainer-stack.sh"
THE_KEY = "ptr_SECRET_KEY_0123"
THE_URL = "https://portainer.example.test"

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None, reason="bash ou jq absent"
)


def _run_portainer(
    tmp_path: Path, *, env: dict[str, str], docker_ok: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str], bool]:
    """Run `delete` with a fake curl and a fake docker first on PATH.

    Returns the process, the arguments of every curl call, and whether docker ran.
    `$CA` in a value is replaced by a real (dummy) file.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(
        '#!/bin/sh\necho "$*" >>"$CALLS"\ncat >/dev/null\n'
        'case "$*" in *"/api/endpoints"*) echo \'[{"Type":1,"Id":1}]\';; *) echo "[]";; esac\n'
    )
    (bin_dir / "docker").write_text(
        '#!/bin/sh\ntouch "$DOCKER_RAN"\n' + ("exit 0\n" if docker_ok else "exit 1\n")
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    full = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls"),
        "DOCKER_RAN": str(tmp_path / "docker-ran"),
        "PORTAINER_API_KEY": THE_KEY,
        **{k: v.replace("$CA", str(ca)) for k, v in env.items()},
    }
    done = subprocess.run(
        ["bash", str(PORTAINER_SCRIPT), "delete"],
        capture_output=True,
        text=True,
        check=False,
        env=full,
    )
    calls_file = tmp_path / "calls"
    calls = calls_file.read_text().splitlines() if calls_file.exists() else []
    return done, calls, (tmp_path / "docker-ran").exists()


@needs_tools
def test_with_a_url_the_script_calls_curl_directly_and_never_docker(tmp_path: Path) -> None:
    done, calls, docker_ran = _run_portainer(
        tmp_path, env={"PORTAINER_URL": THE_URL + "/", "PORTAINER_CA_CERT": "$CA"}
    )

    assert done.returncode == 0, done.stderr
    assert not docker_ran
    assert calls and all(f"{THE_URL}/api/" in c for c in calls)
    assert all("--cacert" in c and " -k " not in f" {c} " for c in calls)
    assert THE_KEY not in "".join(calls) + done.stdout + done.stderr


@needs_tools
def test_without_a_url_the_script_keeps_the_docker_curl(tmp_path: Path) -> None:
    done, calls, docker_ran = _run_portainer(tmp_path, env={}, docker_ok=True)

    assert docker_ran
    assert calls == []
    assert THE_KEY not in done.stdout + done.stderr


@needs_tools
@pytest.mark.parametrize("url", ["http://portainer.example.test", "portainer.example.test"])
def test_a_url_that_is_not_https_is_refused(tmp_path: Path, url: str) -> None:
    done, calls, docker_ran = _run_portainer(
        tmp_path, env={"PORTAINER_URL": url, "PORTAINER_CA_CERT": "$CA"}
    )

    assert done.returncode != 0 and "https://" in done.stderr
    assert calls == [] and not docker_ran


@needs_tools
def test_https_without_a_ca_is_refused_unless_insecure_is_explicit(tmp_path: Path) -> None:
    done, calls, docker_ran = _run_portainer(tmp_path, env={"PORTAINER_URL": THE_URL})
    assert done.returncode != 0 and "PORTAINER_INSECURE=1" in done.stderr
    assert calls == [] and not docker_ran

    loose = tmp_path / "loose"
    loose.mkdir()
    done, calls, _ = _run_portainer(
        loose, env={"PORTAINER_URL": THE_URL, "PORTAINER_INSECURE": "1"}
    )
    assert done.returncode == 0, done.stderr
    assert "AVERTISSEMENT" in done.stderr
    assert calls and all(" -k " in f" {c} " for c in calls)
    assert THE_KEY not in "".join(calls) + done.stdout + done.stderr


@needs_tools
def test_infra_ca_cert_is_enough_to_verify(tmp_path: Path) -> None:
    done, calls, _ = _run_portainer(
        tmp_path, env={"PORTAINER_URL": THE_URL, "INFRA_CA_CERT": "$CA"}
    )

    assert done.returncode == 0, done.stderr
    assert calls and all("--cacert" in c for c in calls)


def test_the_script_is_valid_bash_and_never_traces() -> None:
    text = _code(PORTAINER_SCRIPT)

    assert "set -x" not in text and "set -o xtrace" not in text
    if shutil.which("shellcheck"):
        cmd = ["shellcheck", "-S", "error", str(PORTAINER_SCRIPT)]
    elif shutil.which("bash"):
        cmd = ["bash", "-n", str(PORTAINER_SCRIPT)]
    else:
        pytest.skip("ni shellcheck ni bash")
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr


def _code(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_the_runner_no_longer_holds_the_docker_socket_and_the_workflow_agrees() -> None:
    """The socket was there for the curl container only (docs/adr/0045)."""
    compose = _code(REPO_ROOT / "docker-compose.runner.yml")
    workflow = _code(REPO_ROOT / ".github" / "workflows" / "deploy.yml")

    assert "docker.sock" not in compose
    assert "/etc/ea/infra-ca.crt:ro" in compose
    assert "PORTAINER_URL:" in workflow
    assert "PORTAINER_CA_CERT: /etc/ea/infra-ca.crt" in workflow
    assert "docker " not in workflow
    assert "pull_request" not in workflow


def test_the_deployed_api_takes_the_third_party_targets_from_optional_variables() -> None:
    """A token in the stack file would be committed; an empty one must not block `app-up`."""
    environment = api_environment()

    for name in ("EA_EXTERNAL_TARGETS", "EA_EXTERNAL_TOKENS", "EA_EXTERNAL_CA_CERT"):
        assert environment[name] == "${" + name + ":-}", name


def test_no_third_party_secret_or_host_is_written_in_the_stack_or_its_example() -> None:
    text = (REPO_ROOT / "deploy" / "ea.stack.yml").read_text(encoding="utf-8")
    example = (REPO_ROOT / "deploy" / "ea.env.example").read_text(encoding="utf-8")

    assert "api.github.com" not in text
    assert "ghp_" not in text + example
    assert "github_pat_" not in text + example
    assert "EA_EXTERNAL_TOKENS=\n" in example


def test_the_ca_is_inlined_and_matches_the_file_in_git() -> None:
    # `file:` is a bind mount the host daemon resolves: Portainer's clone is not there.
    import yaml

    stack = yaml.safe_load((STACK).read_text())
    config = stack["configs"]["infra-ca"]
    assert "file" not in config
    assert config["content"].strip() == (REPO_ROOT / "deploy" / "infra-ca.crt").read_text().strip()
