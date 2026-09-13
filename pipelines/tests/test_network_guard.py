"""The autouse guard of `conftest.py` really refuses another machine.

A guard nobody tests is a guard that can be deleted by a refactor without a
single test turning red — mirrors `backend/tests/unit/test_network_guard.py`.
"""

from __future__ import annotations

import socket

import pytest

from conftest import NetworkAccessInTestError


def test_a_connection_to_another_machine_fails_at_once_and_names_it() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkAccessInTestError, match=r"192\.0\.2\.1:80"),
    ):
        sock.connect(("192.0.2.1", 80))


def test_create_connection_to_another_machine_fails_the_same_way() -> None:
    with pytest.raises(NetworkAccessInTestError, match=r"192\.0\.2\.1"):
        socket.create_connection(("192.0.2.1", 80), timeout=1)


def test_loopback_is_still_reachable() -> None:
    """`prefect_test_harness` serves its temporary API on 127.0.0.1."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
