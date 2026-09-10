#!/usr/bin/env python3
"""
P2P File Transfer — Direct peer-to-peer file sharing

Uses WHISPER for private file transfers between peers.
Works with any local file.

Usage:
  python3 examples/file_transfer.py receive ./out --port 15670
  python3 examples/file_transfer.py send <peer_hex> ./sample.mp4 --port 15670
"""

import argparse
import asyncio
import contextlib
import uuid
from pathlib import Path

from zre import ZreNode

CHUNK_SIZE = 64 * 1024


async def send_file(node: ZreNode, peer_id: str, filepath: Path):
    if not filepath.exists():
        print(f"File not found: {filepath}")
        return
    filesize = filepath.stat().st_size
    filename = filepath.name
    header = f"FILE_START:{filename}:{filesize}".encode()
    await node.whisper(peer_id, header)
    print(f"Sending {filename} ({filesize} bytes) to {peer_id[:8]}...")
    sent = 0
    with filepath.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            await node.whisper(peer_id, b"CHUNK:" + chunk)
            sent += len(chunk)
            if sent % (1024 * 1024) == 0:
                print(f"  Progress: {sent}/{filesize} bytes")
    await node.whisper(peer_id, b"FILE_END")
    print(f"Transfer complete: {filename}")


async def receive_loop(node: ZreNode, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    current_file_handle = None
    expected_size = 0
    received = 0
    async for event in node.events():
        if event["type"] != "WHISPER":
            continue
        payload = event.get("payload", b"")
        peer = event.get("peer_name", "unknown")
        if payload.startswith(b"FILE_START:"):
            parts = payload.decode(errors="replace").split(":", 2)
            if len(parts) == 3:
                _, filename, size_str = parts
                expected_size = int(size_str)
                filepath = output_dir / filename
                current_file_handle = filepath.open("wb")
                received = 0
                print(f"Receiving {filename} ({expected_size} bytes) from {peer}...")
        elif payload.startswith(b"CHUNK:"):
            if current_file_handle:
                chunk = payload[6:]
                current_file_handle.write(chunk)
                received += len(chunk)
                if received % (1024 * 1024) == 0:
                    print(f"  Progress: {received}/{expected_size} bytes")
        elif payload == b"FILE_END":
            if current_file_handle:
                current_file_handle.close()
                current_file_handle = None
                print(f"Transfer complete from {peer}")


async def main_receive(
    output_dir: Path, port: int, interface: str | None, verbose: bool
):
    node = ZreNode(f"file-xfer-{uuid.uuid4().hex[:6]}")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    await node.join(b"FILE_XFER")
    print(f"Waiting for files in {output_dir}... (beacon {port})")
    try:
        await asyncio.gather(node.run(), receive_loop(node, output_dir))

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await node.stop()


async def main_send(
    peer_id: str, filepath: Path, port: int, interface: str | None, verbose: bool
):
    node = ZreNode(f"file-xfer-{uuid.uuid4().hex[:6]}")
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    # wait for beacon discovery (ENTER) before joining, to avoid seq race
    print(f"[send] waiting for peers on {port}...")
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    await node.join(b"FILE_XFER")
    await asyncio.sleep(0.3)
    # wait until peer is ready (present in peer list)
    if peer_id not in node.peers():
        print(f"Peer {peer_id[:8]} not yet discovered, waiting...")
        for _ in range(30):
            if peer_id in node.peers():
                break
            await asyncio.sleep(0.2)
    if peer_id not in node.peers():
        print(f"Peer {peer_id[:8]} not found, peers={node.peers()}")
    else:
        print(f"Peer {peer_id[:8]} ready, sending {filepath}")
        await send_file(node, peer_id, filepath)
        await asyncio.sleep(1)
    run_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run_task
    await node.stop()


def main():
    p = argparse.ArgumentParser(description="ZRE file transfer")
    sub = p.add_subparsers(dest="mode", required=True)
    pr = sub.add_parser("receive", help="receive files")
    pr.add_argument("output_dir", type=Path)
    pr.add_argument("--port", type=int, default=15670)
    pr.add_argument("--interface", type=str, default=None)
    pr.add_argument("--verbose", action="store_true")

    ps = sub.add_parser("send", help="send file")
    ps.add_argument("peer_id", help="target peer hex id")
    ps.add_argument("filepath", type=Path, help="file to send (e.g. ./sample.mp4)")
    ps.add_argument("--port", type=int, default=15670)
    ps.add_argument("--interface", type=str, default=None)
    ps.add_argument("--verbose", action="store_true")

    args = p.parse_args()
    try:
        if args.mode == "receive":
            asyncio.run(
                main_receive(args.output_dir, args.port, args.interface, args.verbose)
            )
        else:
            asyncio.run(
                main_send(
                    args.peer_id, args.filepath, args.port, args.interface, args.verbose
                )
            )
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
