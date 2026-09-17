"""Parity easy-wins: query API, silent timeout, convenience shouts, codec."""

import asyncio
import time

from zre.node import ELECT, GOODBYE, LEADER, Codec, ZreNode


async def wait_event(node, event_type, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            event = node._event_queue.get_nowait()
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.05)
    return None


async def started_pair(port_a=24901, port_b=24902):
    first = ZreNode("parity-a")
    second = ZreNode("parity-b")
    first.set_beacon_peer_port(port_a)
    second.set_beacon_peer_port(port_b)
    await first.start()
    await second.start()
    first_task = asyncio.create_task(first.run())
    second_task = asyncio.create_task(second.run())
    await first.connect_peer("127.0.0.1", port_b)
    await wait_event(first, "ENTER")
    await wait_event(second, "ENTER")
    return first, second, first_task, second_task


async def shutdown_pair(first, second, first_task, second_task):
    first._running = False
    second._running = False
    await asyncio.sleep(0.3)
    first_task.cancel()
    second_task.cancel()
    await asyncio.gather(first_task, second_task, return_exceptions=True)
    await first.stop()
    await second.stop()


class TestQueryApi:
    async def test_peers_by_group_and_peer_groups(self):
        first, second, first_task, second_task = await started_pair()
        try:
            await first.join("CHAT")
            await second.join("CHAT")
            await second.join("EXTRA")
            assert await wait_event(first, "JOIN") is not None
            await asyncio.sleep(1.0)
            assert second.peer_id_hex in first.peers_by_group("CHAT")
            assert first.peers_by_group("MISSING") == []
            assert b"CHAT" in first.peer_groups()
            assert b"EXTRA" in first.peer_groups()
            assert b"CHAT" in second.own_groups()
        finally:
            await shutdown_pair(first, second, first_task, second_task)

    async def test_peer_address_and_header_value(self):
        first = ZreNode("parity-a")
        second = ZreNode("parity-b")
        second.set_header("X-ROLE", "worker")
        first.set_beacon_peer_port(24905)
        second.set_beacon_peer_port(24906)
        await first.start()
        await second.start()
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            await first.connect_peer("127.0.0.1", 24906)
            assert await wait_event(first, "ENTER") is not None
            assert await wait_event(second, "ENTER") is not None
            address = first.peer_address(second.peer_id_hex)
            assert address.endswith(":24906")
            assert address.startswith("tcp://")
            assert first.peer_address("0" * 32) == ""
            assert first.peer_header_value(second.peer_id_hex, "X-ROLE") == b"worker"
            assert first.peer_header_value(second.peer_id_hex, "X-NOPE") is None
            assert first.peer_header_value("0" * 32, "X-ROLE") is None
        finally:
            await shutdown_pair(first, second, first_task, second_task)

    async def test_convenience_and_misc(self):
        first, second, first_task, second_task = await started_pair(
            port_a=24903, port_b=24904
        )
        try:
            await first.join("CHAT")
            await second.join("CHAT")
            assert await wait_event(first, "JOIN") is not None
            await second.shouts("CHAT", "hello-group")
            event = await wait_event(first, "SHOUT")
            assert event is not None
            assert event["payload"] == b"hello-group"
            await first.whispers(second.peer_id_hex, "hello-direkt")
            event = await wait_event(second, "WHISPER")
            assert event is not None
            assert event["payload"] == b"hello-direkt"
            assert ZreNode.version()
            assert "parity-a" in first.dump()["name"]
            first.print()
            node = ZreNode("quiet")
            node.set_silent_timeout(5000)
            assert node._ev_timeout == 5000
        finally:
            await shutdown_pair(first, second, first_task, second_task)


class TestElectionCodec:
    def test_elect_round_trip(self):
        data = Codec.encode_election(ELECT, 9, b"VOTE", "aa" * 16)
        cmd, _ver, seq, extra = Codec.decode(data)
        assert cmd == ELECT
        assert seq == 9
        assert extra["group"] == b"VOTE"
        assert extra["challenger"] == b"aa" * 16

    def test_leader_round_trip(self):
        data = Codec.encode_election(LEADER, 10, b"VOTE", "bb" * 16)
        cmd, _ver, _seq, extra = Codec.decode(data)
        assert cmd == LEADER
        assert extra["leader"] == b"bb" * 16

    def test_goodbye_round_trip(self):
        data = Codec.encode_election(GOODBYE, 3, b"VOTE", "cc" * 16)
        assert Codec.decode(data) is not None
        plain = Codec.encode_simple(GOODBYE, 4)
        cmd, _ver, seq, _extra = Codec.decode(plain)
        assert cmd == GOODBYE
        assert seq == 4
