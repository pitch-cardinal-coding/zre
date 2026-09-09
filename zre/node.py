"""
Pure Python ZRE (RFC 36) — Asyncio + pyzmq.

Architecture:
  - Single asyncio event loop (no threads, no race conditions)
  - Uses zmq.Context (sync) for sockets + zmq.NOBLOCK for non-blocking
  - asyncio loop for scheduling, NOT zmq.asyncio
  - UDP beacon: non-blocking socket + asyncio loop.sock_recvfrom
  - Follows C zyre node_actor main loop structure

Usage:
    node = ZreNode("my-app")
    await node.start()
    await node.join("CHAT")
    async for event in node.events():
        print(event)
    await node.shout("CHAT", b"Hello!")
    await node.stop()
"""

import asyncio
import contextlib
import fcntl
import logging
import select
import socket
import struct
import uuid as _uuid_mod

import zmq

ZRE_SIGNATURE = 0xAAA0 | 1  # 0xAAA1 on wire, per RFC 36
ZRE_VERSION = 2
HELLO, WHISPER, SHOUT, JOIN, LEAVE, PING, PING_OK = range(1, 8)
BEACON_PORT = 5670
BEACON_SIZE = 22
DEFAULT_EV_MS = 5000
DEFAULT_EX_MS = 30000
REAP_INTERVAL_MS = 1000


class Codec:
    @staticmethod
    def _s(buf, v):
        if isinstance(v, str):
            v = v.encode()
        return buf + bytes([len(v)]) + v

    @staticmethod
    def _l(buf, v):
        if isinstance(v, str):
            v = v.encode()
        return buf + struct.pack("!I", len(v)) + v

    @staticmethod
    def _rs(d, o):
        if o >= len(d):
            return None, o
        n = d[o]
        o += 1
        return (d[o : o + n], o + n) if o + n <= len(d) else (None, o)

    @staticmethod
    def _rl(d, o):
        if o + 4 > len(d):
            return None, o
        n = struct.unpack("!I", d[o : o + 4])[0]
        o += 4
        return (d[o : o + n], o + n) if o + n <= len(d) else (None, o)

    @staticmethod
    def encode_hello(seq, ep, groups, status, name, headers):
        b = struct.pack("!HBBH", ZRE_SIGNATURE, HELLO, ZRE_VERSION, seq)
        b = Codec._s(b, ep)
        gl = list(groups or [])
        b += struct.pack("!I", len(gl))
        for g in gl:
            b = Codec._l(b, g)
        b += struct.pack("B", status)
        b = Codec._s(b, name)
        hdr = headers or {}
        b += struct.pack("!I", len(hdr))
        for k, v in hdr.items():
            b = Codec._s(b, k)
            b = Codec._l(b, v)
        return b

    @staticmethod
    def encode_simple(cmd, seq, group=None, status=None):
        b = struct.pack("!HBBH", ZRE_SIGNATURE, cmd, ZRE_VERSION, seq)
        if group is not None:
            b = Codec._s(b, group)
        if status is not None:
            b += struct.pack("B", status)
        return b

    @staticmethod
    def decode(data):
        if len(data) < 6:
            return None
        sig = struct.unpack("!H", data[0:2])[0]
        if sig != ZRE_SIGNATURE:
            return None
        cmd, ver = data[2], data[3]
        if ver != ZRE_VERSION:
            return None
        seq = struct.unpack("!H", data[4:6])[0]
        o = 6
        x = {}
        if cmd == HELLO:
            ep, o = Codec._rs(data, o)
            if ep is None:
                return None
            if o + 4 > len(data):
                return None
            n = struct.unpack("!I", data[o : o + 4])[0]
            o += 4
            groups = []
            for _ in range(n):
                if o + 4 > len(data):
                    return None
                g, o = Codec._rl(data, o)
                if g is None:
                    return None
                groups.append(g)
            if o >= len(data):
                return None
            status = data[o]
            o += 1
            name, o = Codec._rs(data, o)
            if name is None:
                return None
            if o + 4 > len(data):
                return None
            nh = struct.unpack("!I", data[o : o + 4])[0]
            o += 4
            hdrs = {}
            for _ in range(nh):
                k, o = Codec._rs(data, o)
                if k is None:
                    return None
                v, o = Codec._rl(data, o)
                if v is None:
                    return None
                hdrs[k] = v
            x = {
                "endpoint": ep,
                "groups": groups,
                "status": status,
                "name": name,
                "headers": hdrs,
            }
        elif cmd in (SHOUT, JOIN, LEAVE):
            g, o = Codec._rs(data, o)
            if g is None:
                return None
            x["group"] = g
            if cmd in (JOIN, LEAVE):
                if o >= len(data):
                    return None
                x["status"] = data[o]
        return cmd, ver, seq, x


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

    def __init__(self, uuid_hex, addr, port):
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

    def connect(self, ctx, our_uuid):
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

    def disconnect(self):
        if self.dealer:
            with contextlib.suppress(Exception):
                self.dealer.close(linger=0)
            self.dealer = None
        self.connected = False
        self.ready = False

    def send(self, data, more=None):
        if not self.connected or self.dealer is None:
            return False
        try:
            self.dealer.send_multipart([data] + (more or []), zmq.NOBLOCK)
            self.sent_seq += 1
            return True
        except zmq.ZMQError:
            return False

    def refresh(self, now, ev_ms, ex_ms):
        self.evasive_at = now + ev_ms / 1000.0
        self.expired_at = now + ex_ms / 1000.0

    def check_seq(self, cmd, seq):
        self.want_seq = 1 if cmd == HELLO else self.want_seq + 1
        return self.want_seq != seq


class Group:
    __slots__ = ("name", "peers")

    def __init__(self, name):
        self.name = name
        self.peers = {}

    def join(self, p):
        self.peers[p.uuid_hex] = p

    def leave(self, p):
        self.peers.pop(p.uuid_hex, None)

    def send(self, hdr, more=None):
        for p in self.peers.values():
            if p.connected:
                p.send(hdr, more)


class ZreNode:
    """
    Async ZRE node. Single asyncio event loop, no threads.

    Uses zmq.Context (sync) with zmq.NOBLOCK for non-blocking I/O.
    This avoids all zmq.asyncio issues. asyncio is used for scheduling
    and non-blocking UDP reads only.

    Follows C zyre zyre_node_actor structure:
      poll(inbox | beacon | api_pipe) with timeout → process ready socket
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
        self._headers: dict[bytes, bytes] = {}
        self._status = 0
        self._seq = 1
        self._ev_timeout = DEFAULT_EV_MS
        self._ex_timeout = DEFAULT_EX_MS
        self._beacon_port = BEACON_PORT
        self._beacon_interval = 1.0
        # None = all interfaces, or IP / iface name
        self._interface = None
        # fixed ROUTER port if set
        self._fixed_port = None
        self._verbose = False
        self._logger = logging.getLogger(f"zre.{self.peer_id_hex[:6]}")

        # Synchronous zmq.Context — all operations use zmq.NOBLOCK
        self._ctx = zmq.Context()
        # zmq.Socket (ROUTER), sync
        self._inbox = None
        self._inbox_port = 0
        self._beacon_sock = None
        self._running = False
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._api_queue: asyncio.Queue = asyncio.Queue()
        # Reusable poller
        self._inbox_poller = zmq.Poller()

    def _ensure_not_running(self, method: str):
        if self._running:
            raise RuntimeError(f"{method}() must be called before start()")

    def set_interface(self, iface: str):
        """Set network interface (e.g., 'eth0' or '192.168.1.100')."""
        self._ensure_not_running("set_interface")
        self._interface = iface

    def set_port(self, port: int):
        """Set UDP beacon port (default 5670). Use different port to isolate clusters."""
        self._ensure_not_running("set_port")
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1-65535")
        self._beacon_port = int(port)

    def set_interval(self, interval_ms: int):
        """Set beacon interval in milliseconds (default 1000)."""
        self._ensure_not_running("set_interval")
        if interval_ms <= 0:
            raise ValueError("interval must be >0")
        self._beacon_interval = interval_ms / 1000.0

    def set_evasive_timeout(self, timeout_ms: int):
        """Set evasive timeout in ms (default 5000)."""
        self._ensure_not_running("set_evasive_timeout")
        self._ev_timeout = int(timeout_ms)

    def set_expired_timeout(self, timeout_ms: int):
        """Set expired timeout in ms (default 30000)."""
        self._ensure_not_running("set_expired_timeout")
        self._ex_timeout = int(timeout_ms)

    def set_beacon_peer_port(self, port: int):
        """Set fixed TCP port for ROUTER socket (default ephemeral)."""
        self._ensure_not_running("set_beacon_peer_port")
        if not 1 <= port <= 65535:
            raise ValueError("port must be 1-65535")
        self._fixed_port = int(port)

    def set_verbose(self, verbose: bool = True):
        """Enable verbose logging."""
        self._verbose = bool(verbose)
        level = logging.DEBUG if self._verbose else logging.WARNING
        logging.basicConfig(level=level)
        self._logger.setLevel(level)

    def _log(self, msg: str, *args):
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
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", iface.encode()[:15]),
            )
            return socket.inet_ntoa(packed[20:24])
        except OSError:
            pass
        finally:
            sock.close()
        # Try to resolve via getaddrinfo / ioctl fallback: use gethostbyname
        # For iface name like eth0, try to get IP via socket ioctl would need netifaces;
        # fallback to 0.0.0.0 bind and let OS choose, but return iface for binding.
        # We attempt to use socket.getaddrinfo; if fails, return "" (bind all).
        try:
            return socket.gethostbyname(iface)
        except Exception:
            return ""

    async def start(self):
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
        bind_ip = self._resolve_interface_ip()
        # Bind to beacon port on chosen interface; fallback to "" on failure
        try:
            self._beacon_sock.bind((bind_ip, self._beacon_port))
        except OSError as exc:
            self._log(
                "beacon bind (%s:%s) failed: %s, trying 0.0.0.0", bind_ip, self._beacon_port, exc
            )
            self._beacon_sock.bind(("", self._beacon_port))
        self._beacon_sock.setblocking(False)

        self._running = True
        self._log(
            "started name=%s id=%s port=%s beacon=%s",
            self.name,
            self.peer_id_hex[:8],
            self._inbox_port,
            self._beacon_port,
        )
        await self._send_beacon()

    async def stop(self):
        if not self._running:
            return
        self._running = False
        with contextlib.suppress(Exception):
            await self._send_beacon(port=0)
        for g in list(self._own_groups):
            with contextlib.suppress(Exception):
                await self._leave_group(g)
        for p in list(self._peers.values()):
            p.disconnect()
        self._peers.clear()
        if self._beacon_sock:
            with contextlib.suppress(Exception):
                self._inbox_poller.unregister(self._inbox)
            with contextlib.suppress(Exception):
                self._beacon_sock.close()
            self._beacon_sock = None
        for s in (self._inbox,):
            if s:
                with contextlib.suppress(Exception):
                    s.close(linger=0)
        with contextlib.suppress(Exception):
            self._ctx.term()

    async def join(self, group):
        g = group.encode() if isinstance(group, str) else group
        await self._api_queue.put(("JOIN", g))

    async def leave(self, group):
        g = group.encode() if isinstance(group, str) else group
        await self._api_queue.put(("LEAVE", g))

    async def whisper(self, peer_hex, payload):
        ph = peer_hex.encode() if isinstance(peer_hex, str) else peer_hex
        pl = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("WHISPER", ph, pl))

    async def shout(self, group, payload):
        g = group.encode() if isinstance(group, str) else group
        pl = payload.encode() if isinstance(payload, str) else payload
        await self._api_queue.put(("SHOUT", g, pl))

    def set_header(self, k, v):
        self._headers[(k if isinstance(k, bytes) else k.encode())] = (
            v if isinstance(v, bytes) else v.encode()
        )

    async def events(self):
        while self._running:
            try:
                yield await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def recv(self, timeout=1.0):
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def peers(self):
        return list(self._peers.keys())

    def own_groups(self):
        return list(self._own_groups)

    async def run(self):
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

            if now - getattr(self, "_last_beacon", 0) >= self._beacon_interval:
                await self._send_beacon()
                self._last_beacon = now

            if self._beacon_sock is not None:
                try:
                    readable, _, _ = select.select([self._beacon_sock], [], [], 0)
                    if readable:
                        data, addr = self._beacon_sock.recvfrom(BEACON_SIZE)
                        self._handle_beacon(data, addr)
                except (BlockingIOError, OSError) as exc:
                    self._log("beacon recv error: %s", exc)

            if self._inbox is not None:
                try:
                    items = dict(self._inbox_poller.poll(0))
                    if self._inbox in items:
                        frames = self._inbox.recv_multipart(zmq.NOBLOCK)
                        await self._handle_peer_msg(frames)
                except zmq.Again:
                    pass
                except zmq.ZMQError as exc:
                    self._log("inbox poll error: %s", exc)

            if now - last_reap >= reap_interval:
                await self._reap(now)
                last_reap = now

            await asyncio.sleep(0.005)

        # Send EXIT beacon on shutdown
        with contextlib.suppress(Exception):
            await self._send_beacon(port=0)

    async def _join_group(self, g):
        if g in self._own_groups:
            return
        self._own_groups.append(g)
        self._status += 1
        if g not in self._groups:
            self._groups[g] = Group(g)
        self._log("SEND JOIN seq=%s group=%s", self._seq, g)
        hdr = Codec.encode_simple(JOIN, self._seq, g, self._status)
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        for p in self._peers.values():
            p.send(hdr)

    async def _leave_group(self, g):
        if g not in self._own_groups:
            return
        self._own_groups.remove(g)
        self._status += 1
        hdr = Codec.encode_simple(LEAVE, self._seq, g, self._status)
        self._seq = (self._seq + 1) & 0xFFFF
        if self._seq == 0:
            self._seq = 1
        for p in self._peers.values():
            p.send(hdr)

    async def _do_whisper(self, peer_hex, payload):
        ph = peer_hex.decode() if isinstance(peer_hex, bytes) else peer_hex
        p = self._peers.get(ph)
        if p and p.connected:
            self._log("SEND WHISPER seq=%s to %s", self._seq, ph[:8])
            p.send(Codec.encode_simple(WHISPER, self._seq), [payload])
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1

    async def _do_shout(self, group, payload):
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
                0x891B,  # SIOCGIFNETMASK
                struct.pack("256s", self._interface.encode()[:15]),
            )
            mask = struct.unpack("!I", packed[20:24])[0]
        except OSError:
            return ""
        finally:
            sock.close()
        addr = struct.unpack("!I", socket.inet_aton(ip))[0]
        return socket.inet_ntoa(struct.pack("!I", addr | (~mask & 0xFFFFFFFF)))

    async def _send_beacon(self, port=None):
        if port is None:
            port = self._inbox_port
        beacon = b"ZRE\x01" + self.peer_id + struct.pack("!H", port)
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            bind_ip = self._resolve_interface_ip()
            if bind_ip:
                with contextlib.suppress(Exception):
                    s.bind((bind_ip, 0))
            # Send to the interface subnet broadcast first (reaches that
            # NIC like upstream zbeacon), then limited broadcast and
            # loopback for single-host testing.
            targets = [
                ("255.255.255.255", self._beacon_port),
                ("127.255.255.255", self._beacon_port),
                ("127.0.0.1", self._beacon_port),
            ]
            subnet = self._interface_broadcast()
            if subnet:
                targets.insert(0, (subnet, self._beacon_port))
            for target in targets:
                with contextlib.suppress(Exception):
                    s.sendto(beacon, target)
        except Exception as exc:
            self._log("beacon send failed: %s", exc)
        finally:
            if s:
                with contextlib.suppress(Exception):
                    s.close()

    def _handle_beacon(self, data, addr):
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
            p = self._peers.pop(rhex, None)
            if p:
                p.disconnect()
                self._emit(
                    {
                        "type": "EXIT",
                        "peer_id": rhex,
                        "peer_name": self._pname(p),
                    }
                )
            return
        if rhex in self._peers:
            self._peers[rhex].refresh(now, self._ev_timeout, self._ex_timeout)
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

    def _send_hello(self, peer):
        self._log("SEND HELLO seq=1 to %s groups=%s", peer.uuid_hex[:8], self._own_groups)
        hdr = Codec.encode_hello(
            1,
            f"tcp://{self._own_address_for(peer.addr)}:{self._inbox_port}".encode(),
            self._own_groups,
            self._status,
            self.name,
            self._headers,
        )
        peer.send(hdr)

    async def _handle_peer_msg(self, frames):
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
            self._log("seq mismatch %s expected %s got %s, updating", rhex[:8], peer.want_seq, seq)
            peer.want_seq = seq
            # continue, do not drop (robust to out-of-order HELLO/JOIN)

        peer.refresh(now, self._ev_timeout, self._ex_timeout)

        if cmd == WHISPER:
            pl = extra_frames[0] if extra_frames else b""
            self._emit(
                {
                    "type": "WHISPER",
                    "peer_id": rhex,
                    "peer_name": self._pname(peer),
                    "payload": pl,
                }
            )
        elif cmd == SHOUT:
            g = extra.get("group", b"")
            pl = extra_frames[0] if extra_frames else b""
            self._emit(
                {
                    "type": "SHOUT",
                    "peer_id": rhex,
                    "peer_name": self._pname(peer),
                    "group": self._bdec(g),
                    "payload": pl,
                }
            )
        elif cmd == JOIN:
            g = extra.get("group")
            if g:
                peer.groups.add(g)
                grp = self._groups.get(g)
                if not grp:
                    grp = Group(g)
                    self._groups[g] = grp
                grp.join(peer)
                self._emit(
                    {
                        "type": "JOIN",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                        "group": self._bdec(g),
                    }
                )
        elif cmd == LEAVE:
            g = extra.get("group")
            if g:
                peer.groups.discard(g)
                grp = self._groups.get(g)
                if grp:
                    grp.leave(peer)
                self._emit(
                    {
                        "type": "LEAVE",
                        "peer_id": rhex,
                        "peer_name": self._pname(peer),
                        "group": self._bdec(g),
                    }
                )
        elif cmd == PING:
            peer.send(Codec.encode_simple(PING_OK, self._seq))
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1
        elif cmd == PING_OK:
            peer.refresh(now, self._ev_timeout, self._ex_timeout)

    async def _handle_hello(self, rhex, extra, existing):
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
            await self._remove_peer(existing)

        is_new = rhex not in self._peers
        if is_new:
            try:
                hp = ep_str.split("://")[1]
                addr, port = hp.rsplit(":", 1)
                port = int(port)
                # If peer advertised 0.0.0.0, use beacon source addr instead
                if addr in ("0.0.0.0", "*"):
                    # fallback to existing peer addr if known, else keep as is
                    # We already have addr from beacon in existing case, otherwise we need to use beacon addr;
                    # Since we are in new-peer path, we don't have beacon addr here — keep 127.0.0.1 fallback
                    addr = "127.0.0.1"
            except Exception:
                return
            peer = Peer(rhex, addr, port)
            if peer.connect(self._ctx, self.peer_id):
                peer.refresh(now, self._ev_timeout, self._ex_timeout)
                self._peers[rhex] = peer
            else:
                return
        else:
            peer = self._peers[rhex]
            is_new = False

        peer.name = extra.get("name")
        peer.headers = extra.get("headers", {})
        peer.status = extra.get("status", 0)
        peer.ready = True
        if is_new:
            # send HELLO back to ensure mutual readiness (peer discovered via HELLO, not beacon)
            self._send_hello(peer)

        for g in extra.get("groups", []):
            grp = self._groups.get(g)
            if not grp:
                grp = Group(g)
                self._groups[g] = grp
            grp.join(peer)
            peer.groups.add(g)

        pname = self._pname(peer)
        self._emit(
            {
                "type": "ENTER",
                "peer_id": rhex,
                "peer_name": pname,
                "address": ep_str,
            }
        )
        for g in extra.get("groups", []):
            self._emit(
                {
                    "type": "JOIN",
                    "peer_id": rhex,
                    "peer_name": pname,
                    "group": self._bdec(g),
                }
            )

    async def _remove_peer(self, peer):
        for g in list(peer.groups):
            grp = self._groups.get(g)
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

    async def _reap(self, now):
        for p in list(self._peers.values()):
            if p.expired_at and now > p.expired_at:
                await self._remove_peer(p)
            elif p.evasive_at and now > p.evasive_at:
                self._emit(
                    {
                        "type": "EVASIVE",
                        "peer_id": p.uuid_hex,
                        "peer_name": self._pname(p),
                    }
                )
                p.send(Codec.encode_simple(PING, self._seq))
                self._seq = (self._seq + 1) & 0xFFFF
                if self._seq == 0:
                    self._seq = 1
                p.evasive_at = now + self._ev_timeout / 1000.0

    @staticmethod
    def _pname(p):
        n = p.name
        if isinstance(n, bytes):
            return n.decode(errors="replace")
        return n or "unknown"

    @staticmethod
    def _bdec(v):
        return v.decode(errors="replace") if isinstance(v, bytes) else v

    def _emit(self, event):
        with contextlib.suppress(asyncio.QueueFull):
            self._event_queue.put_nowait(event)
