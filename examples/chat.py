#!/usr/bin/env python3
"""
Interactive Chat Room — Classic ZRE Example

Run multiple instances:
  python chat.py alice --port 15670
  python chat.py bob --port 15670
  python chat.py charlie --port 15670

All instances on the same LAN will discover each other automatically.
Commands: type message + Enter to SHOUT, /w <peer_hex> <msg> to whisper.
"""

import argparse
import asyncio
import sys
import uuid

from zre import ZreNode


async def chat_loop(name: str, port: int, interface: str | None, verbose: bool):
    node = ZreNode(name)
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
            t = event["type"]
            n = event.get("peer_name", "?")
            if t == "ENTER":
                print(f"  >> {n} joined the chat")
            elif t == "EXIT":
                print(f"  >> {n} left the chat")
            elif t == "JOIN":
                print(f"  >> {n} joined {event.get('group', '')}")
            elif t == "LEAVE":
                print(f"  >> {n} left {event.get('group', '')}")
            elif t == "SHOUT":
                pl = event.get("payload", b"")
                if isinstance(pl, bytes):
                    pl = pl.decode("utf-8", errors="replace")
                print(f"  {n}: {pl}")
            elif t == "WHISPER":
                pl = event.get("payload", b"")
                if isinstance(pl, bytes):
                    pl = pl.decode("utf-8", errors="replace")
                print(f"  {n} (private): {pl}")
            elif t == "EVASIVE":
                print(f"  >> {n} is evasive")

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
    p = argparse.ArgumentParser(description="ZRE chat example")
    p.add_argument(
        "name", nargs="?", default=f"user-{uuid.uuid4().hex[:4]}", help="node name"
    )
    p.add_argument(
        "--port", type=int, default=15670, help="beacon UDP port (default 15670)"
    )
    p.add_argument(
        "--interface", type=str, default=None, help="network interface or IP"
    )
    p.add_argument("--verbose", action="store_true", help="enable verbose logging")
    args = p.parse_args()
    try:
        asyncio.run(chat_loop(args.name, args.port, args.interface, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
