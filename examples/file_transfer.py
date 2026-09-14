#!/usr/bin/env python3
"""
P2P File Transfer — Direct peer-to-peer file sharing

Uses WHISPER for private file transfers between peers.
Works with any local file.

The sender needs the receiver's <peer_hex>: the UUID printed in the
receiver's first line, e.g. `[receiver] UUID: 17e68c17...  ...`. List live
receivers on the LAN from another terminal with:
  python3 file_transfer.py list --port <same-port>

Pass --uuid on both sides (any label, e.g. --uuid alice-laptop) to keep
peer_hex stable across restarts.

Usage:
  python3 file_transfer.py receive ./out --port 24110
  python3 file_transfer.py list --port 24110
  python3 file_transfer.py send <peer_hex> ./sample.mp4 --port 24110
"""

import argparse
import asyncio
import contextlib
import sys
import uuid
from pathlib import Path

from _common import (
    CollisionExit,
    add_uuid_arg,
    check_collision_event,
    exit_on_uuid_collision,
    parse_uuid,
)

from zre import UUIDCollisionError, ZreNode

CHUNK_SIZE = 64 * 1024


async def _spinup_node(
    label: str,
    port: int,
    interface: str | None,
    verbose: bool,
    uuid_hex: str | None = None,
):
    """Start a node, launch its run loop, wait for beacon discovery."""
    node = ZreNode(f"file-xfer-{label}")
    if uuid_hex:
        node.set_uuid(uuid_hex)
    if port:
        node.set_port(port)
    if interface:
        node.set_interface(interface)
    if verbose:
        node.set_verbose(True)
    await node.start()
    run_task = asyncio.create_task(node.run())
    print(f"[{label}] UUID: {node.peer_id_hex}  <- give THIS hex to the sender")
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    await node.join(b"FILE_XFER")
    await asyncio.sleep(0.3)
    return node, run_task


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
        check_collision_event(event)
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


async def peer_list_loop(node: ZreNode):
    """Live view of peers on the network — prints UUID hex per peer."""
    print(
        "Peers (copy the full 32-char hex after UUID: to use as <peer_hex>\n"
        "in `send <peer_hex> <file>`):\n"
    )
    known: dict[str, str] = {}
    async for event in node.events():
        check_collision_event(event)
        etype = event["type"]
        pid = event.get("peer_id", "")
        pname = event.get("peer_name", "?")
        if etype == "ENTER":
            known[pid] = pname
            print(f"  {pid}  {pname}")
        elif etype == "EXIT":
            known.pop(pid, None)
            print(f"  -- {pname} left ({pid[:8]}...)  [peers now: {len(known)}]")
        elif etype == "EVASIVE":
            pass


async def main_receive(
    output_dir: Path,
    port: int,
    interface: str | None,
    verbose: bool,
    uuid_hex: str | None = None,
):
    node, run_task = await _spinup_node(
        f"rx-{uuid.uuid4().hex[:6]}", port, interface, verbose, uuid_hex
    )
    print(f"[receiver] saving files into {output_dir} (beacon {port}, Ctrl-C to quit)")
    try:
        await receive_loop(node, output_dir)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await node.stop()


async def main_list(
    port: int, interface: str | None, verbose: bool, uuid_hex: str | None = None
):
    node, run_task = await _spinup_node(
        f"ls-{uuid.uuid4().hex[:6]}", port, interface, verbose, uuid_hex
    )
    print(f"[list] my uuid: {node.peer_id_hex} (beacon {port})")
    try:
        await peer_list_loop(node)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await node.stop()


async def main_send(
    peer_id: str,
    filepath: Path,
    port: int,
    interface: str | None,
    verbose: bool,
    uuid_hex: str | None = None,
):
    node, run_task = await _spinup_node(
        f"tx-{uuid.uuid4().hex[:6]}", port, interface, verbose, uuid_hex
    )
    # wait until peer is ready (present in peer list)
    if peer_id not in node.peers():
        print(f"[send] peer {peer_id[:8]} not yet discovered, waiting...")
        for _ in range(30):
            if peer_id in node.peers():
                break
            await asyncio.sleep(0.2)
    if peer_id not in node.peers():
        print(
            f"[send] peer {peer_id[:8]} not found.\n"
            "  - make sure the receiver runs with the same --port\n"
            "  - check the receiver's UUID line matches this peer_hex\n"
            f"  - discovered peers: {node.peers()}"
        )
    else:
        print(f"[send] peer {peer_id[:8]} ready, sending {filepath}")
        await send_file(node, peer_id, filepath)
        await asyncio.sleep(1)
    run_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run_task
    await node.stop()


def main():
    parser = argparse.ArgumentParser(description="ZRE file transfer")
    sub = parser.add_subparsers(dest="mode", required=True)
    pr = sub.add_parser("receive", help="receive files")
    pr.add_argument("output_dir", type=Path)
    pr.add_argument("--port", type=int, default=15670)
    pr.add_argument("--interface", type=str, default=None)
    add_uuid_arg(pr)
    pr.add_argument("--verbose", action="store_true")

    pl = sub.add_parser(
        "list",
        help="list live peers and their hex ids (use one as <peer_hex> in send)",
    )
    pl.add_argument("--port", type=int, default=15670)
    pl.add_argument("--interface", type=str, default=None)
    add_uuid_arg(pl)
    pl.add_argument("--verbose", action="store_true")

    ps = sub.add_parser("send", help="send file")
    ps.add_argument(
        "peer_id", type=parse_uuid, help="target peer hex id (or a --uuid label)"
    )
    ps.add_argument("filepath", type=Path, help="file to send (e.g. ./sample.mp4)")
    ps.add_argument("--port", type=int, default=15670)
    ps.add_argument("--interface", type=str, default=None)
    add_uuid_arg(ps)
    ps.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    try:
        if args.mode == "receive":
            asyncio.run(
                main_receive(
                    args.output_dir, args.port, args.interface, args.verbose, args.uuid
                )
            )
        elif args.mode == "list":
            asyncio.run(main_list(args.port, args.interface, args.verbose, args.uuid))
        else:
            asyncio.run(
                main_send(
                    args.peer_id,
                    args.filepath,
                    args.port,
                    args.interface,
                    args.verbose,
                    args.uuid,
                )
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
