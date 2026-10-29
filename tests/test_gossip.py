"""Gossip discovery: UDP-free mesh through a hub, late joiners, departure."""

import asyncio
import time

import zmq

from zre.gossip import GossipHub
from zre.node import ZreNode

HUB_PORT = 24931


async def wait_event(node, event_type, timeout=12.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            event = node._event_queue.get_nowait()
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.05)
    return None


async def collect(node, seconds=2.0):
    deadline = time.monotonic() + seconds
    found = []
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            found.append(node._event_queue.get_nowait())
        await asyncio.sleep(0.05)
    return found


async def drain_hub(hub, stop_event):
    """Serve a standalone GossipHub from the test loop (the pyzmq hub needs
    its ROUTER socket drained, like zgossip's zloop does)."""
    poller = zmq.Poller()
    poller.register(hub.socket, zmq.POLLIN)
    while not stop_event.is_set():
        if poller.poll(timeout=50):
            hub.handle_socket()
        await asyncio.sleep(0.02)


class TestZgsCodec:
    def test_hello_ping_pong_roundtrip(self):
        from zre.gossip import (
            ZGS_HELLO,
            ZGS_PING,
            ZGS_PONG,
            zgs_decode,
            zgs_encode,
        )

        hello = zgs_encode(ZGS_HELLO)
        assert hello == b"\xaa\xa0\x01\x01"
        assert zgs_decode(hello)["command"] == ZGS_HELLO
        assert zgs_decode(zgs_encode(ZGS_PING))["command"] == ZGS_PING
        assert zgs_decode(zgs_encode(ZGS_PONG))["command"] == ZGS_PONG

    def test_publish_roundtrip_with_string_and_bytes(self):
        from zre.gossip import ZGS_PUBLISH, zgs_decode, zgs_encode

        frame = zgs_encode(ZGS_PUBLISH, "uuid-key", "tcp://10.0.0.1:5670|ABCD", 0)
        message = zgs_decode(frame)
        assert message is not None
        assert message["command"] == ZGS_PUBLISH
        assert message["key"] == b"uuid-key"
        assert message["value"] == b"tcp://10.0.0.1:5670|ABCD"
        assert message["ttl"] == 0

    def test_malformed_frames_rejected(self):
        from zre.gossip import ZGS_PUBLISH, zgs_decode, zgs_encode

        assert zgs_decode(b"") is None
        assert zgs_decode(b"\x00\x00\x01\x01") is None  # wrong signature
        assert zgs_decode(b"\xaa\xa0\x01\x02") is None  # wrong version
        assert zgs_decode(b"\xaa\xa0\x09\x01") is None  # unknown command
        # PUBLISH with a truncated value must not decode
        frame = zgs_encode(ZGS_PUBLISH, "k", "value", 0)
        assert zgs_decode(frame[:-2]) is None


class TestGossip:
    async def test_two_nodes_find_each_other_without_beacons(self):
        first = ZreNode("gos-a")
        second = ZreNode("gos-b")
        first.gossip_bind(f"tcp://127.0.0.1:{HUB_PORT}")
        second.gossip_connect(f"tcp://127.0.0.1:{HUB_PORT}")
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            assert first._beacon_sock is None
            assert await wait_event(first, "ENTER") is not None
            assert await wait_event(second, "ENTER") is not None
            await first.join("MESH")
            await second.join("MESH")
            assert await wait_event(second, "JOIN") is not None
            await first.shout("MESH", b"via hub")
            event = await wait_event(second, "SHOUT")
            assert event is not None
            assert event["payload"] == b"via hub"
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.5)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()

    async def test_late_joiner_gets_snapshot_and_departure_forgets(self):
        endpoint = f"tcp://127.0.0.1:{HUB_PORT + 1}"
        first = ZreNode("gos-a")
        second = ZreNode("gos-b")
        first.gossip_bind(endpoint)
        second.gossip_connect(endpoint)
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            assert await wait_event(first, "ENTER") is not None
            late = ZreNode("gos-c")
            late.gossip_connect(endpoint)
            await late.start()
            late_task = asyncio.create_task(late.run())
            first_seen = await wait_event(late, "ENTER")
            assert first_seen is not None
            second_seen = await wait_event(late, "ENTER")
            assert second_seen is not None
            assert {first.peer_id_hex, second.peer_id_hex} == set(late.peers())
            await late.stop()
            late_task.cancel()
            await asyncio.gather(late_task, return_exceptions=True)
            events = await collect(second, seconds=4.0)
            exits = [
                event
                for event in events
                if event.get("type") == "EXIT"
                and event.get("peer_id") == late.peer_id_hex
            ]
            assert exits
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.5)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()

    async def test_standalone_hub_serves_connect_only_nodes(self):
        endpoint = f"tcp://127.0.0.1:{HUB_PORT + 2}"
        ctx = zmq.Context()
        hub = GossipHub(ctx)
        hub.bind(endpoint)
        stop_event = asyncio.Event()
        drainer = asyncio.create_task(drain_hub(hub, stop_event))
        first = ZreNode("gos-a")
        second = ZreNode("gos-b")
        first.gossip_connect(endpoint)
        second.gossip_connect(endpoint)
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            assert await wait_event(first, "ENTER") is not None
            assert await wait_event(second, "ENTER") is not None
            await first.whispers(second.peer_id_hex, "hub relay ok")
            event = await wait_event(second, "WHISPER")
            assert event is not None
            assert event["payload"] == b"hub relay ok"
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.5)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()
            stop_event.set()
            drainer.cancel()
            await asyncio.gather(drainer, return_exceptions=True)
            hub.close()
            ctx.term()
