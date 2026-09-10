#!/usr/bin/env python3
"""
Real-time Collaborative Whiteboard — SHOUT strokes to group WHITEBOARD.

Each peer SHOUTs JSON stroke {x,y,color,peer}. All peers print strokes.
Covers Scenarios: real-time collaboration, Ar/Vis.

Usage:
  python3 examples/whiteboard.py alice --port 5670
  python3 examples/whiteboard.py bob --port 5670
"""

import argparse
import asyncio
import json
import random
import sys
import time

from zre import ZreNode

STROKE_COLORS = ["red", "green", "blue", "black", "orange"]


async def run_board(
    peer_name: str, port: int, interface: str | None, verbose: bool, demo: bool
):
    node = ZreNode(f"board-{peer_name}")
    node.set_header("X-ROLE", "whiteboard")
    node.set_header("X-PEER", peer_name)
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    await node.join(b"WHITEBOARD")
    print(
        f"[whiteboard {peer_name}] joined WHITEBOARD on {port}. Type 'x y color' or press Enter for demo strokes."
    )
    print(f"[whiteboard {peer_name}] peers: {node.peers()}")

    strokes: list[dict] = []

    async def handle_events():
        async for event in node.events():
            t = event["type"]
            if t == "ENTER":
                print(f"  >> {event.get('peer_name')} entered whiteboard")
            elif t == "EXIT":
                print(f"  >> {event.get('peer_name')} left")
            elif t == "SHOUT" and event.get("group") == "WHITEBOARD":
                try:
                    data = json.loads(event["payload"].decode())
                    strokes.append(data)
                    print(
                        f"  stroke from {event.get('peer_name')}: ({data.get('x')},{data.get('y')}) {data.get('color')} total={len(strokes)}"
                    )
                except Exception:
                    pass

    async def stdin_loop():
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.decode().strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    color = parts[2] if len(parts) > 2 else random.choice(STROKE_COLORS)
                except ValueError:
                    print("usage: x y [color]")
                    continue
            else:
                print("usage: x y [color]")
                continue
            stroke = {
                "x": x,
                "y": y,
                "color": color,
                "peer": peer_name,
                "ts": time.time(),
            }
            strokes.append(stroke)
            await node.shout(b"WHITEBOARD", json.dumps(stroke).encode())
            print(f"[you] stroke ({x},{y}) {color}")

    async def demo_loop():
        # auto-generate strokes if no tty or demo flag
        while True:
            await asyncio.sleep(2)
            stroke = {
                "x": random.randint(0, 800),
                "y": random.randint(0, 600),
                "color": random.choice(STROKE_COLORS),
                "peer": peer_name,
                "ts": time.time(),
            }
            strokes.append(stroke)
            await node.shout(b"WHITEBOARD", json.dumps(stroke).encode())
            if verbose:
                print(f"[demo] stroke ({stroke['x']},{stroke['y']}) {stroke['color']}")

    try:
        if demo or not sys.stdin.isatty():
            await asyncio.gather(handle_events(), demo_loop())
        else:
            await asyncio.gather(handle_events(), stdin_loop())
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await node.stop()


def main():
    p = argparse.ArgumentParser(description="ZRE whiteboard")
    p.add_argument("name", help="peer name")
    p.add_argument("--port", type=int, default=5670)
    p.add_argument("--interface", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--demo", action="store_true", help="auto-generate strokes (for testing)"
    )
    args = p.parse_args()
    try:
        asyncio.run(
            run_board(args.name, args.port, args.interface, args.verbose, args.demo)
        )
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
