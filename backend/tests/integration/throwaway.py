"""Whether a destructive test may touch the database it is pointed at.

Pure functions, so the decision is tested without a database
(`test_throwaway_guards.py`) and the fixtures in `conftest.py` only act on it.

The rule is an address, not a setting. Both stores have a shared instance on
the cluster, and that address is the *default* in `Settings`: a guard that
trusted a flag would be one `backend/.env` away from emptying the graph
everyone models against. Loopback cannot be another machine, so it is the only
thing accepted — the throwaway containers of `docker-compose.yml` publish
there and nowhere else. See docs/adr/0024.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: The one variable a developer sets to say "yes, wipe it". Necessary for the
#: graph, never sufficient: it says the caller means it, not where.
DESTRUCTIVE_OPT_IN = "EA_ALLOW_DESTRUCTIVE_TESTS"


def is_loopback(host: str) -> bool:
    """True for `localhost` and any loopback literal, IPv4 or IPv6.

    `0.0.0.0` is deliberately not: it is an address to *bind*, and a client
    that connects to it reaches whatever the system decides.
    """
    name = host.strip().strip("[]").lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def refuse_a_shared_graph(uri: str, *, allow_destructive: str | None) -> str | None:
    """Why the graph at `uri` must not be wiped, or `None` when it may be."""
    if allow_destructive != "1":
        return (
            "integration tests empty the graph; run `make test-integration`, "
            f"or set {DESTRUCTIVE_OPT_IN}=1 against a throwaway Neo4j"
        )
    host = urlsplit(uri).hostname or ""
    if not is_loopback(host):
        return (
            f"refusing to empty the graph at {uri}: {host or 'that host'} is not loopback, "
            "so it may be a graph somebody models against (the shared one is on the "
            "cluster). Start the throwaway one with `make db-test-up` and point "
            "EA_NEO4J_URI at bolt://127.0.0.1 — `make test-integration` does both"
        )
    return None


def refuse_a_shared_postgres(host: str, port: int) -> str | None:
    """Why the PostgreSQL at `host` must not be migrated down, or `None`."""
    if not is_loopback(host):
        return (
            f"refusing to run `alembic downgrade base` on PostgreSQL at {host}:{port}: "
            "it is not loopback, so it may be the shared database on the cluster. "
            "Start the throwaway one with `make pg-up` and set EA_POSTGRES_HOST=127.0.0.1 "
            "— `make test-postgres` does both"
        )
    return None
