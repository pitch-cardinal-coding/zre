"""Gossip discovery: UDP-free mesh through a hub, late joiners, departure."""

import asyncio
import time

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
        hub = GossipHub()
        server = await hub.serve("127.0.0.1", HUB_PORT + 2)
        endpoint = f"tcp://127.0.0.1:{HUB_PORT + 2}"
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
            server.close()
            await server.wait_closed()
