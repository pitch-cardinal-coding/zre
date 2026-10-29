"""
Pure Python ZRE (RFC 36) — Asyncio + pyzmq.

Architecture:
  - Single asyncio event loop (no threads, no race conditions)
  - Uses zmq.Context (sync) for sockets + zmq.NOBLOCK for non-blocking
 - asyncio loop for scheduling, NOT zmq.asyncio
 - UDP beacon: non-blocking socket + asyncio loop.sock_recvfrom
 - poll(inbox | beacon | api_pipe) main loop with timeout

Usage:
    node = ZreNode("my-app")
    await node.start()
    await node.join("CHAT")

    async for event in node.events():
        print(event)

    await node.shout("CHAT", b"Hello!")
    await node.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import socket
import struct
import uuid as _uuid_mod
from collections.abc import AsyncIterator

import zmq
from zmq.utils import z85

from .gossip import (
    ZGS_HELLO,
    ZGS_INVALID,
    ZGS_PING,
    ZGS_PUBLISH,
    GossipHub,
    zgs_decode,
    zgs_encode,
)

ZRE_SIGNATURE = 0xAAA0 | 1  # 0xAAA1 on wire, per RFC 36
ZRE_VERSION = 2
HELLO, WHISPER, SHOUT, JOIN, LEAVE, PING, PING_OK, ELECT, LEADER, GOODBYE = range(1, 11)
# RFC 36 section "Node Discovery and Presence": every ZRE node listens to and
# broadcasts on UDP 5670 (ZRE-DISC, IANA assigned). libzyre uses the same value
# (zyre_library.h: ZRE_DISCOVERY_PORT 5670), so anything else here would put a
# zre node and a libzyre node on two different, non-overlapping clusters.
BEACON_PORT = 5670
BEACON_SIZE = 22
BEACON_SIZE_V3 = 54
BEACON_VERSION_V1 = 0x01
BEACON_VERSION_V3 = 0x03
# zre_msg.h caps every string field (endpoint, name, group, challenger/leader
# id) in a char[256], so the one-octet length prefix on the wire can never
# carry more than 255 bytes for a libzyre peer. Keep our own values in range.
MAX_STRING_BYTES = 255
PUBLIC_KEY_HEADER = b"X-PUBLICKEY"

# ioctl numbers for interface address/netmask lookup (Linux, stable ABI).
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
DEFAULT_EV_MS = 5000
DEFAULT_EX_MS = 30000
REAP_INTERVAL_MS = 1000


def _wire_string(label: str, value: str | bytes) -> bytes:
    """Encode a short-string field, rejecting values libzyre would truncate.

    Group, name, endpoint and the election identities travel in `char[256]`
    fields on the C side (zre_msg.c), so anything over 255 bytes is silently
    cut there. Fail loudly here instead of sending a message the far side
    decodes as something else.
    """
    raw = value.encode() if isinstance(value, str) else value
    if len(raw) > MAX_STRING_BYTES:
        raise ValueError(
            f"{label} must be at most {MAX_STRING_BYTES} bytes, got {len(raw)}"
        )
    return raw


def _tcp_address(addr: str, port: int) -> str:
    host = addr.strip("[]")
    if ":" in host and "%" not in host:
        return f"tcp://[{host}]:{port}"
    return f"tcp://{addr}:{port}"


def _split_endpoint(ep_str: str) -> tuple[str, int]:
    without_scheme = ep_str.split("://", 1)[1]
    if without_scheme.startswith("["):
        host, _, remainder = without_scheme[1:].partition("]")
        return host, int(remainder.lstrip(":"))
    host, _, port = without_scheme.rpartition(":")
    # Strip a link-local zone id for parsing; dialing keeps it.
    return host.split("%", 1)[0], int(port)


class ZreError(Exception):
    """Base class for zre errors."""


class UUIDCollisionError(ZreError):
    """Another node on the network is running with our stable UUID.

    Raised from run() when a peer announces our own peer id with a
    different TCP endpoint — the signature of a second node started with
    the same --uuid / set_uuid() value. Stable UUIDs must be unique among
    concurrently running peers. Every node sharing the uuid stops itself:
    leaving one alive would let whispers route to either node.
    """


class Codec:
    @staticmethod
    def _s(buf: bytes, value: bytes | str) -> bytes:
        if isinstance(value, str):
            value = value.encode()
        return buf + bytes([len(value)]) + value

    @staticmethod
    def _l(buf: bytes, value: bytes | str) -> bytes:
        if isinstance(value, str):
            value = value.encode()
        return buf + struct.pack("!I", len(value)) + value

    @staticmethod
    def _rs(data: bytes, offset: int) -> tuple:
        if offset >= len(data):
            return None, offset
        length = data[offset]
        offset += 1
        end = offset + length
        return (data[offset:end], end) if end <= len(data) else (None, offset)

    @staticmethod
    def _rl(data: bytes, offset: int) -> tuple:
        if offset + 4 > len(data):
            return None, offset
        length = struct.unpack("!I", data[offset : offset + 4])[0]
        offset += 4
        end = offset + length
        return (data[offset:end], end) if end <= len(data) else (None, offset)

    @staticmethod
    def encode_hello(
        seq: int,
        endpoint: bytes | str,
        groups: list | None,
        status: int,
        name: bytes | str,
        headers: dict | None,
    ) -> bytes:
        buf = struct.pack("!HBBH", ZRE_SIGNATURE, HELLO, ZRE_VERSION, seq)
        buf = Codec._s(buf, endpoint)
        group_list = list(groups or [])
        buf += struct.pack("!I", len(group_list))
        for group in group_list:
            buf = Codec._l(buf, group)
        buf += struct.pack("B", status)
        buf = Codec._s(buf, name)
        hdr = headers or {}
        buf += struct.pack("!I", len(hdr))
        for key, value in hdr.items():
            buf = Codec._s(buf, key)
            buf = Codec._l(buf, value)
        return buf

    @staticmethod
    def encode_simple(
        cmd: int, seq: int, group: bytes | None = None, status: int | None = None
    ) -> bytes:
        buf = struct.pack("!HBBH", ZRE_SIGNATURE, cmd, ZRE_VERSION, seq)
        if group is not None:
            buf = Codec._s(buf, group)
        if status is not None:
            buf += struct.pack("B", status)
        return buf

    @staticmethod
    def encode_election(
        cmd: int, seq: int, group: bytes | str, peer_id: bytes | str
    ) -> bytes:
        buf = struct.pack("!HBBH", ZRE_SIGNATURE, cmd, ZRE_VERSION, seq)
        buf = Codec._s(buf, group)
        buf = Codec._s(buf, peer_id)
        return buf

    @staticmethod
    def decode(data: bytes) -> tuple | None:
        if len(data) < 6:
            return None
        sig = struct.unpack("!H", data[0:2])[0]
        if sig != ZRE_SIGNATURE:
            return None
        cmd, ver = data[2], data[3]
        if ver != ZRE_VERSION:
            return None
        seq = struct.unpack("!H", data[4:6])[0]
        offset = 6
        fields = {}
        if cmd == HELLO:
            endpoint, offset = Codec._rs(data, offset)
            if endpoint is None:
                return None
            if offset + 4 > len(data):
                return None
            group_count = struct.unpack("!I", data[offset : offset + 4])[0]
            offset += 4
            groups = []
            for _ in range(group_count):
                if offset + 4 > len(data):
                    return None
                group, offset = Codec._rl(data, offset)
                if group is None:
                    return None
                groups.append(group)
            if offset >= len(data):
                return None
            status = data[offset]
            offset += 1
            name, offset = Codec._rs(data, offset)
            if name is None:
                return None
            if offset + 4 > len(data):
                return None
            header_count = struct.unpack("!I", data[offset : offset + 4])[0]
            offset += 4
            headers = {}
            for _ in range(header_count):
                key, offset = Codec._rs(data, offset)
                if key is None:
                    return None
                value, offset = Codec._rl(data, offset)
                if value is None:
                    return None
                headers[key] = value
            fields = {
                "endpoint": endpoint,
                "groups": groups,
                "status": status,
                "name": name,
                "headers": headers,
            }
        elif cmd in (SHOUT, JOIN, LEAVE):
            group, offset = Codec._rs(data, offset)
            if group is None:
                return None
            fields["group"] = group
            if cmd in (JOIN, LEAVE):
                if offset >= len(data):
                    return None
                fields["status"] = data[offset]
        elif cmd in (ELECT, LEADER):
            group, offset = Codec._rs(data, offset)
            if group is None:
                return None
            fields["group"] = group
            challenger, offset = Codec._rs(data, offset)
            if challenger is None:
                return None
            fields["challenger" if cmd == ELECT else "leader"] = challenger
        return cmd, ver, seq, fields


class Peer:
    __slots__ = (
        "addr",
        "connected",
        "dealer",
        "endpoint_str",
        "evasive_at",
        "expired_at",
        "groups",
        "headers",
        "name",
        "port",
        "ready",
        "sent_seq",
        "status",
        "uuid_hex",
        "want_seq",
    )

    def __init__(self, uuid_hex: str, addr: str, port: int) -> None:
        self.uuid_hex = uuid_hex
        self.addr = addr
        self.port = port
        self.endpoint_str = _tcp_address(addr, port)
        self.name = None
        self.headers = {}
        self.groups = set()
        self.status = 0
        self.connected = False
        self.ready = False
        self.dealer = None
        self.sent_seq = 0
        self.want_seq = 0
        self.evasive_at = 0.0
        self.expired_at = 0.0

    def connect(
        self,
        ctx: zmq.Context,
        our_uuid: bytes,
        server_key: bytes | None = None,
        public_key: bytes | None = None,
        secret_key: bytes | None = None,
    ) -> bool:
        if self.connected:
            return True
        self.dealer = ctx.socket(zmq.DEALER)
        self.dealer.setsockopt(zmq.IDENTITY, b"\x01" + our_uuid)
        self.dealer.setsockopt(zmq.SNDHWM, 1000)
        self.dealer.setsockopt(zmq.SNDTIMEO, 0)
        self.dealer.setsockopt(zmq.LINGER, 0)
        if ":" in self.addr.strip("[]"):
            self.dealer.setsockopt(zmq.IPV6, 1)
        if server_key is not None:
            if public_key is None or secret_key is None:
                with contextlib.suppress(Exception):
                    self.dealer.close(linger=0)
                self.dealer = None
                return False
            self.dealer.setsockopt(zmq.CURVE_PUBLICKEY, public_key)
            self.dealer.setsockopt(zmq.CURVE_SECRETKEY, secret_key)
            self.dealer.setsockopt(zmq.CURVE_SERVERKEY, server_key)
        try:
            self.dealer.connect(_tcp_address(self.addr, self.port))
            self.connected = True
            return True
        except zmq.ZMQError:
            with contextlib.suppress(Exception):
                self.dealer.close(linger=0)
            self.dealer = None
            return False

    def disconnect(self) -> None:
        if self.dealer:
            with contextlib.suppress(Exception):
                self.dealer.close(linger=0)
            self.dealer = None
        self.connected = False
        self.ready = False

    def next_seq(self) -> int:
        """Consume and return this peer's next outgoing sequence number.

        RFC 36: "The first message from a peer (HELLO) MUST have sequence
        number 1, and every message must have a strictly incrementing
        sequence number." The counter is per connection, not per node:
        libzyre keeps it in zyre_peer_t::sent_sequence and *drops* the peer
        when the numbers it receives do not match what it expects
        (zyre_peer.c:495 messages_lost -> zyre_node.c:1121 remove_peer). A
        node-wide counter therefore breaks every libzyre peer as soon as a
        second peer joins, so it must not be used here.
        """
        self.sent_seq = (self.sent_seq + 1) & 0xFFFF
        return self.sent_seq

    def send(self, data: bytes, more: list | None = None) -> bool:
        """Send an already encoded frame. No sequence bookkeeping."""
        if not self.connected or self.dealer is None:
            return False
        try:
            self.dealer.send_multipart([data] + (more or []), zmq.NOBLOCK)
            return True
        except zmq.ZMQError:
            return False

    def send_encoded(self, encode, more: list | None = None) -> bool:
        """Stamp this peer's sequence number, encode, then send.

        `encode` receives the sequence number and returns the frame bytes.
        Messages that could not be queued are dropped and the peer is torn
        down, mirroring zyre_peer_send()'s EAGAIN handling.
        """
        if not self.connected or self.dealer is None:
            return False
        frame = encode(self.next_seq())
        try:
            self.dealer.send_multipart([frame] + (more or []), zmq.NOBLOCK)
            return True
        except zmq.Again:
            self.disconnect()
            return False
        except zmq.ZMQError:
            return False

    def send_cmd(
        self,
        cmd: int,
        group: bytes | None = None,
        status: int | None = None,
        more: list | None = None,
    ) -> bool:
        return self.send_encoded(
            lambda seq: Codec.encode_simple(cmd, seq, group, status), more
        )

    def send_election(self, cmd: int, group: bytes, identity: bytes) -> bool:
        return self.send_encoded(
            lambda seq: Codec.encode_election(cmd, seq, group, identity)
        )

    def refresh(self, now: float, ev_ms: int, ex_ms: int) -> None:
        self.evasive_at = now + ev_ms / 1000.0
        self.expired_at = now + ex_ms / 1000.0

    def check_seq(self, cmd: int, seq: int) -> bool:
        self.want_seq = 1 if cmd == HELLO else self.want_seq + 1
        return self.want_seq != seq


class Group:
    __slots__ = ("contest", "election", "leader", "name", "peers")

    def __init__(self, name: bytes) -> None:
        self.name = name
        self.peers = {}
        self.contest = False
        self.election: dict | None = None
        self.leader: str | None = None

    def join(self, peer: Peer) -> None:
        self.peers[peer.uuid_hex] = peer

    def leave(self, peer: Peer) -> None:
        self.peers.pop(peer.uuid_hex, None)

    def send(self, encode, more: list | None = None) -> None:
        """Send one logical message to every peer in the group.

        `encode(seq)` is called once per peer so each DEALER gets its own
        sequence number, the way libzyre's zyre_group_send() duplicates the
        message per peer and lets zyre_peer_send() stamp the sequence.
        """
        for peer in list(self.peers.values()):
            if peer.connected:
                peer.send_encoded(encode, more)


class ZreNode:
    """
    Async ZRE node. Single asyncio event loop, no threads.

    Uses zmq.Context (sync) with zmq.NOBLOCK for non-blocking I/O.
    This avoids all zmq.asyncio issues. asyncio is used for scheduling
    and non-blocking UDP reads only.

    Main loop: poll(inbox | beacon | api_pipe) with timeout,
    then process whichever socket is ready.
    """

    def __init__(self, name=None):
        if name is None:
            name = _uuid_mod.uuid4().hex[:6]
        # Matches libzyre: the default name is the first 6 characters of the
        # node UUID, which is stable and readable in both logs.
        self.name = _wire_string("name", name)
        self.peer_id = _uuid_mod.uuid4().bytes
        self.peer_id_hex = self.peer_id.hex()
        self._groups: dict[bytes, Group] = {}
        self._own_groups: list[bytes] = []
        self._contest_groups: set[bytes] = set()
        self._peers: dict[str, Peer] = {}
        self._pending: dict[str, Peer] = {}
        self._headers: dict[bytes, bytes] = {}
        # Group status byte: bumped on every own join/leave, mirrored by
        # libzyre (zyre_node.c JOIN/LEAVE: ++self->status) and asserted
        # against the peer's own count with a `byte` on the C side.
        self._status = 0
        self._ev_timeout = DEFAULT_EV_MS
        self._ex_timeout = DEFAULT_EX_MS
        # EVASIVE re-emit throttle: an idle peer is probed every reap cycle
        # (~1 s) once it goes quiet; without throttling the application sees
        # an EVASIVE event per cycle for as long as the peer stays idle.
        self._evasive_emit_interval = 20.0
        self._evasive_emit_at: dict[str, float] = {}
        self._beacon_port = BEACON_PORT
        self._beacon_interval = 1.0
        self._last_beacon = 0.0
        self._beacon_msg: bytes | None = None
        # None = all interfaces, or IP / iface name
        self._interface = None
        # fixed ROUTER port if set
        self._fixed_port = None
        # public endpoint advertised in HELLO (NAT/port-forward workaround:
        # peers dial this address instead of our bound one)
        self._advertised_endpoint = None
        self._curve_public: bytes | None = None
        self._curve_secret: bytes | None = None
        # Curve nodes broadcast v3 beacons (public key appended), plain
        # nodes v1. libzyre tosses any beacon whose version differs from
        # its own beacon_version (zyre_node.c recv_beacon), so the same rule
        # keeps a mixed-secure cluster from splitting in two.
        self._beacon_version = BEACON_VERSION_V1
        self._zap_domain = "global"
        self._ipv6 = False
        # Peer public keys learned from v3 beacons, gossip announcements and
        # HELLO headers, keyed by peer id and by "host:port".
        self._peer_server_keys: dict[str, bytes] = {}
        self._gossip_bind: str | None = None
        self._gossip_connects: list[str] = []
        # ZGS.1 gossip service (zgossip wire-compatible): one ROUTER hub and
        # DEALER client sockets, all served from the main poller — the same
        # shape zyre uses with its zgossip delegate actor.
        self._gossip_hub: GossipHub | None = None
        self._gossip_socks: list[zmq.Socket] = []
        # Tuples to re-publish after a (re)connect: our own announcement plus
        # everything learned from the hub — zgossip.c re-publishes "for each
        # tuple we have" after every HELLO.
        self._gossip_tuples: dict[bytes, tuple[bytes, int]] = {}
        self._gossip_targets: dict[zmq.Socket, tuple[str, int]] = {}
        self._gossip_last_rx: dict[zmq.Socket, float] = {}
        self._gossip_next_ping = 0.0
        self._verbose = False
        self._logger = logging.getLogger(f"zre.{self.peer_id_hex[:6]}")

        # Synchronous zmq.Context — all operations use zmq.NOBLOCK
        self._ctx = zmq.Context()
        # zmq.Socket (ROUTER), sync
        self._inbox = None
        self._inbox_port = 0
        self._beacon_sock = None
        self._beacon_fd = -1
        # Persistent beacon send socket + cached targets (built lazily on
        # first send, rebuilt on send failure).
        self._send_sock = None
        self._send_targets: list[tuple[str, int]] = []
        self._running = False
        # Queues are created lazily on first use: on Python <= 3.9,
        # asyncio.Queue() binds to the current event loop at __init__ time
        # and raises when constructed outside one. Every accessor below is
        # async (or called from within the running loop), so first access
        # always happens inside a loop. See _event_queue/_api_queue below.
        self._event_queue_store: asyncio.Queue | None = None
        self._api_queue_store: asyncio.Queue | None = None
        # Reusable poller
        self._inbox_poller = zmq.Poller()

    @property
    def _event_queue(self) -> asyncio.Queue:
        if self._event_queue_store is None:
            self._event_queue_store = asyncio.Queue(maxsize=1000)
        return self._event_queue_store

    @property
    def _api_queue(self) -> asyncio.Queue:
        if self._api_queue_store is None:
            self._api_queue_store = asyncio.Queue()
        return self._api_queue_store

    def _ensure_not_running(self, method: str) -> None:
        if self._running:
            raise RuntimeError(f"{method}() must be called before start()")

    def set_uuid(self, value: str | bytes) -> None:
        """Set a stable peer UUID (32-char hex string or 16 raw bytes).

        The UUID is the peer id used by whisper() and shown as peer_hex in
        the examples; by default it is random per process. Set a stable one
        so peer_hex survives restarts. Must be called before start().
        """
        self._ensure_not_running("set_uuid")
        if isinstance(value, bytes):
            raw = value
        else:
            try:
                raw = bytes.fromhex(value)
            except ValueError:
                raise ValueError(
                    "uuid must be a 32-char hex string or 16 raw bytes"
                ) from None
        if len(raw) != 16:
            raise ValueError("uuid must be exactly 16 bytes (32 hex chars)")
        if raw == b"\x00" * 16:
            raise ValueError("uuid must not be all zero")
        self.peer_id = raw
        self.peer_id_hex = raw.hex()

    def set_interface(self, iface: str) -> None:
        """Set network interface (e.g., 'eth0' or '192.168.1.100')."""
        self._ensure_not_running("set_interface")
        self._interface = iface

    def set_port(self, port: int) -> None:
        """Set UDP beacon port (default 5670). Use different port to isolate clusters."""
        self._ensure_not_running("set_port")
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1-65535")
        self._beacon_port = int(port)

    def set_interval(self, interval_ms: int) -> None:
        """Set beacon interval in milliseconds (default 1000)."""
        self._ensure_not_running("set_interval")
        if interval_ms <= 0:
            raise ValueError("interval must be >0")
        self._beacon_interval = interval_ms / 1000.0

    def set_evasive_timeout(self, timeout_ms: int) -> None:
        """Set evasive timeout in ms (default 5000)."""
        self._ensure_not_running("set_evasive_timeout")
        self._ev_timeout = int(timeout_ms)

    def set_silent_timeout(self, timeout_ms: int) -> None:
        self._ensure_not_running("set_silent_timeout")
        self._ev_timeout = int(timeout_ms)

    def set_expired_timeout(self, timeout_ms: int) -> None:
        """Set expired timeout in ms (default 30000)."""
        self._ensure_not_running("set_expired_timeout")
        self._ex_timeout = int(timeout_ms)

    def set_beacon_peer_port(self, port: int) -> None:
        """Set fixed TCP port for ROUTER socket (default ephemeral)."""
        self._ensure_not_running("set_beacon_peer_port")
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1-65535")
        self._fixed_port = int(port)

    def set_advertised_endpoint(self, endpoint: str) -> None:
        """Set public endpoint advertised in HELLO (NAT/port-forward setups)."""
        self._ensure_not_running("set_advertised_endpoint")
        if not endpoint.startswith("tcp://"):
            raise ValueError("advertised endpoint must look like tcp://host:port")
        _split_endpoint(endpoint)
        self._advertised_endpoint = _wire_string("endpoint", endpoint)

    @staticmethod
    def _curve_key_raw(value: bytes | str, label: str) -> bytes:
        if isinstance(value, str):
            value = value.encode()
        if len(value) == 32:
            return bytes(value)
        if len(value) == 40:
            try:
                return z85.decode(value)
            except ValueError:
                raise ValueError(f"{label} is not valid z85") from None
        raise ValueError(f"{label} must be 32 raw bytes or 40-char z85")

    def set_zcert(self, public_key: bytes | str, secret_key: bytes | str) -> None:
        self._ensure_not_running("set_zcert")
        self._curve_public = self._curve_key_raw(public_key, "public_key")
        self._curve_secret = self._curve_key_raw(secret_key, "secret_key")
        # libzyre publishes its Curve public key as an X-PUBLICKEY header
        # (zyre_node.c "SET PUBLICKEY" -> zhash_update(headers, ...)) and
        # *requires* it on an inbound HELLO: a HELLO without the header is
        # discarded to avoid a security downgrade. Mirror both halves, or a
        # libzyre peer never accepts our connection.
        self.set_header(PUBLIC_KEY_HEADER, z85.encode(self._curve_public))

    def set_zap_domain(self, domain: str) -> None:
        self._ensure_not_running("set_zap_domain")
        if not domain:
            raise ValueError("zap domain must not be empty")
        self._zap_domain = domain

    def set_ipv6(self, enabled: bool = True) -> None:
        self._ensure_not_running("set_ipv6")
        self._ipv6 = bool(enabled)

    @staticmethod
    def _split_hub_endpoint(endpoint: str) -> tuple[str, int]:
        without_scheme = endpoint.split("://", 1)[-1]
        host, _, port = without_scheme.rpartition(":")
        return host.strip("[]") or "127.0.0.1", int(port)

    def gossip_bind(self, endpoint: str) -> None:
        self._ensure_not_running("gossip_bind")
        self._gossip_bind = endpoint

    def gossip_connect(self, endpoint: str) -> None:
        self._ensure_not_running("gossip_connect")
        self._gossip_connects.append(endpoint)

    def gossip_connect_curve(self, public_key: bytes | str, endpoint: str) -> None:
        self._ensure_not_running("gossip_connect_curve")
        self._gossip_connects.append(endpoint)

    def gossip_unpublish(self, node_uuid: str) -> None:
        """Remove a tuple from our gossip tables.

        Mirrors zgossip's UNPUBLISH actor method (a local table delete —
        ZGS.1 has no forget command on the wire).
        """
        raw = node_uuid.encode() if isinstance(node_uuid, str) else node_uuid
        self._gossip_tuples.pop(raw, None)
        if self._gossip_hub is not None:
            self._gossip_hub.unpublish(raw)

    def _gossip_active(self) -> bool:
        return self._gossip_bind is not None or bool(self._gossip_connects)

    def _gossip_publish_endpoint(self, hub_host: str, hub_port: int) -> str:
        if self._advertised_endpoint:
            base = self._advertised_endpoint
        else:
            local_ip = self._resolve_interface_ip()
            if not local_ip:
                probe_host = hub_host
                probe_port = hub_port
                if probe_host in ("0.0.0.0", "*", ""):
                    probe_host, probe_port = self._gossip_probe_target()
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    probe.connect((probe_host, probe_port))
                    local_ip = probe.getsockname()[0]
                    if local_ip.startswith("127."):
                        local_ip = ""
                except OSError:
                    local_ip = ""
                finally:
                    probe.close()
            if not local_ip:
                local_ip = "127.0.0.1"
            base = _tcp_address(local_ip, self._inbox_port)
        if self._curve_public is not None:
            return f"{base}|{z85.encode(self._curve_public).decode()}"
        return base

    def set_verbose(self, verbose: bool = True) -> None:
        """Enable verbose logging."""
        self._verbose = bool(verbose)

    def set_contest_in_group(self, group: str | bytes) -> None:
        group_bytes = _wire_string("group", group)
        self._contest_groups.add(group_bytes)
        found = self._groups.get(group_bytes)
        if found is not None:
            found.contest = True
        level = logging.DEBUG if self._verbose else logging.WARNING
        logging.basicConfig(level=level)
        self._logger.setLevel(level)

    def _log(self, msg: str, *args: object) -> None:
        if self._verbose:
            self._logger.debug(msg, *args)

    def _curve_client_args(
        self, addr: str, port: int, rhex: str | None = None
    ) -> tuple[bytes | None, bytes | None, bytes | None]:
        if self._curve_secret is None:
            return None, None, None
        server_key = None
        if rhex is not None:
            server_key = self._peer_server_keys.get(rhex)
        if server_key is None:
            server_key = self._peer_server_keys.get(f"{addr}:{port}")
        if server_key is None:
            return None, None, None
        return server_key, self._curve_public, self._curve_secret

    def _build_beacon(self, port: int) -> bytes:
        if self._beacon_version == BEACON_VERSION_V3:
            return (
                b"ZRE"
                + bytes([BEACON_VERSION_V3])
                + self.peer_id
                + struct.pack("!H", port)
                + self._curve_public
            )
        return (
            b"ZRE" + bytes([BEACON_VERSION_V1]) + self.peer_id + struct.pack("!H", port)
        )

    def _resolve_interface_ip(self, iface: str | None = None) -> str:
        """Resolve an interface name to its IPv4 address; pass an IP as-is.

        With no argument the node's pinned interface (`set_interface`) is
        used. An empty result means "no pin" and callers fall back to their
        own defaults.
        """
        iface = self._interface if iface is None else iface
        if not iface:
            return ""
        # If it looks like an IP, return directly
        try:
            socket.inet_aton(iface)
            return iface
        except OSError:
            pass
        # Interface name (e.g. virbr0): resolve via ioctl SIOCGIFADDR.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = fcntl.ioctl(
                sock.fileno(),
                SIOCGIFADDR,
                struct.pack("256s", iface.encode()[:15]),
            )
            return socket.inet_ntoa(packed[20:24])
        except OSError:
            pass
        finally:
            sock.close()
        # Last resort: hostname lookup. Returns "" when unresolvable;
        # callers treat "" as "no pin".
        try:
            return socket.gethostbyname(iface)
        except Exception:
            return ""

    async def start(self) -> None:
        bind_addr = f"tcp://*:{self._fixed_port}" if self._fixed_port else "tcp://*:*"
        self._inbox = self._ctx.socket(zmq.ROUTER)
        self._inbox.setsockopt(zmq.ROUTER_MANDATORY, 1)
        self._inbox.setsockopt(zmq.ROUTER_HANDOVER, 1)
        self._inbox.setsockopt(zmq.LINGER, 0)
        if self._ipv6:
            self._inbox.setsockopt(zmq.IPV6, 1)
        if self._curve_secret is not None:
            self._beacon_version = BEACON_VERSION_V3
            self._inbox.setsockopt(zmq.CURVE_PUBLICKEY, self._curve_public)
            self._inbox.setsockopt(zmq.CURVE_SECRETKEY, self._curve_secret)
            self._inbox.setsockopt(zmq.CURVE_SERVER, 1)
            self._inbox.setsockopt_string(zmq.ZAP_DOMAIN, self._zap_domain)
        self._inbox.bind(bind_addr)
        ep = self._inbox.getsockopt(zmq.LAST_ENDPOINT).decode()
        self._inbox_port = int(ep.rsplit(":", 1)[1])
        self._inbox_poller.register(self._inbox, zmq.POLLIN)

        if self._gossip_active():
            self._beacon_sock = None
            self._beacon_fd = -1
            self._beacon_msg = None
            own_key = self.peer_id_hex.encode()
            hub_host, hub_port = "127.0.0.1", 0
            if self._gossip_bind is not None:
                hub_host, hub_port = self._split_hub_endpoint(self._gossip_bind)
                self._gossip_hub = GossipHub(self._ctx)
                self._gossip_hub.bind(self._gossip_bind)
                self._inbox_poller.register(self._gossip_hub.socket, zmq.POLLIN)
                # Our own tuple goes straight into the hub table, the way
                # zyre sends the PUBLISH actor command to its zgossip
                # delegate after bind.
                self._gossip_tuples[own_key] = (
                    self._gossip_publish_endpoint(hub_host, hub_port),
                    0,
                )
                self._gossip_hub.publish(own_key, *self._gossip_tuples[own_key])
            for target in self._gossip_connects:
                host, port = self._split_hub_endpoint(target)
                sock = self._ctx.socket(zmq.DEALER)
                sock.setsockopt(zmq.SNDHWM, 1000)
                sock.setsockopt(zmq.LINGER, 0)
                sock.connect(target)
                self._gossip_socks.append(sock)
                self._gossip_targets[sock] = (host, port)
                self._gossip_last_rx[sock] = self._now()
                self._inbox_poller.register(sock, zmq.POLLIN)
                if own_key not in self._gossip_tuples:
                    self._gossip_tuples[own_key] = (
                        self._gossip_publish_endpoint(host, port),
                        0,
                    )
                # zgossip client flow: HELLO first, then PUBLISH for each
                # tuple we have.
                self._gossip_announce(sock)
        else:
            self._beacon_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_REUSEPORT if available for multiple nodes on same host
            with contextlib.suppress(Exception):
                self._beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self._beacon_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Listen stays on the wildcard: on bridged/multi-homed stacks a
            # socket bound to one unicast address gets no broadcasts at all
            # (measured: neither limited nor subnet-directed arrive). The
            # interface pin applies to sending only.
            try:
                self._beacon_sock.bind(("", self._beacon_port))
            except OSError as exc:
                self._log("beacon bind (0.0.0.0:%s) failed: %s", self._beacon_port, exc)
                raise
            self._beacon_sock.setblocking(False)
            self._beacon_fd = self._beacon_sock.fileno()
            self._inbox_poller.register(self._beacon_sock, zmq.POLLIN)
            self._beacon_msg = self._build_beacon(self._inbox_port)

        self._running = True
        self._log(
            "started name=%s id=%s port=%s beacon=%s",
            self.name,
            self.peer_id_hex[:8],
            self._inbox_port,
            self._beacon_port,
        )
        await self._send_beacon()

    async def stop(self) -> None:
        """Stop the node and release every socket deterministically.

        The teardown runs even when the run loop already ended (the
        UUID-collision path sets `_running` false itself, and tests stop
        nodes by flipping the flag directly). An early return here left
        the ROUTER and DEALER sockets with their shared Context open —
        `Context.term()` only completes once every socket is closed, so
        the leaked context sat in the garbage collector and blocked
        pytest's `gc_collect_harder` inside `Context.__del__` for good.
        Idempotent: safe to call from any state, any number of times.
        The safe published pattern around a live run loop is
        cancel() -> join() -> stop(); stop() running while the loop is
        still alive is supported — the suppressed close paths absorb
        the race, as exercised by the idempotency test in
        tests/test_teardown.py.
        """
        was_running = self._running
        self._running = False
        if was_running:
            with contextlib.suppress(Exception):
                await self._send_beacon(port=0)
            if self._gossip_active():
                # GOODBYE is the gossip-mode departure signal only: libzyre
                # removes a peer on GOODBYE when self->gossip is set and ignores
                # it in beacon mode (zyre_node.c recv_peer), where the port-0
                # beacon is what announces the departure.
                with contextlib.suppress(Exception):
                    await self._gossip_shutdown()
                for peer in list(self._peers.values()):
                    with contextlib.suppress(Exception):
                        peer.send_cmd(GOODBYE)
            for group in list(self._own_groups):
                with contextlib.suppress(Exception):
                    await self._leave_group(group)
        for peer in list(self._peers.values()):
            peer.disconnect()
        self._peers.clear()
        for peer in list(self._pending.values()):
            peer.disconnect()
        self._pending.clear()
        with contextlib.suppress(Exception):
            self._inbox_poller.unregister(self._inbox)
        if self._beacon_sock:
            with contextlib.suppress(Exception):
                self._beacon_sock.close()
        self._beacon_sock = None
        with contextlib.suppress(Exception):
            if self._send_sock is not None:
                self._send_sock.close()
            self._send_sock = None
        for sock in (self._inbox,):
            if sock:
                with contextlib.suppress(Exception):
                    sock.close(linger=0)
        try:
            # destroy(linger=0) closes ANY sockets still attached to this
            # context (for example a ZAP authenticator whose thread has not
            # stopped yet) and then terms the context — bare term() can
            # block forever on such a straggler (that hang is what froze
            # pytest inside Context.__del__ before this fix).
            self._ctx.destroy(linger=0)
        except Exception as exc:
            # Teardown stays best-effort, but it is never silent: a
            # suppressed destroy would leave the context untermed.
            self._log("stop: context destroy suppressed: %s", exc)

    async def join(self, group: str | bytes) -> None:
        group_bytes = _wire_string("group", group)
        await self._api_queue.put(("JOIN", group_bytes))

    async def leave(self, group: str | bytes) -> None:
        group_bytes = _wire_string("group", group)
        await self._api_queue.put(("LEAVE", group_bytes))

    async def whisper(self, peer_hex: str | bytes, payload: str | bytes) -> None:
        peer_id = peer_hex.encode() if isinstance(peer_hex, str) else peer_hex
        raw_payload = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("WHISPER", peer_id, raw_payload))

    async def shout(self, group: str | bytes, payload: str | bytes) -> None:
        group_bytes = _wire_string("group", group)
        raw_payload = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("SHOUT", group_bytes, raw_payload))

    async def whispers(self, peer_hex: str | bytes, text: str) -> None:
        await self.whisper(peer_hex, text.encode() if isinstance(text, str) else text)

    async def shouts(self, group: str | bytes, text: str) -> None:
        await self.shout(group, text.encode() if isinstance(text, str) else text)

    def print(self) -> None:
        self._logger.warning(
            "node=%s id=%s peers=%d groups=%s",
            self._bdec(self.name),
            self.peer_id_hex[:8],
            len(self._peers),
            [self._bdec(group) for group in self._own_groups],
        )

    def dump(self) -> dict:
        return {
            "name": self._bdec(self.name),
            "uuid": self.peer_id_hex,
            "peers": {
                peer_id: {
                    "name": self._pname(found),
                    "address": found.endpoint_str or "",
                    "groups": sorted(self._bdec(group) for group in found.groups),
                }
                for peer_id, found in self._peers.items()
            },
            "own_groups": [self._bdec(group) for group in self._own_groups],
        }

    @staticmethod
    def version() -> str:
        from . import __version__

        return __version__

    async def connect_peer(
        self, host: str, port: int, public_key: bytes | str | None = None
    ) -> None:
        """Connect directly to a peer by host:port (WAN-capable).

        Bypasses UDP beacon discovery. Use when:
        - Peer is on a different subnet / across WAN
        - You know the peer's address (from DNS, config, or manual exchange)
        - Beacon broadcast doesn't reach the peer

        After connection, exchanges HELLOs and emits ENTER event.
        """
        if public_key is not None:
            self._peer_server_keys[f"{host}:{port}"] = self._curve_key_raw(
                public_key, "public_key"
            )
        await self._api_queue.put(("CONNECT", host, port))

    async def _do_connect(self, host: str, port: int) -> None:
        """Execute direct peer connection (called from run loop).

        Creates a persistent placeholder peer and HELLOs through it. When
        the answer arrives, the placeholder is re-keyed by the real UUID
        (same dealer, same connection -- never closed and reopened, so the
        far-side routing entry stays valid).
        """
        if not self._running:
            return
        for peer in self._peers.values():
            if peer.addr == host and peer.port == port and peer.connected:
                return

        key = f"pending-{host}:{port}"
        if key in self._pending:
            return
        peer = Peer(key, host, port)
        server_key, public_key, secret_key = self._curve_client_args(host, port)
        if self._curve_secret is not None and server_key is None:
            self._log("connect_peer refused: no public key for %s:%s", host, port)
            return
        if not peer.connect(
            self._ctx, self.peer_id, server_key, public_key, secret_key
        ):
            self._log("connect_peer failed: %s:%s", host, port)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        peer.refresh(loop.time(), self._ev_timeout, self._ex_timeout)
        self._pending[key] = peer
        self._send_hello(peer)

    def _adopt_pending(
        self, addr: str, port: int, rhex: str, now: float
    ) -> Peer | None:
        """Move a pending outbound peer to the real table under its UUID.

        Returns the adopted peer, or None. The dealer and connection are
        kept as-is; only the table key changes.
        """
        for key, old in list(self._pending.items()):
            if old.addr == addr and old.port == port:
                self._pending.pop(key, None)
                old.uuid_hex = rhex
                old.refresh(now, self._ev_timeout, self._ex_timeout)
                self._peers[rhex] = old
                return old
        for key, old in list(self._pending.items()):
            if old.port != port:
                continue
            if rhex in self._peers:
                continue
            self._pending.pop(key, None)
            old.uuid_hex = rhex
            old.refresh(now, self._ev_timeout, self._ex_timeout)
            self._peers[rhex] = old
            return old
        return None

    def set_header(self, key: str | bytes, value: str | bytes) -> None:
        key_bytes = _wire_string("header name", key)
        self._headers[key_bytes] = _wire_string(f"header value for {key!r}", value)

    def _require_group(self, group: bytes) -> Group:
        found = self._groups.get(group)
        if found is None:
            found = Group(group)
            self._groups[group] = found
        if group in self._contest_groups:
            found.contest = True
        return found

    def _wire_id(self, uuid_hex: str) -> str:
        """Peer id in the form libzyre puts on the wire.

        libzyre compares ELECT/LEADER identities with plain strcmp() over
        zuuid_str() output, which is UPPERCASE hex (e.g. "AB12..."). If we
        sent lowercase, 'a' (0x61) sorts after 'A' (0x41) and every libzyre
        node would treat our challenger as *not* superior, re-running the
        election forever. So identities travel uppercase; they are lowercased
        again for the public event fields and our own peer tables.
        """
        return uuid_hex.upper()

    def _election_neighbors(self, group: Group) -> list:
        """Peers an election wave can travel through.

        Matches zyre_group_peers(): every peer libzyre has recorded as a
        member of the group. Counting a different set here makes
        erec_complete()/lrec_complete() disagree with libzyre's and the
        election never converges.
        """
        return [found for found in group.peers.values() if found.connected]

    def _now(self) -> float:
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return 0.0

    def _start_election(self, group_bytes: bytes) -> None:
        group = self._require_group(group_bytes)
        # libzyre only runs an election for a group we are a member of
        # (zyre_node.c guards every start with zlist_exists(own_groups, ...));
        # contesting a group we never joined would make us a phantom
        # candidate that libzyre peers never count.
        if not group.contest or group_bytes not in self._own_groups:
            return
        if group.election is not None:
            # libzyre unconditionally throws away a running election when
            # group membership changes (zyre_node.c recv_peer HELLO/JOIN:
            # "Discard a running election because the number of peers
            # change"). Restarting on top of a live election produces extra
            # ELECT waves and a duplicate LEADER event per member.
            group.election = None
        now = self._now()
        group.election = {
            "caw": self._wire_id(self.peer_id_hex),
            "father": None,
            "erec": 0,
            "lrec": 0,
            "leader": None,
            "started_at": now,
            "resent_at": now,
        }
        group.leader = None
        neighbors = self._election_neighbors(group)
        if not neighbors:
            self._finish_election(group, self._wire_id(self.peer_id_hex))
            return
        caw = self._wire_id(self.peer_id_hex).encode()
        for found in neighbors:
            found.send_election(ELECT, group_bytes, caw)

    def _handle_elect(self, peer: Peer, group_bytes: bytes, challenger: bytes) -> None:
        group = self._require_group(group_bytes)
        challenger_id = self._wire_id(self._bdec(challenger))
        election = group.election
        now = self._now()
        if election is None or challenger_id < election["caw"]:
            election = {
                "caw": challenger_id,
                "father": peer.uuid_hex,
                "erec": 0,
                "lrec": 0,
                "leader": None,
                "started_at": now,
                "resent_at": now,
            }
            group.election = election
            for found in self._election_neighbors(group):
                if found.uuid_hex != peer.uuid_hex:
                    found.send_election(ELECT, group_bytes, challenger_id.encode())
        if challenger_id != election["caw"]:
            return
        election["erec"] += 1
        if election["erec"] < len(self._election_neighbors(group)):
            return
        if election["caw"] == self._wire_id(self.peer_id_hex):
            for found in self._election_neighbors(group):
                found.send_election(LEADER, group_bytes, election["caw"].encode())
        elif election["father"] is not None:
            father = self._peers.get(election["father"])
            if father is not None:
                father.send_election(ELECT, group_bytes, election["caw"].encode())

    def _maintain_elections(self, now: float) -> None:
        """Resend a stalled election wave so mixed clusters converge.

        When group membership changes mid-election, libzyre discards the
        running wave on every member and each restarts with its own id.
        Waves already in flight then count towards the *new* election and
        some echoes never arrive: every member waits forever (seen with 2
        libzyre + 1 zre peers where zre holds the smallest id). Resending
        the current CAW every few seconds gives the stalled members the
        echo they wait for — an ELECT for the CAW they already support
        increments their erec — and the election terminates. Traffic is one
        small frame per neighbor per interval, only while an election is
        open.
        """
        for group_bytes, group in self._groups.items():
            election = group.election
            if election is None or not group.contest:
                continue
            if group_bytes not in self._own_groups:
                continue
            neighbors = self._election_neighbors(group)
            if not neighbors:
                self._finish_election(group, self._wire_id(self.peer_id_hex))
                continue
            if now - election.get("resent_at", 0.0) < 2.0:
                continue
            election["resent_at"] = now
            caw = election["caw"].encode()
            if election["caw"] == self._wire_id(self.peer_id_hex):
                if election["erec"] >= len(neighbors):
                    for found in neighbors:
                        found.send_election(LEADER, group_bytes, caw)
                else:
                    for found in neighbors:
                        found.send_election(ELECT, group_bytes, caw)
            else:
                for found in neighbors:
                    if found.uuid_hex != election["father"]:
                        found.send_election(ELECT, group_bytes, caw)

    def _handle_leader(self, peer: Peer, group_bytes: bytes, leader: bytes) -> None:
        group = self._require_group(group_bytes)
        election = group.election
        if election is None or election["caw"] is None:
            return
        leader_id = self._wire_id(self._bdec(leader))
        if leader_id != self._wire_id(self.peer_id_hex) and election["lrec"] == 0:
            for found in self._election_neighbors(group):
                found.send_election(LEADER, group_bytes, leader_id.encode())
        election["lrec"] += 1
        election["leader"] = leader_id
        if election["lrec"] >= len(self._election_neighbors(group)):
            self._finish_election(group, leader_id)

    def _finish_election(self, group: Group, leader_id: str) -> None:
        leader_hex = leader_id.lower()
        group.leader = leader_hex
        group.election = None
        if leader_id == self._wire_id(self.peer_id_hex):
            leader_name = self._bdec(self.name)
        else:
            leader_peer = self._peers.get(leader_hex)
            leader_name = self._pname(leader_peer) if leader_peer else "unknown"
        self._emit(
            {
                "type": "LEADER",
                "peer_id": leader_hex,
                "peer_name": leader_name,
                "group": self._bdec(group.name),
            }
        )

    def _restart_election_if_needed(self, group_bytes: bytes) -> None:
        group = self._groups.get(group_bytes)
        if group is None or not group.contest:
            return
        neighbors = self._election_neighbors(group)
        leader = group.leader
        if leader is not None and leader not in self._peers:
            # The elected leader is gone: a new one must be picked.
            self._start_election(group_bytes)
        elif group.election is not None and not neighbors:
            # Nobody left to exchange waves with; we are the last one.
            self._finish_election(group, self._wire_id(self.peer_id_hex))
        elif group.election is not None and leader is not None:
            # A leader was already announced for the current membership;
            # re-running the wave would emit a second LEADER event.
            group.election = None

    async def _gossip_require(self, uuid_hex: str, endpoint: str, now: float) -> None:
        # zgossip announces keys as zuuid_str() — UPPERCASE hex. Our tables
        # key peers by lowercase, and C peers accept either form (they
        # strcmp() against their own zuuid_str, which is uppercase); without
        # the normalization a single phone announces two peers here.
        uuid_hex = uuid_hex.lower()
        if not self._running:
            return
        if uuid_hex == self.peer_id_hex:
            return
        if "|" in endpoint:
            endpoint, advertised_key = endpoint.split("|", 1)
            with contextlib.suppress(ValueError):
                self._peer_server_keys[uuid_hex] = self._curve_key_raw(
                    advertised_key, "public_key"
                )
        try:
            addr, port = _split_endpoint(endpoint)
        except ValueError:
            return
        if uuid_hex in self._peers:
            self._peers[uuid_hex].refresh(now, self._ev_timeout, self._ex_timeout)
            return
        if self._adopt_pending(addr, port, uuid_hex, now) is not None:
            self._send_hello(self._peers[uuid_hex])
            return
        peer = Peer(uuid_hex, addr, port)
        server_key, public_key, secret_key = self._curve_client_args(
            addr, port, uuid_hex
        )
        if self._curve_secret is not None and server_key is None:
            return
        if peer.connect(self._ctx, self.peer_id, server_key, public_key, secret_key):
            peer.refresh(now, self._ev_timeout, self._ex_timeout)
            self._peers[uuid_hex] = peer
            self._send_hello(peer)

    def _gossip_announce(self, sock: zmq.Socket) -> None:
        """zgossip client flow on a hub link: HELLO, then PUBLISH for each
        tuple we have (our own announcement, plus anything learned)."""
        try:
            sock.send(zgs_encode(ZGS_HELLO), zmq.NOBLOCK)
            for key, (value, ttl) in self._gossip_tuples.items():
                sock.send(zgs_encode(ZGS_PUBLISH, key, value, ttl), zmq.NOBLOCK)
        except zmq.ZMQError as exc:
            self._log("gossip announce failed: %s", exc)

    def _gossip_probe_target(self) -> tuple[str, int]:
        for target in self._gossip_connects:
            host, port = self._split_hub_endpoint(target)
            if host not in ("0.0.0.0", "*", ""):
                return host, port
        return "8.8.8.8", 80

    async def _handle_gossip_client(self, sock: zmq.Socket) -> None:
        """Inbound ZGS.1 traffic on one of our DEALER hub links."""
        while True:
            try:
                body = sock.recv(zmq.NOBLOCK)
            except zmq.Again:
                break
            except zmq.ZMQError:
                break
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            now = loop.time()
            self._gossip_last_rx[sock] = now
            message = zgs_decode(body)
            if message is None:
                self._log("gossip: malformed frame from hub")
                continue
            command = message["command"]
            if command == ZGS_PUBLISH:
                key, value, ttl = message["key"], message["value"], message["ttl"]
                stored = self._gossip_tuples.get(key)
                if stored is None or stored[0] != value:
                    self._gossip_tuples[key] = (value, ttl)
                    # Delivered by the hub: dial the announced endpoint the
                    # way zgossip's DELIVER wakes zyre_peer_require.
                    await self._gossip_require(self._bdec(key), self._bdec(value), now)
            elif command == ZGS_INVALID:
                # Remote reset the session: say HELLO again and re-publish
                # everything we have (zgossip.c remote_handler).
                self._gossip_announce(sock)
            # PONG: any inbound traffic counts as liveness

    async def _gossip_redial(self, sock: zmq.Socket) -> None:
        """Reconnect a hub link that stopped answering."""
        target = self._gossip_targets.get(sock)
        if target is None:
            return
        host, port = target
        self._log("gossip redial %s:%s", host, port)
        with contextlib.suppress(Exception):
            self._inbox_poller.unregister(sock)
        with contextlib.suppress(Exception):
            sock.close(linger=0)
        self._gossip_socks.remove(sock)
        self._gossip_last_rx.pop(sock, None)
        fresh = self._ctx.socket(zmq.DEALER)
        fresh.setsockopt(zmq.SNDHWM, 1000)
        fresh.setsockopt(zmq.LINGER, 0)
        fresh.connect(f"tcp://{host}:{port}")
        self._gossip_socks.append(fresh)
        self._gossip_targets[fresh] = target
        self._gossip_last_rx[fresh] = self._now()
        self._inbox_poller.register(fresh, zmq.POLLIN)
        self._gossip_announce(fresh)

    async def _gossip_maintain(self, now: float) -> None:
        """Heartbeat hub links and re-dial the ones that went quiet.

        zgossip keeps its remotes alive via the zmq socket monitor; the
        PING every second doubles as that liveness signal here (the hub
        answers PONG, and any inbound frame refreshes the link).
        """
        if not self._gossip_socks:
            return
        if now >= self._gossip_next_ping:
            self._gossip_next_ping = now + 1.0
            for sock in list(self._gossip_socks):
                with contextlib.suppress(zmq.ZMQError):
                    sock.send(zgs_encode(ZGS_PING), zmq.NOBLOCK)
        for sock in list(self._gossip_socks):
            if now - self._gossip_last_rx.get(sock, now) > 5.0:
                await self._gossip_redial(sock)

    async def _gossip_shutdown(self) -> None:
        for sock in list(self._gossip_socks):
            with contextlib.suppress(Exception):
                self._inbox_poller.unregister(sock)
            with contextlib.suppress(Exception):
                sock.close(linger=0)
            self._gossip_socks.remove(sock)
        self._gossip_targets.clear()
        self._gossip_last_rx.clear()
        self._gossip_next_ping = 0.0
        if self._gossip_hub is not None:
            with contextlib.suppress(Exception):
                self._inbox_poller.unregister(self._gossip_hub.socket)
            self._gossip_hub.close()
            self._gossip_hub = None
        self._gossip_tuples.clear()

    async def events(self) -> AsyncIterator[dict]:
        # Drain past `self._running`: the run loop can die (e.g. a uuid
        # collision raises after emitting COLLISION) while a consumer is
        # still about to start iterating — without the queue check that
        # event would sit undelivered forever.
        while self._running or not self._event_queue.empty():
            try:
                yield await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def recv(self, timeout: float = 1.0) -> dict | None:
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def peers(self) -> list[str]:
        return list(self._peers.keys())

    def own_groups(self) -> list[bytes]:
        return list(self._own_groups)

    def peers_by_group(self, group: str | bytes) -> list[str]:
        """Return peer ids known to be in the given group."""
        group_bytes = group.encode() if isinstance(group, str) else group
        found = self._groups.get(group_bytes)
        if found is None:
            return []
        return list(found.peers.keys())

    def peer_groups(self) -> list[bytes]:
        """Return all groups known through connected peers plus own groups."""
        known: set[bytes] = set(self._own_groups)
        for peer in self._peers.values():
            known.update(peer.groups)
        return sorted(known)

    def peer_address(self, peer_hex: str | bytes) -> str:
        """Return the endpoint of a connected peer, or empty string."""
        peer_id = peer_hex.decode() if isinstance(peer_hex, bytes) else peer_hex
        found = self._peers.get(peer_id)
        if found is None:
            return ""
        return found.endpoint_str or ""

    def peer_header_value(
        self, peer_hex: str | bytes, name: str | bytes
    ) -> bytes | None:
        """Return one header value of a connected peer, or None."""
        peer_id = peer_hex.decode() if isinstance(peer_hex, bytes) else peer_hex
        found = self._peers.get(peer_id)
        if found is None:
            return None
        key = name.encode() if isinstance(name, str) else name
        value = found.headers.get(key)
        if value is None:
            # Headers may arrive decoded as str in some paths; retry loosely.
            for existing_key, existing_value in found.headers.items():
                if self._bdec(existing_key) == self._bdec(key):
                    value = existing_value
                    break
        return value

    async def run(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        last_reap = loop.time()
        reap_interval = REAP_INTERVAL_MS / 1000.0

        try:
            await self._run_loop(loop, last_reap, reap_interval)
        except UUIDCollisionError as exc:
            # Another node owns our uuid on this network. Emit a COLLISION
            # event first so application event loops can report it (the
            # exception itself is often swallowed by gather(...)), then
            # shut down WITHOUT the goodbye beacon: broadcasting EXIT for
            # our uuid would tell the *other* node's peers that it departed.
            self._running = False
            self._emit({"type": "COLLISION", "detail": str(exc)})
            with contextlib.suppress(Exception):
                for peer in list(self._peers.values()):
                    peer.disconnect()
            self._peers.clear()
            for sock in (self._inbox, self._beacon_sock, self._send_sock):
                if sock:
                    with contextlib.suppress(Exception):
                        sock.close()
            self._beacon_sock = None
            self._send_sock = None
            self._log("stopped: uuid collision")
            raise

    async def _run_loop(
        self, loop: asyncio.AbstractEventLoop, last_reap: float, reap_interval: float
    ) -> None:
        while self._running:
            now = loop.time()

            while not self._api_queue.empty():
                try:
                    cmd = self._api_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if cmd[0] == "JOIN":
                    await self._join_group(cmd[1])
                elif cmd[0] == "LEAVE":
                    await self._leave_group(cmd[1])
                elif cmd[0] == "WHISPER":
                    await self._do_whisper(cmd[1], cmd[2])
                elif cmd[0] == "SHOUT":
                    await self._do_shout(cmd[1], cmd[2])
                elif cmd[0] == "CONNECT":
                    await self._do_connect(cmd[1], cmd[2])

            if now - self._last_beacon >= self._beacon_interval:
                await self._send_beacon()
                self._last_beacon = now

            # One wait covers both sockets: wake on traffic or when the
            # next beacon is due (capped so commands stay responsive).
            wait_ms = (
                self._beacon_interval - (loop.time() - self._last_beacon)
            ) * 1000.0
            if wait_ms < 0.0:
                wait_ms = 0.0
            elif wait_ms > 5.0:
                wait_ms = 5.0
            try:
                ready = self._inbox_poller.poll(wait_ms)
            except zmq.ZMQError as exc:
                self._log("poll error: %s", exc)
                ready = []
            for sock, _ev in ready:
                if sock is self._inbox:
                    try:
                        frames = self._inbox.recv_multipart(zmq.NOBLOCK)
                    except zmq.Again:
                        continue
                    await self._handle_peer_msg(frames)
                elif sock == self._beacon_fd:
                    try:
                        data, addr = self._beacon_sock.recvfrom(BEACON_SIZE_V3)
                    except (BlockingIOError, OSError):
                        continue
                    self._handle_beacon(data, addr)
                elif self._gossip_hub is not None and sock is self._gossip_hub.socket:
                    # ZGS.1 hub traffic: accepted tuples also wake this node,
                    # the way zgossip's DELIVER wakes the hub-hosting zyre.
                    for key, value in self._gossip_hub.handle_socket():
                        await self._gossip_require(
                            self._bdec(key), self._bdec(value), now
                        )
                elif sock in self._gossip_socks:
                    await self._handle_gossip_client(sock)

            if now - last_reap >= reap_interval:
                await self._reap(now)
                last_reap = now

            # Yield every iteration: the loop above is fully synchronous
            # (blocking poll + NOBLOCK receives), so without this no other
            # task on the loop ever runs.
            await asyncio.sleep(0)

        # Send EXIT beacon on shutdown
        with contextlib.suppress(Exception):
            await self._send_beacon(port=0)
        with contextlib.suppress(Exception):
            await self._gossip_shutdown()
        with contextlib.suppress(Exception):
            if self._send_sock is not None:
                self._send_sock.close()
            self._send_sock = None

    async def _join_group(self, group: bytes) -> None:
        if group in self._own_groups:
            return
        self._own_groups.append(group)
        self._status = (self._status + 1) & 0xFF
        self._require_group(group)
        status = self._status
        self._log("SEND JOIN group=%s", self._bdec(group))
        for peer in list(self._peers.values()):
            peer.send_cmd(JOIN, group, status)
        # No election is started here, matching libzyre: its JOIN handler
        # only sends the JOIN command (zyre_node_recv_api), and elections
        # are kicked off by the *receiving* side when a peer's HELLO/JOIN
        # arrives. Starting one on our own join makes us a second candidate
        # wave, which double-counts ELECT/LEADER and produces two LEADER
        # events for the same group. We still take part in an election the
        # peers start, via _handle_elect.

    async def _leave_group(self, group: bytes) -> None:
        if group not in self._own_groups:
            return
        self._own_groups.remove(group)
        self._status = (self._status + 1) & 0xFF
        status = self._status
        for peer in list(self._peers.values()):
            peer.send_cmd(LEAVE, group, status)

    async def _do_whisper(self, peer_hex: bytes, payload: bytes) -> None:
        peer_id = peer_hex.decode() if isinstance(peer_hex, bytes) else peer_hex
        peer = self._peers.get(peer_id)
        if peer and peer.connected:
            self._log("SEND WHISPER to %s", peer_id[:8])
            peer.send_cmd(WHISPER, more=[payload])

    async def _do_shout(self, group: bytes, payload: bytes) -> None:
        grp = self._groups.get(group)
        if grp:
            grp.send(lambda seq: Codec.encode_simple(SHOUT, seq, group), [payload])

    def _interface_broadcast(self) -> str:
        """Subnet broadcast for the pinned interface (e.g. 192.168.122.255)."""
        if not self._interface:
            return ""
        ip = self._resolve_interface_ip()
        if not ip:
            return ""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = fcntl.ioctl(
                sock.fileno(),
                SIOCGIFNETMASK,
                struct.pack("256s", self._interface.encode()[:15]),
            )
            mask = struct.unpack("!I", packed[20:24])[0]
        except OSError:
            return ""
        finally:
            sock.close()
        addr = struct.unpack("!I", socket.inet_aton(ip))[0]
        # Clear the host bits before setting them: the subnet part must come
        # from the masked address, not from whatever the host address had in
        # those bits (OR-ing straight through yields 255.168.122.1 for
        # 192.168.122.1/24 instead of 192.168.122.255).
        return socket.inet_ntoa(struct.pack("!I", (addr & mask) | (~mask & 0xFFFFFFFF)))

    def _local_subnet_broadcasts(self) -> list[str]:
        """Subnet broadcast address of every usable IPv4 interface.

        libzyre's zbeacon resolves one interface and sends to that
        interface's subnet broadcast (zbeacon logs e.g. broadcast=
        192.168.122.255). A limited 255.255.255.255 datagram is *routed*
        by the kernel, so on a multi-homed host it leaves through the
        default route — a peer's LAN that is not the default route never
        sees it. Targeting each local subnet explicitly is what makes
        discovery work on those hosts.
        """
        found: list[str] = []
        try:
            with open("/proc/net/route") as route_file:
                lines = route_file.readlines()[1:]
        except OSError:
            return found
        for line in lines:
            # Iface Destination Gateway Flags RefCnt Use Metric Mask ...
            fields = line.split()
            if len(fields) < 8:
                continue
            iface, destination, mask = fields[0], fields[1], fields[7]
            if iface == "lo" or destination == "00000000":
                # default route: covered by the limited broadcast below
                continue
            if int(mask, 16) == 0:
                continue
            try:
                ip = self._resolve_interface_ip(iface)
                if not ip or ip.startswith("127."):
                    continue
                addr = struct.unpack("!I", socket.inet_aton(ip))[0]
                # /proc/net/route prints Destination/Mask as hex of the
                # host-order 32-bit value, so on little-endian hosts the
                # bytes are reversed vs network order: 00FFFFFF on the wire
                # is 255.255.255.0 (/24), not 0.255.255.255. Reverse before
                # decoding. struct.unpack("!I", bytes.fromhex(mask)) without
                # the reverse yields e.g. 255.168.122.1 for 192.168.122.1/24
                # instead of 192.168.122.255.
                netmask = struct.unpack("!I", bytes.fromhex(mask)[::-1])[0]
                # Clear the host bits before setting them: the subnet part
                # must come from the masked address (OR-ing straight through
                # keeps host bits from the interface address).
                broadcast = socket.inet_ntoa(
                    struct.pack("!I", (addr & netmask) | (~netmask & 0xFFFFFFFF))
                )
            except (OSError, ValueError):
                continue
            if broadcast not in found:
                found.append(broadcast)
        return found

    def _build_send_sock(self) -> None:
        old, self._send_sock = self._send_sock, None
        with contextlib.suppress(Exception):
            if old is not None:
                old.close()
        port = self._beacon_port
        targets = [(addr, port) for addr in self._local_subnet_broadcasts()]
        targets.append(("255.255.255.255", port))
        # Loopback targets keep same-host clusters (and the test suite)
        # working without touching the wire for peers.
        targets.append(("127.0.0.1", port))
        self._send_targets = targets
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        bind_ip = self._resolve_interface_ip()
        if bind_ip:
            sock.bind((bind_ip, 0))
            # A pinned interface means a single, deliberate destination.
            subnet = self._interface_broadcast()
            self._send_targets = (
                [(subnet, port), ("127.0.0.1", port)] if subnet else targets
            )
        self._send_sock = sock

    async def _send_beacon(self, port: int | None = None) -> None:
        if self._gossip_active() or (self._beacon_sock is None and port is None):
            return
        if port is None:
            beacon = self._beacon_msg
            if beacon is None:
                beacon = self._build_beacon(self._inbox_port)
        else:
            beacon = self._build_beacon(port)
        try:
            if self._send_sock is None:
                self._build_send_sock()
            for target in self._send_targets:
                with contextlib.suppress(Exception):
                    self._send_sock.sendto(beacon, target)
        except Exception as exc:
            self._log("beacon send failed: %s", exc)
            self._send_sock = None

    def _handle_beacon(self, data: bytes, addr: tuple) -> None:
        if not self._running:
            return
        if data[:3] != b"ZRE":
            return
        version = data[3]
        expected = (
            BEACON_SIZE_V3
            if version == BEACON_VERSION_V3
            else BEACON_SIZE
            if version == BEACON_VERSION_V1
            else None
        )
        if expected is None or len(data) != expected:
            return
        # Beacons are versioned per security mode; libzyre drops any
        # beacon whose version differs from its own. Accepting only our own
        # keeps a Curve node from dialling a peer it cannot authenticate
        # and vice versa.
        if version != self._beacon_version:
            return
        remote_uuid = data[4:20]
        remote_port = struct.unpack("!H", data[20:22])[0]
        if remote_uuid == self.peer_id:
            # A beacon with our uuid is usually our own broadcast looping
            # back (the payload carries our inbox port), or our EXIT beacon
            # (port 0). But a foreign node with the same stable uuid sends
            # ITS inbox port — and TCP ROUTER binds are exclusive, so a
            # port difference means a second node is live with our id.
            if remote_port and remote_port != self._inbox_port:
                raise UUIDCollisionError(
                    f"another node with uuid {self.peer_id_hex} responded on "
                    f"the network (beacon from {addr[0]}:{remote_port})"
                )
            return
        rhex = remote_uuid.hex()
        if version == BEACON_VERSION_V3 and len(data) == BEACON_SIZE_V3:
            self._peer_server_keys[rhex] = bytes(data[22:BEACON_SIZE_V3])
            self._peer_server_keys[f"{addr[0]}:{remote_port}"] = bytes(
                data[22:BEACON_SIZE_V3]
            )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        now = loop.time()

        if remote_port == 0:
            peer = self._peers.pop(rhex, None)
            if peer:
                gone_groups = list(peer.groups)
                for group in gone_groups:
                    grp = self._groups.get(group)
                    if grp:
                        grp.leave(peer)
                peer.disconnect()
                self._evasive_emit_at.pop(rhex, None)
                self._emit(
                    {
                        "type": "EXIT",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                    }
                )
                for group in gone_groups:
                    self._restart_election_if_needed(group)
            return
        if rhex in self._peers:
            self._peers[rhex].refresh(now, self._ev_timeout, self._ex_timeout)
            return

        if self._adopt_pending(addr[0], remote_port, rhex, now) is not None:
            self._send_hello(self._peers[rhex])
            return

        peer = Peer(rhex, addr[0], remote_port)
        server_key, public_key, secret_key = self._curve_client_args(
            addr[0], remote_port, rhex
        )
        if self._curve_secret is not None and server_key is None:
            return
        if peer.connect(self._ctx, self.peer_id, server_key, public_key, secret_key):
            peer.refresh(now, self._ev_timeout, self._ex_timeout)
            self._peers[rhex] = peer
            self._send_hello(peer)

    def _own_address_for(self, peer_addr: str) -> str:
        """Local address the peer should dial back (sends no traffic)."""
        ip = self._resolve_interface_ip()
        if ip:
            return ip
        family = socket.AF_INET6 if ":" in peer_addr.strip("[]") else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.connect((peer_addr, self._beacon_port))
            return sock.getsockname()[0]
        except OSError:
            return peer_addr
        finally:
            sock.close()

    def _public_endpoint_for(self, peer_addr: str) -> bytes:
        """Dial-back endpoint announced in HELLO.

        Plainly `tcp://host:port`, with no `|z85-key` suffix. libzyre dials
        the HELLO endpoint verbatim (zyre_peer_connect -> zsock_connect), so
        a suffixed endpoint fails to connect and the peer is dropped. The
        Curve key travels in the X-PUBLICKEY header instead, and the
        `|key` suffix only exists on zgossip PUBLISH announcements.
        """
        if self._advertised_endpoint:
            return self._advertised_endpoint
        return _tcp_address(
            self._own_address_for(peer_addr.strip("[]")), self._inbox_port
        ).encode()

    def _send_hello(self, peer: Peer) -> None:
        self._log("SEND HELLO to %s groups=%s", peer.uuid_hex[:8], self._own_groups)
        # HELLO is always the first command on a fresh DEALER, so its
        # sequence number is this peer's first one (RFC 36). Peers that
        # answer HELLO on a connection we already greeted get a 2.
        peer.send_encoded(
            lambda seq: Codec.encode_hello(
                seq,
                self._public_endpoint_for(peer.addr),
                self._own_groups,
                self._status,
                self.name,
                self._headers,
            )
        )

    async def _handle_peer_msg(self, frames: list) -> None:
        if not frames or len(frames) < 2:
            return
        routing_id = frames[0]
        data = frames[1]
        extra_frames = frames[2:]
        if len(routing_id) != 17 or routing_id[0] != 1:
            return
        rhex = routing_id[1:].hex()
        result = Codec.decode(data)
        if result is None:
            self._log("decode failed from %s", rhex[:8])
            return
        cmd, _ver, seq, extra = result
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        now = loop.time()
        peer = self._peers.get(rhex)

        if cmd == HELLO:
            # libzyre runs every inbound message through messages_lost(),
            # and HELLO resets the counter to 1 (zyre_peer.c). Without that
            # reset our first non-HELLO command would look like a gap.
            if peer is not None:
                peer.want_seq = 1
            await self._handle_hello(rhex, extra, peer)
            return

        if peer is None or not peer.ready:
            return
        if cmd in (WHISPER, SHOUT, JOIN, LEAVE, ELECT, LEADER):
            # App-level traffic from the peer: a new quiet period after this
            # is a fresh episode, so re-arm the EVASIVE throttle.
            self._evasive_emit_at.pop(rhex, None)
        if peer.check_seq(cmd, seq):
            self._log(
                "seq mismatch %s expected %s got %s, updating",
                rhex[:8],
                peer.want_seq,
                seq,
            )
            peer.want_seq = seq
            # continue, do not drop (robust to out-of-order HELLO/JOIN)

        peer.refresh(now, self._ev_timeout, self._ex_timeout)

        if cmd == WHISPER:
            payload = extra_frames[0] if extra_frames else b""
            self._emit(
                {
                    "type": "WHISPER",
                    "peer_id": rhex,
                    "peer_name": self._pname(peer),
                    "payload": payload,
                }
            )
        elif cmd == SHOUT:
            group = extra.get("group", b"")
            payload = extra_frames[0] if extra_frames else b""
            self._emit(
                {
                    "type": "SHOUT",
                    "peer_id": rhex,
                    "peer_name": self._pname(peer),
                    "group": self._bdec(group),
                    "payload": payload,
                }
            )
        elif cmd == JOIN:
            group = extra.get("group")
            if group:
                peer.groups.add(group)
                grp = self._require_group(group)
                grp.join(peer)
                self._emit(
                    {
                        "type": "JOIN",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                        "group": self._bdec(group),
                    }
                )
                self._start_election(group)
        elif cmd == LEAVE:
            group = extra.get("group")
            if group:
                peer.groups.discard(group)
                grp = self._groups.get(group)
                if grp:
                    grp.leave(peer)
                self._emit(
                    {
                        "type": "LEAVE",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                        "group": self._bdec(group),
                    }
                )
                self._restart_election_if_needed(group)
        elif cmd == ELECT:
            group = extra.get("group")
            challenger = extra.get("challenger")
            if group is not None and challenger is not None:
                self._handle_elect(peer, group, challenger)
        elif cmd == LEADER:
            group = extra.get("group")
            leader = extra.get("leader")
            if group is not None and leader is not None:
                self._handle_leader(peer, group, leader)
        elif cmd == GOODBYE:
            await self._remove_peer(peer)
        elif cmd == PING:
            peer.send_cmd(PING_OK)
        elif cmd == PING_OK:
            peer.refresh(now, self._ev_timeout, self._ex_timeout)

    async def _handle_hello(
        self, rhex: str, extra: dict, existing: Peer | None
    ) -> None:
        if not self._running:
            return
        ep = extra.get("endpoint")
        if rhex == self.peer_id_hex:
            # Own-uuid HELLO. If it announces our inbox port it is our own
            # handshake looping back (a second process can never bind our
            # TCP port) — ignore, matching zyre_node.c:1092. A different
            # port means a second node runs with our stable uuid.
            try:
                helo_port = _split_endpoint(self._bdec(ep))[1] if ep else None
            except (IndexError, ValueError, AttributeError):
                helo_port = None
            if helo_port == self._inbox_port:
                return
            raise UUIDCollisionError(
                f"another node with uuid {self.peer_id_hex} announced itself "
                f"(HELLO from {extra.get('endpoint', 'unknown')})"
            )
        if not ep:
            return
        # A HELLO is app-level liveness: re-arm the EVASIVE throttle so a
        # later quiet period is reported as a fresh episode.
        self._evasive_emit_at.pop(rhex, None)
        ep_str = self._bdec(ep)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        now = loop.time()

        if existing and existing.ready:
            # Duplicate HELLO for a live peer: refresh in place. Tearing the
            # peer down here re-triggers a HELLO reply and ping-pongs forever.
            existing.refresh(now, self._ev_timeout, self._ex_timeout)
            existing.name = extra.get("name")
            existing.headers = extra.get("headers", {})
            existing.status = extra.get("status", 0)
            return

        is_new = rhex not in self._peers
        hello_reply = False
        if is_new:
            # A Curve peer announces its key as an X-PUBLICKEY header, the
            # way libzyre does (zyre_node.c: zhash_update on SET PUBLICKEY,
            # read back in recv_peer). Older/foreign peers may instead
            # append "|z85key" to the endpoint, so keep that path too.
            headers = extra.get("headers") or {}
            advertised = headers.get(PUBLIC_KEY_HEADER)
            if isinstance(advertised, bytes):
                with contextlib.suppress(ValueError):
                    self._peer_server_keys[rhex] = self._curve_key_raw(
                        advertised, "public_key"
                    )
            if "|" in ep_str:
                ep_str, advertised_key = ep_str.split("|", 1)
                with contextlib.suppress(ValueError):
                    self._peer_server_keys[rhex] = self._curve_key_raw(
                        advertised_key, "public_key"
                    )
            try:
                addr, port = _split_endpoint(ep_str)
                if addr in ("0.0.0.0", "*"):
                    addr = "127.0.0.1"
            except Exception:
                return
            pending = self._adopt_pending(addr, port, rhex, now)
            if pending is not None:
                # No HELLO reply: they already hold us (they answered ours).
                peer = pending
            else:
                peer = Peer(rhex, addr, port)
                server_key, public_key, secret_key = self._curve_client_args(
                    addr, port, rhex
                )
                if self._curve_secret is not None and server_key is None:
                    return
                if peer.connect(
                    self._ctx, self.peer_id, server_key, public_key, secret_key
                ):
                    peer.refresh(now, self._ev_timeout, self._ex_timeout)
                    self._peers[rhex] = peer
                    hello_reply = True
                else:
                    return
        else:
            peer = self._peers[rhex]
            is_new = False

        peer.name = extra.get("name")
        peer.headers = extra.get("headers", {})
        peer.status = extra.get("status", 0)
        peer.ready = True
        peer.endpoint_str = ep_str
        if hello_reply:
            self._send_hello(peer)

        for group in extra.get("groups", []):
            grp = self._groups.get(group)
            if not grp:
                grp = Group(group)
                self._groups[group] = grp
            grp.join(peer)
            peer.groups.add(group)

        pname = self._pname(peer)
        self._emit(
            {
                "type": "ENTER",
                "peer_id": rhex,
                "peer_name": pname,
                "address": ep_str,
            }
        )
        for group in extra.get("groups", []):
            self._emit(
                {
                    "type": "JOIN",
                    "peer_id": rhex,
                    "peer_name": pname,
                    "group": self._bdec(group),
                }
            )
        for group in extra.get("groups", []):
            self._start_election(group)

    async def _remove_peer(self, peer: Peer) -> None:
        gone_groups = list(peer.groups)
        for group in gone_groups:
            grp = self._groups.get(group)
            if grp:
                grp.leave(peer)
        pname = self._pname(peer)
        peer.disconnect()
        self._peers.pop(peer.uuid_hex, None)
        self._evasive_emit_at.pop(peer.uuid_hex, None)
        self._emit(
            {
                "type": "EXIT",
                "peer_id": peer.uuid_hex,
                "peer_name": pname,
            }
        )
        for group in gone_groups:
            self._restart_election_if_needed(group)

    async def _reap(self, now: float) -> None:
        # Gossip heartbeat + dead-link re-dial come first so hub links stay
        # healthy before peer timeouts are evaluated.
        await self._gossip_maintain(now)
        for key, p in list(self._pending.items()):
            if p.expired_at and now > p.expired_at:
                p.disconnect()
                self._pending.pop(key, None)
        self._maintain_elections(now)
        for peer in list(self._peers.values()):
            if peer.expired_at and now > peer.expired_at:
                await self._remove_peer(peer)
            elif peer.evasive_at and now > peer.evasive_at:
                # Probe every cycle, but emit EVASIVE to the application at
                # most once per _evasive_emit_interval: the event means "the
                # peer went quiet", not "still quiet" (matches libzyre, which
                # fires per cycle — noisy for idle peers).
                last = self._evasive_emit_at.get(peer.uuid_hex, 0.0)
                if now - last >= self._evasive_emit_interval:
                    self._evasive_emit_at[peer.uuid_hex] = now
                    self._emit(
                        {
                            "type": "EVASIVE",
                            "peer_id": peer.uuid_hex,
                            "peer_name": self._pname(peer),
                        }
                    )
                peer.send_cmd(PING)
                peer.evasive_at = now + self._ev_timeout / 1000.0

    @staticmethod
    def _pname(peer: Peer) -> str:
        name = peer.name
        if isinstance(name, bytes):
            return name.decode(errors="replace")
        return name or "unknown"

    @staticmethod
    def _bdec(value: bytes | str) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else value

    def _emit(self, event: dict) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._event_queue.put_nowait(event)
