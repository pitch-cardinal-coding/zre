#!/usr/bin/env python3
"""
Interactive Chat Room — Classic ZRE Example

Run multiple instances:
  python chat.py alice --port 15670
  python chat.py bob --port 15670
  python chat.py charlie --port 15670

All instances on the same LAN will discover each other automatically.
Commands: type message + Enter to SHOUT, /w <peer_hex> <msg> to whisper.
The <peer_hex> is the UUID printed in each instance's first line.

Note: `X is quiet (heartbeat probe)` appears at most once per 20 s per
peer — the node probes an idle peer every second, but the library only
emits EVASIVE once per quiet episode. Only `X left the chat` (EXIT) means
a peer actually went away.
"""

import argparse
import asyncio
import sys
import uuid

from _common import (  # noqa: F401
    CollisionExit,
    add_uuid_arg,
    check_collision_event,
    exit_on_uuid_collision,
    parse_uuid,
)

from zre import UUIDCollisionError, ZreNode


async def chat_loop(
    name: str,
    port: int,
    interface: str | None,
    verbose: bool,
    uuid_hex: str | None = None,
):
    node = ZreNode(name)
    if uuid_hex:
        node.set_uuid(uuid_hex)
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(b"CHAT")

    print(
        f"[{name}] UUID: {node.peer_id_hex} beacon_port={node._beacon_port} inbox={node._inbox_port}"
    )
    print(f"[{name}] Joined CHAT group. Type messages and press Enter.\n")

    loop = asyncio.get_running_loop()

    async def event_printer():
        async for event in node.events():
            check_collision_event(event)
            etype = event["type"]
            peer_name = event.get("peer_name", "?")
            if etype == "ENTER":
                print(f"  >> {peer_name} joined the chat")
            elif etype == "EXIT":
                print(f"  >> {peer_name} left the chat")
            elif etype == "JOIN":
                print(f"  >> {peer_name} joined {event.get('group', '')}")
            elif etype == "LEAVE":
                print(f"  >> {peer_name} left {event.get('group', '')}")
            elif etype == "SHOUT":
                pl = event.get("payload", b"")
                if isinstance(pl, bytes):
                    pl = pl.decode("utf-8", errors="replace")
                print(f"  {peer_name}: {pl}")
            elif etype == "WHISPER":
                pl = event.get("payload", b"")
                if isinstance(pl, bytes):
                    pl = pl.decode("utf-8", errors="replace")
                print(f"  {peer_name} (private): {pl}")
            elif etype == "EVASIVE":
                # Core rate-limits EVASIVE to once per quiet episode, so
                # this prints at most once per 20 s while a peer idles.
                print(f"  >> {peer_name} is quiet (heartbeat probe)")

    async def stdin_reader():
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.strip()
            if line:
                if line.startswith(b"/w "):
                    parts = line[3:].split(b" ", 1)
                    if len(parts) == 2:
                        peer, msg = parts
                        await node.whisper(peer, msg)
                    else:
                        print("Usage: /w <peer_hex> <msg>")
                else:
                    await node.shout(b"CHAT", line)

    try:
        await asyncio.gather(
            node.run(),
            event_printer(),
            stdin_reader(),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await node.stop()


def main():
    parser = argparse.ArgumentParser(description="ZRE chat example")
    parser.add_argument(
        "name", nargs="?", default=f"user-{uuid.uuid4().hex[:4]}", help="node name"
    )
    parser.add_argument(
        "--port", type=int, default=15670, help="beacon UDP port (default 15670)"
    )
    parser.add_argument(
        "--interface", type=str, default=None, help="network interface or IP"
    )
    add_uuid_arg(parser)
    parser.add_argument("--verbose", action="store_true", help="enable verbose logging")
    args = parser.parse_args()
    try:
        asyncio.run(
            chat_loop(args.name, args.port, args.interface, args.verbose, args.uuid)
        )
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except CollisionExit:
        sys.exit(3)
    except UUIDCollisionError as exc:
        sys.exit(exit_on_uuid_collision(exc))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
    except CollisionExit:
        sys.exit(3)
    except UUIDCollisionError as exc:
        sys.exit(exit_on_uuid_collision(exc))
