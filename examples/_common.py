"""Shared helpers for the zre examples."""

import argparse
import asyncio
import hashlib
import sys

from zre import UUIDCollisionError


def parse_uuid(value: str) -> str:
    """Accept a 32-char hex UUID or a short label (hashed to a UUID).

    Labels are hashed with sha256 and truncated to 32 hex chars, so the
    same label always yields the same stable peer id — use distinct
    labels per peer (e.g. --uuid alice-lab, --uuid rx-lab).
    """
    try:
        raw = bytes.fromhex(value)
        if len(raw) == 16:
            return value.lower()
    except ValueError:
        pass
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def add_uuid_arg(parser: argparse.ArgumentParser) -> None:
    """Add a standard --uuid argument to an example's argument parser."""
    parser.add_argument(
        "--uuid",
        type=parse_uuid,
        default=None,
        metavar="HEX-OR-LABEL",
        help="stable peer id: 32-char hex or any label (e.g. --uuid alice-lab);"
        " survives restarts so whispers to this peer_hex keep working",
    )


def exit_on_uuid_collision(exc: UUIDCollisionError) -> int:
    """Turn a UUIDCollisionError into a friendly message; returns exit code.

    Call this from an example's exception handler for the block that runs
    the node (asyncio.run / asyncio.gather). See chat.py for the pattern.
    """
    print(
        f"\nERROR: {exc}\n"
        "  A node with this stable uuid is already running on the network.\n"
        "  Stable uuids must be unique among concurrently running peers —\n"
        "  stop the other node first, or pick a different --uuid label.",
        file=sys.stderr,
    )
    return 3


class CollisionExit(Exception):
    """Raised inside an example's event loop on a COLLISION event.

    An Exception (not BaseException) so asyncio gathers retrieve it
    cleanly; each example's main() maps it to exit code 3.
    """


def check_collision_event(event: dict) -> None:
    """Raise CollisionExit when an event loop sees a COLLISION event.

    Call this as the FIRST statement of every `async for event in
    node.events():` body. The node's run task raises UUIDCollisionError,
    but most examples swallow task exceptions in gather(...), so the event
    is the reliable delivery path. The example's main() catches
    CollisionExit and exits with code 3.
    """
    if event.get("type") == "COLLISION":
        print(
            f"\nERROR: {event.get('detail', 'uuid collision')}\n"
            "  A node with this stable uuid is already running on the network.\n"
            "  Stop the other node first, or pick a different --uuid label.",
            file=sys.stderr,
        )
        raise CollisionExit(event.get("detail", "uuid collision"))


def run_until_keyboard_interrupt(coro) -> int:
    """Run an example coroutine, mapping Ctrl-C and uuid collisions.

    Returns a process exit code so main() can `sys.exit(run_until_...)`.
    KeyboardInterrupt maps to 0 (Ctrl-C is the normal way to stop an
    example), UUIDCollisionError to 3 with a friendly explanation.
    """
    try:
        asyncio.run(coro)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
        return 0
    except UUIDCollisionError as exc:
        return exit_on_uuid_collision(exc)
