#!/usr/bin/env python3
"""
Media Stream — Broadcast video chunks via SHOUT.

Streams an mp4 to all peers in group MEDIA. Receiver saves chunks and can
play with mpv/vlc. Any local .mp4 works — pass it with --file.

Usage:
  python3 examples/media_stream.py send --file ./sample.mp4 --port 15670
  python3 examples/media_stream.py send --list
  # list .mp4 files in --dir
  python3 examples/media_stream.py recv --out ./media_out --port 15670
"""

import argparse
import asyncio
import contextlib
import pathlib
import sys
import time
import uuid

from zre import ZreNode

DEFAULT_MEDIA_DIR = pathlib.Path.home() / "Videos"
CHUNK_SIZE = 64 * 1024
GROUP = b"MEDIA"


def list_media():
    if not DEFAULT_MEDIA_DIR.exists():
        print(f"No dir {DEFAULT_MEDIA_DIR}")
        return
    for parser in sorted(DEFAULT_MEDIA_DIR.glob("*.mp4")):
        print(f"{parser.name:20} {parser.stat().st_size / 1024 / 1024:.1f} MB")


async def send_media(
    filepath: pathlib.Path, port: int, interface: str | None, verbose: bool
):
    if not filepath.exists():
        print(f"File not found: {filepath}")
        if DEFAULT_MEDIA_DIR.exists():
            print(f"Available in {DEFAULT_MEDIA_DIR}:")
            list_media()
        return
    node = ZreNode(f"media-send-{uuid.uuid4().hex[:4]}")
    node.set_header("X-ROLE", "media-send")
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
    await node.join(GROUP)
    print(
        f"[send] {filepath.name} ({filepath.stat().st_size} bytes) — waiting for peers on {port}..."
    )
    for _ in range(30):
        if node.peers():
            break
        await asyncio.sleep(0.2)
    if not node.peers():
        print("[send] no peers yet, will still broadcast (peers may join late)")
    else:
        print(f"[send] peers {node.peers()} — starting stream")

    filesize = filepath.stat().st_size
    filename = filepath.name
    # header
    await node.shout(GROUP, f"MEDIA_START:{filename}:{filesize}".encode())
    print(f"[send] MEDIA_START {filename} {filesize}")
    sent = 0
    start = time.time()
    with filepath.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            await node.shout(GROUP, b"CHUNK:" + chunk)
            sent += len(chunk)
            if sent % (1024 * 1024) == 0:
                print(f"  {sent}/{filesize} bytes ({sent / filesize * 100:.1f}%)")
            # small yield to avoid flooding
            if sent % (512 * 1024) == 0:
                await asyncio.sleep(0.01)
    await node.shout(GROUP, b"MEDIA_END")
    elapsed = time.time() - start
    print(
        f"[send] MEDIA_END {filename} {sent} bytes in {elapsed:.1f}s ({sent / 1024 / 1024 / elapsed:.1f} MB/s)"
    )
    await asyncio.sleep(1)
    run_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(run_task, return_exceptions=True)
    await node.stop()


async def recv_media(
    out_dir: pathlib.Path, port: int, interface: str | None, verbose: bool
):
    out_dir.mkdir(parents=True, exist_ok=True)
    node = ZreNode(f"media-recv-{uuid.uuid4().hex[:4]}")
    node.set_header("X-ROLE", "media-recv")
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
    await node.join(GROUP)
    print(f"[recv] saving to {out_dir} — beacon {port}, waiting...")

    current_handle = None
    expected = 0
    received = 0
    current_name = ""

    async def handle():
        nonlocal current_handle, expected, received, current_name
        async for event in node.events():
            if event["type"] == "ENTER":
                print(f"  + {event.get('peer_name')} entered")
            elif event["type"] == "SHOUT" and event.get("group") == "MEDIA":
                payload = event.get("payload", b"")
                if payload.startswith(b"MEDIA_START:"):
                    parts = payload.decode(errors="replace").split(":", 2)
                    if len(parts) == 3:
                        _, filename, size_str = parts
                        expected = int(size_str)
                        current_name = filename
                        fp = out_dir / filename
                        current_handle = fp.open("wb")
                        received = 0
                        print(
                            f"[recv] MEDIA_START {filename} {expected} bytes from {event.get('peer_name')}"
                        )
                elif payload.startswith(b"CHUNK:"):
                    if current_handle:
                        chunk = payload[6:]
                        current_handle.write(chunk)
                        received += len(chunk)
                        if received % (1024 * 1024) == 0:
                            print(f"  {received}/{expected} bytes")
                elif payload == b"MEDIA_END":
                    if current_handle:
                        current_handle.close()
                        current_handle = None
                        print(
                            f"[recv] MEDIA_END {current_name} {received}/{expected} bytes"
                        )
                        if received == expected:
                            print(
                                f"[recv] saved {out_dir / current_name} — play with: mpv {out_dir / current_name}"
                            )
                        current_name = ""
                        expected = 0
                        received = 0

    try:
        await handle()
    except asyncio.CancelledError:
        pass
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        if current_handle:
            with contextlib.suppress(Exception):
                current_handle.close()
        await node.stop()


def main():
    parser = argparse.ArgumentParser(
        description="ZRE media stream (broadcast an mp4 to the MEDIA group)"
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    ps = sub.add_parser("send", help="stream file via SHOUT")
    ps.add_argument(
        "--file",
        type=pathlib.Path,
        default=None,
        help="mp4 to stream (e.g. ./sample.mp4); use --list to see files in --dir",
    )
    ps.add_argument(
        "--dir",
        type=pathlib.Path,
        default=DEFAULT_MEDIA_DIR,
        help="directory scanned by --list (default ~/Videos)",
    )
    ps.add_argument(
        "--list", action="store_true", help="list media files in --dir and exit"
    )
    ps.add_argument("--port", type=int, default=15670)
    ps.add_argument("--interface", type=str, default=None)
    ps.add_argument("--verbose", action="store_true")
    pr = sub.add_parser("recv", help="receive streams")
    pr.add_argument("--out", type=pathlib.Path, default=pathlib.Path("media_out"))
    pr.add_argument("--port", type=int, default=15670)
    pr.add_argument("--interface", type=str, default=None)
    pr.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.mode == "send" and args.list:
        list_media()
        return
    if args.mode == "send" and not args.file:
        print(
            "ERROR: specify --file PATH to stream (or --list to browse --dir)",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.mode == "send":
        try:
            asyncio.run(send_media(args.file, args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")
    else:
        try:
            asyncio.run(recv_media(args.out, args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
