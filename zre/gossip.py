"""ZGS.1 gossip service — wire-compatible with czmq's zgossip.

This module mirrors czmq's src/zgossip.c and zgossip_msg (ZGS protocol v1)
so a Python node can host or join a gossip cluster together with libzyre
peers:

  transport   ZMTP. The hub binds a ROUTER socket, clients connect DEALER.
  envelope    ROUTER side: [routing_id, body]; DEALER side: [body].
  body        signature (0xAA 0xA0) + command (1 octet) + version (1 octet)

      HELLO    1   client -> hub. Hub answers with one PUBLISH per tuple.
      PUBLISH  2   key string | value longstr | ttl number-4 (msecs, 0 = never)
      PING     3   client -> hub; hub answers PONG.
      PONG     4   ignored; any inbound traffic counts as liveness.
      INVALID  5   protocol error; on the wire a client re-sends HELLO.

  behaviour   zgossip.c server_accept(): on PUBLISH — ignore duplicates
              (same key and value), store the tuple, deliver it to the
              owning application, and broadcast it to every connected
              client. Tuples with a ttl expire after ttl milliseconds.

Run a hub with ``python3 -m zre.gossip --port 15671`` — libzyre nodes can
``zyre_gossip_connect()`` to it like to a native zgossip hub.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import struct
import time

import zmq

logger = logging.getLogger("zre.gossip")

# ZGS.1 protocol constants (zgossip_msg.h).
ZGS_SIGNATURE = b"\xaa\xa0"
ZGS_VERSION = 1
ZGS_HELLO = 1
ZGS_PUBLISH = 2
ZGS_PING = 3
ZGS_PONG = 4
ZGS_INVALID = 5

MAX_STRING_BYTES = 255
MAX_LONGSTR_BYTES = 65535


def zgs_encode(
    command: int,
    key: str | bytes | None = None,
    value: str | bytes | None = None,
    ttl: int = 0,
) -> bytes:
    """Encode one ZGS body frame (without the routing envelope)."""
    body = ZGS_SIGNATURE + bytes([command, ZGS_VERSION])
    if command == ZGS_PUBLISH:
        raw_key = key.encode() if isinstance(key, str) else key
        raw_value = value.encode() if isinstance(value, str) else value
        if raw_key is None or raw_value is None:
            raise ValueError("PUBLISH needs key and value")
        if not 1 <= len(raw_key) <= MAX_STRING_BYTES:
            raise ValueError(
                f"key must be 1..{MAX_STRING_BYTES} bytes, got {len(raw_key)}"
            )
        if len(raw_value) > MAX_LONGSTR_BYTES:
            raise ValueError(
                f"value must be at most {MAX_LONGSTR_BYTES} bytes, got {len(raw_value)}"
            )
        body += bytes([len(raw_key)]) + raw_key
        body += struct.pack("!I", len(raw_value)) + raw_value
        body += struct.pack("!I", ttl)
    return body


def zgs_decode(body: bytes) -> dict | None:
    """Decode one ZGS body frame into ``{"command": ..., ...}``.

    Returns None for malformed frames (wrong signature, version or length).
    """
    if len(body) < 4:
        return None
    if body[0:2] != ZGS_SIGNATURE or body[3] != ZGS_VERSION:
        return None
    command = body[2]
    message: dict = {"command": command}
    if command == ZGS_PUBLISH:
        key_len = body[4]
        key = body[5 : 5 + key_len]
        if len(key) != key_len:
            return None
        offset = 5 + key_len
        if offset + 4 > len(body):
            return None
        (value_len,) = struct.unpack("!I", body[offset : offset + 4])
        offset += 4
        value = body[offset : offset + value_len]
        if len(value) != value_len:
            return None
        offset += value_len
        if offset + 4 > len(body):
            return None
        (ttl,) = struct.unpack("!I", body[offset : offset + 4])
        message["key"] = key
        message["value"] = value
        message["ttl"] = ttl
    elif command not in (ZGS_HELLO, ZGS_PING, ZGS_PONG, ZGS_INVALID):
        return None
    return message


class GossipHub:
    """ZGS hub on a pyzmq ROUTER socket.

    Mirrors czmq's zgossip server: a table of (key -> value, expiry) tuples,
    a PUBLISH snapshot per HELLO, duplicate-tolerant storage, and forwarding
    of every fresh tuple to every connected client.

    ``deliveries()`` returns tuples accepted since the last call, so the
    embedding node (or the standalone ``main``) can act on them the way
    zgossip's actor pipe delivers ``DELIVER key value`` upstream.
    """

    def __init__(self, ctx: zmq.Context) -> None:
        self._ctx = ctx
        self._socket: zmq.Socket | None = None
        # key -> (value, expiry in time.monotonic() seconds; 0.0 = never)
        self._tuples: dict[bytes, tuple[bytes, float]] = {}
        self._pending: list[tuple[bytes, bytes]] = []
        # routing ids of clients seen on the ROUTER socket — the equivalent
        # of zgossip's per-client table (created on any inbound message).
        self._clients: set[bytes] = set()

    @property
    def socket(self) -> zmq.Socket:
        assert self._socket is not None
        return self._socket

    def bind(self, endpoint: str) -> str:
        """Bind the hub; returns the resolved endpoint (port filled in)."""
        if self._socket is not None:
            raise RuntimeError("GossipHub is already bound")
        sock = self._ctx.socket(zmq.ROUTER)
        sock.setsockopt(zmq.LINGER, 0)
        try:
            sock.bind(endpoint)
        except zmq.ZMQError:
            with contextlib.suppress(Exception):
                sock.close(linger=0)
            raise
        self._socket = sock
        resolved = sock.getsockopt(zmq.LAST_ENDPOINT).decode()
        logger.info("gossip hub bound %s", resolved)
        return resolved

    def unpublish(self, key: str | bytes) -> None:
        """Mirror zgossip's UNPUBLISH actor method: local table delete."""
        raw = key.encode() if isinstance(key, str) else key
        self._tuples.pop(raw, None)

    def publish(self, key: str | bytes, value: str | bytes, ttl: int = 0) -> None:
        """Insert a tuple into the hub table and broadcast it to every
        client (the zgossip PUBLISH actor method mirror)."""
        raw_key = key.encode() if isinstance(key, str) else key
        raw_value = value.encode() if isinstance(value, str) else value
        self._accept(raw_key, raw_value, ttl, time.monotonic())

    def deliveries(self) -> list[tuple[bytes, bytes]]:
        """Tuples accepted since the last call, as (key, value)."""
        pending, self._pending = self._pending, []
        return pending

    def tuples(self) -> dict[str, str]:
        """Snapshot of stored tuples (decoded to str)."""
        return {
            key.decode(errors="replace"): value.decode(errors="replace")
            for key, (value, _expiry) in self._tuples.items()
        }

    def _send(self, routing_id: bytes, body: bytes) -> None:
        assert self._socket is not None
        # A dead client's routing id fails with ROUTER_MANDATORY unset only
        # on the next poll; dropping silently mirrors zgossip's
        # fire-and-forget forwarding.
        with contextlib.suppress(zmq.ZMQError):
            self._socket.send_multipart([routing_id, body], zmq.NOBLOCK)

    def _accept(self, key: bytes, value: bytes, ttl: int, now: float) -> None:
        stored = self._tuples.get(key)
        if stored is not None and stored[0] == value:
            return  # duplicate tuple, do nothing
        expiry = now + ttl / 1000.0 if ttl > 0 else 0.0
        self._tuples[key] = (value, expiry)
        self._pending.append((key, value))
        # Broadcast to every client; the originator re-accepts its own
        # tuple as a duplicate, exactly like the zgossip engine does.
        for routing_id in list(self._clients):
            self._send(routing_id, zgs_encode(ZGS_PUBLISH, key, value, ttl))

    def _reap(self, now: float) -> None:
        for key in [
            key for key, (_v, exp) in self._tuples.items() if exp and now > exp
        ]:
            self._tuples.pop(key, None)

    def handle_socket(self) -> list[tuple[bytes, bytes]]:
        """Drain inbound ZMTP messages; returns accepted (key, value) tuples."""
        if self._socket is None:
            return []
        now = time.monotonic()
        self._reap(now)
        while True:
            try:
                frames = self._socket.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break
            except zmq.ZMQError:
                break
            if len(frames) != 2:
                continue
            routing_id, body = frames
            self._clients.add(routing_id)
            message = zgs_decode(body)
            if message is None:
                continue
            command = message["command"]
            if command == ZGS_HELLO:
                # Snapshot: one PUBLISH per stored tuple.
                for key, (value, _expiry) in self._tuples.items():
                    self._send(routing_id, zgs_encode(ZGS_PUBLISH, key, value, 0))
            elif command == ZGS_PUBLISH:
                self._accept(message["key"], message["value"], message["ttl"], now)
            elif command == ZGS_PING:
                self._send(routing_id, zgs_encode(ZGS_PONG))
            elif command == ZGS_INVALID:
                # Mirror the server: reject the bad frame; a compliant
                # client answers by re-sending HELLO (remote_handler).
                self._send(routing_id, zgs_encode(ZGS_INVALID))
            # PONG: ignore, any traffic counts as liveness
        return self.deliveries()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None


async def _run_hub(host: str, port: int) -> None:
    ctx = zmq.Context()
    hub = GossipHub(ctx)
    resolved = hub.bind(f"tcp://{host}:{port}")
    logger.warning("gossip hub listening on %s", resolved)
    poller = zmq.Poller()
    poller.register(hub.socket, zmq.POLLIN)
    try:
        while True:
            try:
                events = poller.poll(timeout=200)
            except zmq.ZMQError:
                events = []
            if events:
                for key, value in hub.handle_socket():
                    logger.info(
                        "DELIVER %s %s",
                        key.decode(errors="replace"),
                        value.decode(errors="replace"),
                    )
            # Yield to the asyncio loop so signals/interrupts are seen.
            await asyncio.sleep(0)
    finally:
        hub.close()
        ctx.term()


def main() -> None:
    parser = argparse.ArgumentParser(description="zre gossip discovery hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=15671)
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    asyncio.run(_run_hub(args.host, args.port))


if __name__ == "__main__":
    main()
