"""Shared pytest fixtures/overrides.

ZRE_TEST_PORT
    Override the UDP beacon port used by tests (default 15670). Set this on
    machines where another ZRE-speaking service already owns UDP 15670 —
    otherwise the suite discovers that service and fails cross-talk checks
    like ``test_two_node_discovery`` expecting exactly one peer.

    Example::

        ZRE_TEST_PORT=24190 python3 -m pytest tests/ -q
"""

import os

import pytest

from zre.node import ZreNode

TEST_PORT = int(os.environ.get("ZRE_TEST_PORT", "15670"))
_DEFAULT_PORT = 15670


@pytest.fixture(autouse=True)
def zre_test_port(monkeypatch):
    """Point the default beacon port at TEST_PORT for every node created."""
    if TEST_PORT == _DEFAULT_PORT:
        yield
        return
    orig_init = ZreNode.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        if self._beacon_port == _DEFAULT_PORT:
            self._beacon_port = TEST_PORT

    monkeypatch.setattr(ZreNode, "__init__", patched_init)
    yield
