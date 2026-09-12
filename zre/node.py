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

ZRE_SIGNATURE = 0xAAA0 | 1  # 0xAAA1 on wire, per RFC 36
ZRE_VERSION = 2
HELLO, WHISPER, SHOUT, JOIN, LEAVE, PING, PING_OK = range(1, 8)
BEACON_PORT = 15670
BEACON_SIZE = 22
# ioctl numbers for interface address/netmask lookup (Linux, stable ABI).
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
DEFAULT_EV_MS = 5000
DEFAULT_EX_MS = 30000
REAP_INTERVAL_MS = 1000


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
        return cmd, ver, seq, fields


class Peer:
    __slots__ = (
        "addr",
        "connected",
        "dealer",
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

    def connect(self, ctx: zmq.Context, our_uuid: bytes) -> bool:
        if self.connected:
            return True
        self.dealer = ctx.socket(zmq.DEALER)
        self.dealer.setsockopt(zmq.IDENTITY, b"\x01" + our_uuid)
        self.dealer.setsockopt(zmq.SNDHWM, 1000)
        self.dealer.setsockopt(zmq.SNDTIMEO, 0)
        self.dealer.setsockopt(zmq.LINGER, 0)
        try:
            self.dealer.connect(f"tcp://{self.addr}:{self.port}")
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

    def send(self, data: bytes, more: list | None = None) -> bool:
        if not self.connected or self.dealer is None:
            return False
        try:
            self.dealer.send_multipart([data] + (more or []), zmq.NOBLOCK)
            self.sent_seq += 1
            return True
        except zmq.ZMQError:
            return False

    def refresh(self, now: float, ev_ms: int, ex_ms: int) -> None:
        self.evasive_at = now + ev_ms / 1000.0
        self.expired_at = now + ex_ms / 1000.0

    def check_seq(self, cmd: int, seq: int) -> bool:
        self.want_seq = 1 if cmd == HELLO else self.want_seq + 1
        return self.want_seq != seq


class Group:
    __slots__ = ("name", "peers")

    def __init__(self, name: bytes) -> None:
        self.name = name
        self.peers = {}

    def join(self, peer: Peer) -> None:
        self.peers[peer.uuid_hex] = peer

    def leave(self, peer: Peer) -> None:
        self.peers.pop(peer.uuid_hex, None)

    def send(self, hdr: bytes, more: list | None = None) -> None:
        for peer in self.peers.values():
            if peer.connected:
                peer.send(hdr, more)


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
        self.name = name.encode() if isinstance(name, str) else name
        self.peer_id = _uuid_mod.uuid4().bytes
        self.peer_id_hex = self.peer_id.hex()
        self._groups: dict[bytes, Group] = {}
        self._own_groups: list[bytes] = []
        self._peers: dict[str, Peer] = {}
        self._pending: dict[str, Peer] = {}
        self._headers: dict[bytes, bytes] = {}
        self._status = 0
        self._seq = 1
        self._ev_timeout = DEFAULT_EV_MS
        self._ex_timeout = DEFAULT_EX_MS
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
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._api_queue: asyncio.Queue = asyncio.Queue()
        # Reusable poller
        self._inbox_poller = zmq.Poller()

    def _ensure_not_running(self, method: str) -> None:
        if self._running:
            raise RuntimeError(f"{method}() must be called before start()")

    def set_interface(self, iface: str) -> None:
        """Set network interface (e.g., 'eth0' or '192.168.1.100')."""
        self._ensure_not_running("set_interface")
        self._interface = iface

    def set_port(self, port: int) -> None:
        """Set UDP beacon port (default 15670). Use different port to isolate clusters."""
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
        self._advertised_endpoint = endpoint

    def set_verbose(self, verbose: bool = True) -> None:
        """Enable verbose logging."""
        self._verbose = bool(verbose)
        level = logging.DEBUG if self._verbose else logging.WARNING
        logging.basicConfig(level=level)
        self._logger.setLevel(level)

    def _log(self, msg: str, *args: object) -> None:
        if self._verbose:
            self._logger.debug(msg, *args)

    def _resolve_interface_ip(self) -> str:
        """Resolve interface name to IP; if already IP, return as-is."""
        if not self._interface:
            return ""
        iface = self._interface
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
        self._inbox.setsockopt(zmq.LINGER, 0)
        self._inbox.bind(bind_addr)
        ep = self._inbox.getsockopt(zmq.LAST_ENDPOINT).decode()
        self._inbox_port = int(ep.rsplit(":", 1)[1])
        self._inbox_poller.register(self._inbox, zmq.POLLIN)

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
        self._beacon_msg = (
            b"ZRE\x01" + self.peer_id + struct.pack("!H", self._inbox_port)
        )

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
        if not self._running:
            return
        self._running = False
        with contextlib.suppress(Exception):
            await self._send_beacon(port=0)
        for group in list(self._own_groups):
            with contextlib.suppress(Exception):
                await self._leave_group(group)
        for peer in list(self._peers.values()):
            peer.disconnect()
        self._peers.clear()
        for peer in list(self._pending.values()):
            peer.disconnect()
        self._pending.clear()
        if self._beacon_sock:
            with contextlib.suppress(Exception):
                self._inbox_poller.unregister(self._inbox)
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
        with contextlib.suppress(Exception):
            self._ctx.term()

    async def join(self, group: str | bytes) -> None:
        group_bytes = group.encode() if isinstance(group, str) else group
        await self._api_queue.put(("JOIN", group_bytes))

    async def leave(self, group: str | bytes) -> None:
        group_bytes = group.encode() if isinstance(group, str) else group
        await self._api_queue.put(("LEAVE", group_bytes))

    async def whisper(self, peer_hex: str | bytes, payload: str | bytes) -> None:
        peer_id = peer_hex.encode() if isinstance(peer_hex, str) else peer_hex
        raw_payload = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("WHISPER", peer_id, raw_payload))

    async def shout(self, group: str | bytes, payload: str | bytes) -> None:
        group_bytes = group.encode() if isinstance(group, str) else group
        raw_payload = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("SHOUT", group_bytes, raw_payload))

    async def connect_peer(self, host: str, port: int) -> None:
        """Connect directly to a peer by host:port (WAN-capable).

        Bypasses UDP beacon discovery. Use when:
        - Peer is on a different subnet / across WAN
        - You know the peer's address (from DNS, config, or manual exchange)
        - Beacon broadcast doesn't reach the peer

        After connection, exchanges HELLOs and emits ENTER event.
        """
        await self._api_queue.put(("CONNECT", host, port))

    async def _do_connect(self, host: str, port: int) -> None:
        """Execute direct peer connection (called from run loop).

        Creates a persistent placeholder peer and HELLOs through it. When
        the answer arrives, the placeholder is re-keyed by the real UUID
        (same dealer, same connection -- never closed and reopened, so the
        far-side routing entry stays valid).
        """
        for peer in self._peers.values():
            if peer.addr == host and peer.port == port and peer.connected:
                return

        key = f"pending-{host}:{port}"
        if key in self._pending:
            return
        peer = Peer(key, host, port)
        if not peer.connect(self._ctx, self.peer_id):
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
        return None

    def set_header(self, key: str | bytes, value: str | bytes) -> None:
        self._headers[(key if isinstance(key, bytes) else key.encode())] = (
            value if isinstance(value, bytes) else value.encode()
        )

    async def events(self) -> AsyncIterator[dict]:
        while self._running:
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

    async def run(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        last_reap = loop.time()
        reap_interval = REAP_INTERVAL_MS / 1000.0

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
                        data, addr = self._beacon_sock.recvfrom(BEACON_SIZE)
                    except (BlockingIOError, OSError):
                        continue
                    self._handle_beacon(data, addr)

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
            if self._send_sock is not None:
                self._send_sock.close()
            self._send_sock = None

    async def _join_group(self, group: bytes) -> None:
        if group in self._own_groups:
            return
        self._own_groups.append(group)
        self._status += 1
        if group not in self._groups:
            self._groups[group] = Group(group)
        self._log("SEND JOIN seq=%s group=%s", self._seq, group)
        hdr = Codec.encode_simple(JOIN, self._seq, group, self._status)
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        for peer in self._peers.values():
            peer.send(hdr)

    async def _leave_group(self, group: bytes) -> None:
        if group not in self._own_groups:
            return
        self._own_groups.remove(group)
        self._status += 1
        hdr = Codec.encode_simple(LEAVE, self._seq, group, self._status)
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        for peer in self._peers.values():
            peer.send(hdr)

    async def _do_whisper(self, peer_hex: bytes, payload: bytes) -> None:
        peer_id = peer_hex.decode() if isinstance(peer_hex, bytes) else peer_hex
        peer = self._peers.get(peer_id)
        if peer and peer.connected:
            self._log("SEND WHISPER seq=%s to %s", self._seq, peer_id[:8])
            peer.send(Codec.encode_simple(WHISPER, self._seq), [payload])
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1

    async def _do_shout(self, group: bytes, payload: bytes) -> None:
        grp = self._groups.get(group)
        if grp:
            grp.send(Codec.encode_simple(SHOUT, self._seq, group), [payload])
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1

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
        return socket.inet_ntoa(struct.pack("!I", addr | (~mask & 0xFFFFFFFF)))

    def _build_send_sock(self) -> None:
        old, self._send_sock = self._send_sock, None
        with contextlib.suppress(Exception):
            if old is not None:
                old.close()
        self._send_targets = [
            ("255.255.255.255", self._beacon_port),
            ("127.255.255.255", self._beacon_port),
            ("127.0.0.1", self._beacon_port),
        ]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        bind_ip = self._resolve_interface_ip()
        if bind_ip:
            sock.bind((bind_ip, 0))
            subnet = self._interface_broadcast()
            if subnet:
                self._send_targets = [
                    (subnet, self._beacon_port),
                    ("127.0.0.1", self._beacon_port),
                ]
        self._send_sock = sock

    async def _send_beacon(self, port: int | None = None) -> None:
        if port is None:
            beacon = self._beacon_msg
            if beacon is None:
                beacon = b"ZRE\x01" + self.peer_id + struct.pack("!H", self._inbox_port)
        else:
            beacon = b"ZRE\x01" + self.peer_id + struct.pack("!H", port)
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
        if len(data) != BEACON_SIZE or data[:3] != b"ZRE":
            return
        remote_uuid = data[4:20]
        remote_port = struct.unpack("!H", data[20:22])[0]
        if remote_uuid == self.peer_id:
            return
        rhex = remote_uuid.hex()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        now = loop.time()

        if remote_port == 0:
            peer = self._peers.pop(rhex, None)
            if peer:
                peer.disconnect()
                self._emit(
                    {
                        "type": "EXIT",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                    }
                )
            return
        if rhex in self._peers:
            self._peers[rhex].refresh(now, self._ev_timeout, self._ex_timeout)
            return

        if self._adopt_pending(addr[0], remote_port, rhex, now) is not None:
            self._send_hello(self._peers[rhex])
            return

        peer = Peer(rhex, addr[0], remote_port)
        if peer.connect(self._ctx, self.peer_id):
            peer.refresh(now, self._ev_timeout, self._ex_timeout)
            self._peers[rhex] = peer
            self._send_hello(peer)

    def _own_address_for(self, peer_addr: str) -> str:
        """Local address the peer should dial back (sends no traffic)."""
        ip = self._resolve_interface_ip()
        if ip:
            return ip
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((peer_addr, self._beacon_port))
            return sock.getsockname()[0]
        except OSError:
            return peer_addr
        finally:
            sock.close()

    def _public_endpoint_for(self, peer_addr: str) -> bytes:
        if self._advertised_endpoint:
            return self._advertised_endpoint.encode()
        return f"tcp://{self._own_address_for(peer_addr)}:{self._inbox_port}".encode()

    def _send_hello(self, peer: Peer) -> None:
        self._log(
            "SEND HELLO seq=1 to %s groups=%s", peer.uuid_hex[:8], self._own_groups
        )
        hdr = Codec.encode_hello(
            1,
            self._public_endpoint_for(peer.addr),
            self._own_groups,
            self._status,
            self.name,
            self._headers,
        )
        peer.send(hdr)

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
            await self._handle_hello(rhex, extra, peer)
            return

        if peer is None or not peer.ready:
            return
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
                grp = self._groups.get(group)
                if not grp:
                    grp = Group(group)
                    self._groups[group] = grp
                grp.join(peer)
                self._emit(
                    {
                        "type": "JOIN",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                        "group": self._bdec(group),
                    }
                )
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
        elif cmd == PING:
            peer.send(Codec.encode_simple(PING_OK, self._seq))
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1
        elif cmd == PING_OK:
            peer.refresh(now, self._ev_timeout, self._ex_timeout)

    async def _handle_hello(
        self, rhex: str, extra: dict, existing: Peer | None
    ) -> None:
        if rhex == self.peer_id_hex:
            return
        ep = extra.get("endpoint")
        if not ep:
            return
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
            try:
                hp = ep_str.split("://")[1]
                addr, port = hp.rsplit(":", 1)
                port = int(port)
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
                if peer.connect(self._ctx, self.peer_id):
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

    async def _remove_peer(self, peer: Peer) -> None:
        for group in list(peer.groups):
            grp = self._groups.get(group)
            if grp:
                grp.leave(peer)
        pname = self._pname(peer)
        peer.disconnect()
        self._peers.pop(peer.uuid_hex, None)
        self._emit(
            {
                "type": "EXIT",
                "peer_id": peer.uuid_hex,
                "peer_name": pname,
            }
        )

    async def _reap(self, now: float) -> None:
        for key, p in list(self._pending.items()):
            if p.expired_at and now > p.expired_at:
                p.disconnect()
                self._pending.pop(key, None)
        for peer in list(self._peers.values()):
            if peer.expired_at and now > peer.expired_at:
                await self._remove_peer(peer)
            elif peer.evasive_at and now > peer.evasive_at:
                self._emit(
                    {
                        "type": "EVASIVE",
                        "peer_id": peer.uuid_hex,
                        "peer_name": self._pname(peer),
                    }
                )
                peer.send(Codec.encode_simple(PING, self._seq))
                self._seq = (self._seq + 1) & 0xFFFF
                if self._seq == 0:
                    self._seq = 1
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
