"""UDP-free discovery hub for zre (gossip equivalent).

One well-known hub process relays endpoint announcements between nodes
that cannot reach each other with UDP beacons (cloud VPCs, containers,
WAN links). Nodes PUBLISH their uuid and TCP endpoint; the hub relays
each tuple to every other node as a DELIVER message.

Wire format: 4-byte big-endian length followed by a JSON object with a
``cmd`` field. Commands from a node are HELLO, PUBLISH and UNPUBLISH;
the hub answers with DELIVER messages plus a SNAPSHOT list on HELLO.

Run a hub with ``python3 -m zre.gossip --port 15671``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import struct

logger = logging.getLogger("zre.gossip")

HEADER = struct.Struct("!I")


def encode_message(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return HEADER.pack(len(body)) + body


async def read_message(reader: asyncio.StreamReader) -> dict | None:
    try:
        header = await reader.readexactly(HEADER.size)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    (size,) = HEADER.unpack(header)
    if size > 1_000_000:
        return None
    try:
        body = await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    try:
        return json.loads(body.decode())
    except ValueError:
        return None


class GossipHub:
    """Relay endpoint announcements between zre nodes."""

    def __init__(self) -> None:
        self._known: dict[str, str] = {}
        self._writers: set[asyncio.StreamWriter] = set()

    def snapshot(self) -> dict[str, str]:
        return dict(self._known)

    async def _broadcast(
        self, payload: dict, skip: asyncio.StreamWriter | None = None
    ) -> None:
        data = encode_message(payload)
        for writer in list(self._writers):
            if writer is skip:
                continue
            try:
                writer.write(data)
                await writer.drain()
            except ConnectionError:
                self._writers.discard(writer)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.add(writer)
        try:
            while True:
                message = await read_message(reader)
                if message is None:
                    return
                command = message.get("cmd")
                if command == "HELLO":
                    snapshot = [
                        {"uuid": uuid, "endpoint": endpoint}
                        for uuid, endpoint in self._known.items()
                    ]
                    writer.write(encode_message({"cmd": "SNAPSHOT", "known": snapshot}))
                    await writer.drain()
                elif command == "PUBLISH":
                    uuid = message.get("uuid", "")
                    endpoint = message.get("endpoint", "")
                    if uuid and endpoint and self._known.get(uuid) != endpoint:
                        self._known[uuid] = endpoint
                        await self._broadcast(
                            {"cmd": "DELIVER", "uuid": uuid, "endpoint": endpoint},
                            skip=writer,
                        )
                elif command == "UNPUBLISH":
                    uuid = message.get("uuid", "")
                    if uuid in self._known:
                        del self._known[uuid]
                        await self._broadcast({"cmd": "FORGET", "uuid": uuid})
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, RuntimeError):
                pass

    async def serve(self, host: str, port: int) -> asyncio.AbstractServer:
        return await asyncio.start_server(self.handle, host, port)


async def _run_hub(host: str, port: int) -> None:
    hub = GossipHub()
    server = await hub.serve(host, port)
    logger.warning("gossip hub listening on %s:%d", host, port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="zre gossip discovery hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=15671)
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.WARNING,
    )
    asyncio.run(_run_hub(args.host, args.port))


if __name__ == "__main__":
    main()
