"""
WAN capability tests for ZRE.

Tests direct peer connection via connect_peer(host, port):
  - Direct connection without beacons
  - Messaging after direct connection
  - Multiple concurrent connections
  - Edge cases (simultaneous connect, unknown whisper, bad host, LEAVE)
  - Duplicate-HELLO storm guard, advertised-endpoint validation

Cross-subnet proof lives outside pytest (see examples/wan_direct.py):
it needs a live far host, so it runs as a manual script, not a test.
"""

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zre.node import Codec, ZreNode


def drain(node):
    events = []
    while not node._event_queue.empty():
        try:
            events.append(node._event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


async def wait_event(node, event_type, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in drain(node):
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.05)
    return None


async def wait_event_count(node, event_type, count, timeout=5.0):
    found = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in drain(node):
            if event.get("type") == event_type:
                found.append(event)
                if len(found) >= count:
                    return found
        await asyncio.sleep(0.05)
    return found


async def two_nodes_direct(name1="A", name2="B"):
    n1 = ZreNode(name1)
    n2 = ZreNode(name2)
    n1.set_port(19810)
    n2.set_port(19811)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    return n1, n2, t1, t2


async def cleanup(*nodes_and_tasks):
    nodes = nodes_and_tasks[::2]
    tasks = nodes_and_tasks[1::2]
    for n in nodes:
        n._running = False
    await asyncio.sleep(0.3)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for n in nodes:
        await n.stop()


# Direct Connection Tests


@pytest.mark.asyncio
async def test_direct_connection_enter_event():
    """Direct connection emits ENTER event."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="DC1", name2="DC2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        event = await wait_event(n1, "ENTER", timeout=5.0)
        assert event is not None, "DC1 should receive ENTER after direct connect"
        assert event["peer_name"] == "DC2"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_connection_bidirectional():
    """Both nodes see each other after one initiates direct connection."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="BI1", name2="BI2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        e1 = await wait_event(n1, "ENTER", timeout=5.0)
        e2 = await wait_event(n2, "ENTER", timeout=5.0)
        assert e1 is not None, "BI1 should see ENTER"
        assert e2 is not None, "BI2 should see ENTER (HELLO response)"
        assert e1["peer_name"] == "BI2"
        assert e2["peer_name"] == "BI1"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_connection_no_beacon():
    """Direct connection resolves via HELLO exchange; the pending dial stays out of peers()."""
    n1 = ZreNode("NB1")
    n2 = ZreNode("NB2")
    n1.set_port(19801)
    n2.set_port(19802)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    try:
        await asyncio.sleep(1.0)
        assert len(n1.peers()) == 0, "NB1 should have no peers before connect"

        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        event = await wait_event(n1, "ENTER", timeout=8.0)
        assert event is not None, "NB1 should receive ENTER"
        assert len(n1.peers()) >= 1, "NB1 should have at least 1 peer"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_connection_already_connected():
    """Second connect to same peer is idempotent (no duplicate peer)."""
    n1 = ZreNode("ID1")
    n2 = ZreNode("ID2")
    n1.set_port(19803)
    n2.set_port(19804)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        e1 = await wait_event(n1, "ENTER", timeout=5.0)
        assert e1 is not None

        peers_before = len(n1.peers())
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await asyncio.sleep(1.0)
        assert len(n1.peers()) == peers_before, (
            "Peer count unchanged after second connect"
        )
    finally:
        await cleanup(n1, t1, n2, t2)


# Direct Connection + Messaging


@pytest.mark.asyncio
async def test_direct_connection_shout():
    """SHOUT works after direct connection."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="DS1", name2="DS2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await wait_event(n1, "ENTER", timeout=5.0)

        await n1.join(b"CHAT")
        await n2.join(b"CHAT")
        await asyncio.sleep(0.5)

        drain(n1)
        drain(n2)

        await n1.shout(b"CHAT", b"Hello via WAN!")
        event = await wait_event(n2, "SHOUT", timeout=5.0)
        assert event is not None, "DS2 should receive SHOUT"
        assert event["payload"] == b"Hello via WAN!"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_connection_whisper():
    """WHISPER works after direct connection."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="DW1", name2="DW2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await wait_event(n1, "ENTER", timeout=5.0)

        drain(n1)
        drain(n2)

        peers = n1.peers()
        assert len(peers) == 1
        await n1.whisper(peers[0], b"Secret WAN message")
        event = await wait_event(n2, "WHISPER", timeout=5.0)
        assert event is not None, "DW2 should receive WHISPER"
        assert event["payload"] == b"Secret WAN message"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_connection_multiple_messages():
    """Multiple messages delivered after direct connection."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="DM1", name2="DM2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await wait_event(n1, "ENTER", timeout=5.0)

        await n1.join(b"CHAT")
        await n2.join(b"CHAT")
        await asyncio.sleep(0.5)

        drain(n1)
        drain(n2)

        for i in range(5):
            await n1.shout(b"CHAT", f"msg-{i}".encode())

        shouts = await wait_event_count(n2, "SHOUT", 5, timeout=5.0)
        assert len(shouts) >= 5, f"Expected 5 SHOUTs, got {len(shouts)}"
    finally:
        await cleanup(n1, t1, n2, t2)


# Direct + Beacon Hybrid


@pytest.mark.asyncio
async def test_direct_and_beacon_discovery():
    """Node discovered via beacon also receives direct connection."""
    n1 = ZreNode("HB1")
    n2 = ZreNode("HB2")
    n1.set_port(19812)
    n2.set_port(19812)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    try:
        await wait_event(n1, "ENTER", timeout=8.0)
        assert len(n1.peers()) == 1, "HB1 should discover HB2 via beacon"

        peers_before = len(n1.peers())
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await asyncio.sleep(1.0)
        assert len(n1.peers()) == peers_before, "Peer count unchanged"
    finally:
        await cleanup(n1, t1, n2, t2)


# Direct Connection Failure Handling


@pytest.mark.asyncio
async def test_direct_connection_refused():
    """Direct connection to unreachable host fails gracefully."""
    n1 = ZreNode("FAIL1")
    await n1.start()
    t1 = asyncio.create_task(n1.run())
    try:
        await n1.connect_peer("192.0.2.1", 9999)
        await asyncio.sleep(2.0)
        assert len(n1.peers()) == 0, "FAIL1 should have no peers after failed connect"
    finally:
        await cleanup(n1, t1)


@pytest.mark.asyncio
async def test_direct_connection_invalid_port():
    """Direct connection to invalid port fails gracefully."""
    n1 = ZreNode("FAIL2")
    await n1.start()
    t1 = asyncio.create_task(n1.run())
    try:
        await n1.connect_peer("127.0.0.1", 1)
        await asyncio.sleep(2.0)
        assert len(n1.peers()) == 0, "FAIL2 should have no peers"
    finally:
        await cleanup(n1, t1)


# Edge Cases


@pytest.mark.asyncio
async def test_direct_simultaneous_bidirectional_connect():
    """Both sides calling connect_peer at once ends with 1 peer each."""
    n1 = ZreNode("SB1")
    n2 = ZreNode("SB2")
    n1.set_port(19813)
    n2.set_port(19814)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    try:
        await asyncio.gather(
            n1.connect_peer("127.0.0.1", n2._inbox_port),
            n2.connect_peer("127.0.0.1", n1._inbox_port),
        )
        e1 = await wait_event(n1, "ENTER", timeout=8.0)
        e2 = await wait_event(n2, "ENTER", timeout=8.0)
        assert e1 is not None, "SB1 should see ENTER"
        assert e2 is not None, "SB2 should see ENTER"
        await asyncio.sleep(1.0)
        assert len(n1.peers()) == 1, f"SB1 should have 1 peer, got {n1.peers()}"
        assert len(n2.peers()) == 1, f"SB2 should have 1 peer, got {n2.peers()}"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_direct_whisper_unknown_peer_no_crash():
    """WHISPER to an unknown peer id is dropped silently."""
    n1 = ZreNode("WU1")
    n1.set_port(19815)
    await n1.start()
    t1 = asyncio.create_task(n1.run())
    try:
        await n1.whisper("0" * 32, b"nobody home")
        await asyncio.sleep(1.0)
        assert len(n1.peers()) == 0
    finally:
        await cleanup(n1, t1)


@pytest.mark.asyncio
async def test_direct_connect_unresolvable_host():
    """connect_peer to an unresolvable hostname fails gracefully."""
    n1 = ZreNode("UH1")
    n1.set_port(19816)
    await n1.start()
    t1 = asyncio.create_task(n1.run())
    try:
        await n1.connect_peer("no-such-host.invalid", 9999)
        await asyncio.sleep(2.0)
        assert len(n1.peers()) == 0, "UH1 should have no peers"
    finally:
        await cleanup(n1, t1)


@pytest.mark.asyncio
async def test_direct_leave_event_delivered():
    """LEAVE event is delivered over a direct connection."""
    n1, n2, t1, t2 = await two_nodes_direct(name1="LV1", name2="LV2")
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        await wait_event(n1, "ENTER", timeout=5.0)
        await n1.join(b"CHAT")
        await n2.join(b"CHAT")
        await wait_event(n2, "JOIN", timeout=5.0)
        drain(n1)
        drain(n2)
        await n1.leave(b"CHAT")
        event = await wait_event(n2, "LEAVE", timeout=5.0)
        assert event is not None, "LV2 should see LEAVE"
        assert event["group"] == "CHAT"
    finally:
        await cleanup(n1, t1, n2, t2)


@pytest.mark.asyncio
async def test_duplicate_hello_no_loop():
    """Duplicate HELLOs from a live peer cause no ENTER/EXIT storm."""
    n1 = ZreNode("DL1")
    n2 = ZreNode("DL2")
    n1.set_port(19819)
    n2.set_port(19820)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    try:
        await n1.connect_peer("127.0.0.1", n2._inbox_port)
        event = await wait_event(n1, "ENTER", timeout=8.0)
        assert event is not None, "DL1 should see ENTER"
        drain(n1)
        drain(n2)

        for _pid, peer in n2._peers.items():
            for _ in range(5):
                peer.send(
                    Codec.encode_hello(
                        1,
                        f"tcp://127.0.0.1:{n2._inbox_port}".encode(),
                        [],
                        0,
                        n2.name,
                        {},
                    )
                )
        await asyncio.sleep(2.0)

        kinds = [ev["type"] for ev in drain(n1)]
        assert "ENTER" not in kinds, f"no re-ENTER expected, got {kinds}"
        assert "EXIT" not in kinds, f"no EXIT expected, got {kinds}"
        assert len(n1.peers()) == 1, f"DL1 should still have 1 peer, got {n1.peers()}"
        assert len(n2.peers()) == 1, f"DL2 should still have 1 peer, got {n2.peers()}"
    finally:
        await cleanup(n1, t1, n2, t2)


class TestAdvertisedEndpoint:
    def test_rejects_non_tcp(self):
        node = ZreNode("adv-bad")
        with pytest.raises(ValueError):
            node.set_advertised_endpoint("udp://1.2.3.4:5")

    def test_rejects_after_start(self):
        async def check():
            node = ZreNode("adv-late")
            node.set_port(19817)
            await node.start()
            try:
                with pytest.raises(RuntimeError):
                    node.set_advertised_endpoint("tcp://1.2.3.4:5")
            finally:
                await node.stop()

        asyncio.run(check())

    def test_public_endpoint_prefers_advertised(self):
        async def check():
            node = ZreNode("adv-use")
            node.set_port(19818)
            node.set_advertised_endpoint("tcp://203.0.113.50:9090")
            await node.start()
            try:
                assert node._public_endpoint_for("10.0.0.9") == (
                    b"tcp://203.0.113.50:9090"
                )
            finally:
                await node.stop()

        asyncio.run(check())
