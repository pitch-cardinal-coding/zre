"""IPv6: endpoint helpers plus a live loopback dial when available."""

import asyncio
import socket
import time

import pytest

from zre.node import ZreNode, _split_endpoint, _tcp_address


async def wait_event(node, event_type, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            event = node._event_queue.get_nowait()
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.05)
    return None


def v6_loopback_ready():
    if not socket.has_ipv6:
        return False
    probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        probe.bind(("::1", 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class TestIPv6Helpers:
    def test_split_v4(self):
        assert _split_endpoint("tcp://192.168.1.5:5555") == ("192.168.1.5", 5555)

    def test_split_bracketed_v6(self):
        assert _split_endpoint("tcp://[::1]:5555") == ("::1", 5555)

    def test_split_bare_v6(self):
        assert _split_endpoint("tcp://fe80::1:5555") == ("fe80::1", 5555)

    def test_split_wildcard(self):
        assert _split_endpoint("tcp://*:5555") == ("*", 5555)

    def test_format_v6_brackets(self):
        assert _tcp_address("::1", 5555) == "tcp://[::1]:5555"

    def test_format_v4_plain(self):
        assert _tcp_address("10.0.0.1", 5555) == "tcp://10.0.0.1:5555"

    def test_bad_endpoint_raises(self):
        with pytest.raises(ValueError):
            _split_endpoint("tcp://host:notaport")


class TestIPv6Live:
    async def test_v6_direct_dial(self):
        if not v6_loopback_ready():
            pytest.skip("no IPv6 loopback on this host")
        first = ZreNode("v6-a")
        second = ZreNode("v6-b")
        first.set_ipv6(True)
        second.set_ipv6(True)
        first.set_beacon_peer_port(24941)
        second.set_beacon_peer_port(24942)
        first.set_port(24441)
        second.set_port(24442)
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            await first.connect_peer("::1", 24942)
            assert await wait_event(first, "ENTER") is not None
            assert await wait_event(second, "ENTER") is not None
            await first.join("V6")
            await second.join("V6")
            assert await wait_event(second, "JOIN") is not None
            await first.shout("V6", b"hello v6")
            event = await wait_event(second, "SHOUT")
            assert event is not None
            assert event["payload"] == b"hello v6"
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.3)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()
