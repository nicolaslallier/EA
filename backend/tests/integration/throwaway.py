"""Whether a destructive test may touch the database it is pointed at.

Pure functions, so the decision is tested without a database
(`test_throwaway_guards.py`) and the fixtures in `conftest.py` only act on it.

The rule is an address, not a setting. The database has a shared instance on
the cluster, and that address is the *default* in `Settings`: a guard that
trusted a flag would be one `backend/.env` away from emptying the graph
everyone models against. Loopback cannot be another machine, so it is the only
thing accepted — the throwaway containers of `docker-compose.yml` publish
there and nowhere else. See docs/adr/0024.

Since docs/adr/0029 the shared PostgreSQL answers on this Mac's loopback too,
so for that store the address is no longer the whole question: its port is
refused as well, and the throwaway container publishes another one.
"""

from __future__ import annotations

import ipaddress

from ea.core.config import Settings

#: The port of the shared PostgreSQL, read from the default that reaches it, so
#: the guard moves in the same commit as the database. Refused even on loopback.
SHARED_POSTGRES_PORT: int = Settings.model_fields["postgres_port"].default

#: Where `make pg-up` publishes the throwaway one — anything but the port above.
THROWAWAY_POSTGRES_PORT = 5433


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


def refuse_a_shared_postgres(host: str, port: int) -> str | None:
    """Why the PostgreSQL at `host` must not be migrated down, or `None`."""
    if not is_loopback(host):
        return (
            f"refusing to run `alembic downgrade base` on PostgreSQL at {host}:{port}: "
            "it is not loopback, so it may be a database somebody uses. "
            "Start the throwaway one with `make pg-up` and set EA_POSTGRES_HOST=127.0.0.1 "
            f"EA_POSTGRES_PORT={THROWAWAY_POSTGRES_PORT} — `make test-postgres` does both"
        )
    if port == SHARED_POSTGRES_PORT:
        return (
            f"refusing to run `alembic downgrade base` on PostgreSQL at {host}:{port}: "
            "loopback, but the port of the shared database, which lives on this Mac "
            "(docs/adr/0029). Start the throwaway one with `make pg-up` and set "
            f"EA_POSTGRES_PORT={THROWAWAY_POSTGRES_PORT} — `make test-postgres` does both"
        )
    return None
