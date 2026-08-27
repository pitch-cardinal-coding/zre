"""
Comprehensive tests for zyre_py.

Tests:
  - Codec encode/decode round-trip
  - Single node lifecycle
  - Two-node discovery and messaging
  - Multi-node stress (5+ nodes)
  - Join/leave groups
  - Whisper (direct message)
  - Shout (group broadcast)
  - Clean shutdown
"""

import asyncio
import os
import struct
import sys
import time

import pytest
import zmq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zre.node import (
    HELLO,
    JOIN,
    LEAVE,
    PING,
    PING_OK,
    SHOUT,
    ZRE_SIGNATURE,
    ZRE_VERSION,
    Codec,
    Group,
    Peer,
    ZreNode,
)


def drain(node):
    """Drain all events from a node's queue."""
    events = []
    while not node._event_queue.empty():
        try:
            events.append(node._event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


async def wait_event(node, event_type, timeout=5.0):
    """Wait for a specific event type, return it or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in drain(node):
            if e.get("type") == event_type:
                return e
        await asyncio.sleep(0.05)
    return None


async def wait_event_count(node, event_type, count, timeout=5.0):
    """Wait until `count` events of a given type have been received."""
    found = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in drain(node):
            if e.get("type") == event_type:
                found.append(e)
                if len(found) >= count:
                    return found
        await asyncio.sleep(0.05)
    return found


async def two_nodes(name1="A", name2="B"):
    """Create and start two connected nodes."""
    n1 = ZreNode(name1)
    n2 = ZreNode(name2)
    await n1.start()
    await n2.start()
    t1 = asyncio.create_task(n1.run())
    t2 = asyncio.create_task(n2.run())
    await asyncio.sleep(5)
    return n1, n2, t1, t2


async def cleanup(n1, n2, t1, t2):
    """Clean stop of two nodes."""
    n1._running = False
    n2._running = False
    await asyncio.sleep(0.3)
    t1.cancel()
    t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    await n1.stop()
    await n2.stop()


# ══════════════════════════════════════════════════════════════
#  Codec Tests
# ══════════════════════════════════════════════════════════════


class TestCodec:
    def test_encode_decode_hello(self):
        ep = b"tcp://192.168.1.1:5555"
        groups = [b"CHAT", b"GLOBAL"]
        headers = {b"X-ROLE": b"worker", b"X-VER": b"1.0"}
        data = Codec.encode_hello(1, ep, groups, 2, b"alice", headers)
        cmd, ver, seq, extra = Codec.decode(data)
        assert cmd == HELLO
        assert ver == ZRE_VERSION
        assert seq == 1
        assert extra["endpoint"] == ep
        assert extra["groups"] == groups
        assert extra["status"] == 2
        assert extra["name"] == b"alice"
        assert extra["headers"] == headers

    def test_encode_decode_shout(self):
        data = Codec.encode_simple(SHOUT, 42, b"CHAT")
        cmd, _ver, seq, extra = Codec.decode(data)
        assert cmd == SHOUT
        assert seq == 42
        assert extra["group"] == b"CHAT"

    def test_encode_decode_join(self):
        data = Codec.encode_simple(JOIN, 5, b"GLOBAL", 3)
        cmd, _ver, _seq, extra = Codec.decode(data)
        assert cmd == JOIN
        assert extra["group"] == b"GLOBAL"
        assert extra["status"] == 3

    def test_encode_decode_leave(self):
        data = Codec.encode_simple(LEAVE, 7, b"CHAT", 4)
        cmd, _ver, _seq, extra = Codec.decode(data)
        assert cmd == LEAVE
        assert extra["group"] == b"CHAT"
        assert extra["status"] == 4

    def test_encode_decode_ping(self):
        data = Codec.encode_simple(PING, 10)
        cmd, _ver, seq, _extra = Codec.decode(data)
        assert cmd == PING
        assert seq == 10

    def test_encode_decode_ping_ok(self):
        data = Codec.encode_simple(PING_OK, 11)
        cmd, _ver, seq, _extra = Codec.decode(data)
        assert cmd == PING_OK
        assert seq == 11

    def test_decode_bad_signature(self):
        assert Codec.decode(b"\x00\x00" + bytes(10)) is None

    def test_decode_bad_version(self):
        data = struct.pack("!HBBH", ZRE_SIGNATURE, HELLO, 99, 1)
        assert Codec.decode(data) is None

    def test_decode_truncated(self):
        assert Codec.decode(b"\xaa\xa1") is None

    def test_hello_empty_groups(self):
        data = Codec.encode_hello(1, b"tcp://1.2.3.4:5", [], 0, b"n", {})
        cmd, _ver, _seq, extra = Codec.decode(data)
        assert cmd == HELLO
        assert extra["groups"] == []

    def test_hello_empty_headers(self):
        data = Codec.encode_hello(1, b"tcp://1.2.3.4:5", [b"G"], 0, b"n", {})
        _cmd, _ver, _seq, extra = Codec.decode(data)
        assert extra["headers"] == {}

    def test_string_encoding(self):
        buf = Codec._s(b"", b"hello")
        assert buf == b"\x05hello"

    def test_long_string_encoding(self):
        buf = Codec._l(b"", b"world")
        assert buf == b"\x00\x00\x00\x05world"


# ══════════════════════════════════════════════════════════════
#  Peer Tests
# ══════════════════════════════════════════════════════════════


class TestPeer:
    def test_peer_init(self):
        p = Peer("abcd1234", "127.0.0.1", 9999)
        assert p.uuid_hex == "abcd1234"
        assert p.addr == "127.0.0.1"
        assert p.port == 9999
        assert not p.connected
        assert not p.ready

    def test_peer_disconnect(self):
        ctx = zmq.Context()
        p = Peer("abcd1234", "127.0.0.1", 9999)
        p.dealer = ctx.socket(zmq.DEALER)
        p.connected = True
        p.disconnect()
        assert not p.connected
        assert p.dealer is None
        ctx.term()

    def test_peer_refresh(self):
        p = Peer("abcd1234", "127.0.0.1", 9999)
        now = time.monotonic()
        p.refresh(now, 5000, 30000)
        assert p.evasive_at == now + 5.0
        assert p.expired_at == now + 30.0

    def test_peer_check_seq(self):
        p = Peer("abcd1234", "127.0.0.1", 9999)
        assert p.check_seq(HELLO, 1) is False
        assert p.check_seq(SHOUT, 2) is False
        assert p.check_seq(SHOUT, 4) is True


# ══════════════════════════════════════════════════════════════
#  Group Tests
# ══════════════════════════════════════════════════════════════


class TestGroup:
    def test_group_join_leave(self):
        g = Group(b"CHAT")
        p1 = Peer("aaa", "127.0.0.1", 1)
        p2 = Peer("bbb", "127.0.0.1", 2)
        g.join(p1)
        g.join(p2)
        assert len(g.peers) == 2
        g.leave(p1)
        assert len(g.peers) == 1
        assert "bbb" in g.peers

    def test_group_leave_nonexistent(self):
        g = Group(b"CHAT")
        p = Peer("aaa", "127.0.0.1", 1)
        g.leave(p)
        assert len(g.peers) == 0


# ══════════════════════════════════════════════════════════════
#  Node Lifecycle Tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_node_start_stop():
    node = ZreNode("test-start-stop")
    await node.start()
    assert node._running
    assert node._inbox_port > 0
    assert node.peer_id_hex
    await node.stop()


@pytest.mark.asyncio
async def test_node_name():
    node = ZreNode("myname")
    assert node.name == b"myname"
    await node.stop()


@pytest.mark.asyncio
async def test_node_default_name():
    node = ZreNode()
    assert len(node.name) == 6
    await node.stop()


@pytest.mark.asyncio
async def test_node_set_header():
    node = ZreNode("hdr-test")
    node.set_header("X-ROLE", "worker")
    assert node._headers[b"X-ROLE"] == b"worker"
    await node.stop()


# ══════════════════════════════════════════════════════════════
#  Two-Node Tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_node_discovery():
    """Two nodes discover each other via UDP beacon."""
    n1, n2, t1, t2 = await two_nodes("ALICE", "BOB")
    assert len(n1.peers()) == 1, f"ALICE expected 1 peer, got {len(n1.peers())}"
    assert len(n2.peers()) == 1, f"BOB expected 1 peer, got {len(n2.peers())}"
    await cleanup(n1, n2, t1, t2)


@pytest.mark.asyncio
async def test_two_node_enter_events():
    """Both nodes receive ENTER events."""
    n1, n2, t1, t2 = await two_nodes("AE1", "BE1")
    e1 = await wait_event(n1, "ENTER")
    e2 = await wait_event(n2, "ENTER")
    assert e1 is not None, "ALICE should receive ENTER"
    assert e2 is not None, "BOB should receive ENTER"
    assert e1["peer_name"] == "BE1"
    assert e2["peer_name"] == "AE1"
    await cleanup(n1, n2, t1, t2)


@pytest.mark.asyncio
async def test_two_node_join_events():
    """JOIN event emitted when a peer joins a group."""
    n1, n2, t1, t2 = await two_nodes("JJ1", "JJ2")
    await n1.join(b"CHAT")
    e = await wait_event(n2, "JOIN")
    assert e is not None, "BOB should see JOIN"
    assert e["peer_name"] == "JJ1"
    assert e["group"] == "CHAT"
    await cleanup(n1, n2, t1, t2)


@pytest.mark.asyncio
async def test_two_node_leave_events():
    """LEAVE event emitted when a peer leaves a group."""
    n1, n2, t1, t2 = await two_nodes("LL1", "LL2")
    await n1.join(b"CHAT")
    await wait_event(n2, "JOIN")
    await n1.leave(b"CHAT")
    e = await wait_event(n2, "LEAVE")
    assert e is not None, "BOB should see LEAVE"
    assert e["group"] == "CHAT"
    await cleanup(n1, n2, t1, t2)


# ══════════════════════════════════════════════════════════════
#  Multi-Node Stress Tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_five_node_mesh():
    """5 nodes form a full mesh."""
    nodes = [ZreNode(f"mesh{i}") for i in range(5)]
    for n in nodes:
        await n.start()
        await n.join(b"MESH")
    tasks = [asyncio.create_task(n.run()) for n in nodes]
    await asyncio.sleep(8)
    for n in nodes:
        assert len(n.peers()) == 4, f"{n.name.decode()} expected 4 peers, got {len(n.peers())}"
    for n in nodes:
        n._running = False
    await asyncio.sleep(0.3)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for n in nodes:
        await n.stop()


@pytest.mark.asyncio
async def test_ten_node_mesh():
    """10 nodes form a full mesh."""
    nodes = [ZreNode(f"big{i}") for i in range(10)]
    for n in nodes:
        await n.start()
        await n.join(b"GLOBAL")
    tasks = [asyncio.create_task(n.run()) for n in nodes]
    await asyncio.sleep(10)
    for n in nodes:
        assert len(n.peers()) == 9, f"{n.name.decode()} expected 9 peers, got {len(n.peers())}"
    for n in nodes:
        n._running = False
    await asyncio.sleep(0.3)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for n in nodes:
        await n.stop()


# ══════════════════════════════════════════════════════════════
#  Messaging Tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_shout_delivered():
    """SHOUT delivered to all group members."""
    n1, n2, t1, t2 = await two_nodes("SH1", "SH2")
    await n1.join(b"CHAT")
    await n2.join(b"CHAT")
    # let joins propagate
    await asyncio.sleep(1)

    # Clear
    drain(n1)
    drain(n2)

    await n1.shout(b"CHAT", b"Hello World!")
    e = await wait_event(n2, "SHOUT")
    assert e is not None, "BOB should receive SHOUT"
    assert e["payload"] == b"Hello World!"
    assert e["peer_name"] == "SH1"
    assert e["group"] == "CHAT"
    await cleanup(n1, n2, t1, t2)


@pytest.mark.asyncio
async def test_whisper_delivered():
    """WHISPER delivered to specific peer."""
    n1, n2, t1, t2 = await two_nodes("WH1", "WH2")
    drain(n1)
    drain(n2)

    peers = n1.peers()
    assert len(peers) == 1
    await n1.whisper(peers[0], b"Secret msg")
    e = await wait_event(n2, "WHISPER")
    assert e is not None, "BOB should receive WHISPER"
    assert e["payload"] == b"Secret msg"
    await cleanup(n1, n2, t1, t2)


@pytest.mark.asyncio
async def test_shout_multiple_messages():
    """Multiple SHOUT messages are all delivered."""
    n1, n2, t1, t2 = await two_nodes("SM1", "SM2")
    await n1.join(b"CHAT")
    await n2.join(b"CHAT")
    await asyncio.sleep(1)
    drain(n1)
    drain(n2)

    for i in range(5):
        await n1.shout(b"CHAT", f"msg-{i}".encode())
    await asyncio.sleep(1)

    shouts = await wait_event_count(n2, "SHOUT", 5, timeout=3)
    assert len(shouts) >= 5, f"Expected 5 SHOUTs, got {len(shouts)}"
    await cleanup(n1, n2, t1, t2)


# ══════════════════════════════════════════════════════════════
#  Shutdown Tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_exit_events_on_shutdown():
    """EXIT events emitted when node stops."""
    n1, n2, t1, t2 = await two_nodes("EX1", "EX2")
    assert len(n1.peers()) == 1
    assert len(n2.peers()) == 1

    n1._running = False
    e = await wait_event(n2, "EXIT")
    assert e is not None, "BOB should see EXIT"
    assert e["peer_name"] == "EX1"
    n2._running = False
    await asyncio.sleep(0.3)
    t1.cancel()
    t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    await n1.stop()
    await n2.stop()
