"""
Teardown-lifecycle tests for ZRE.

The production bug these guard against: `stop()` used to early-return
when the run loop had already set `_running = False` (the UUID-collision
path does that itself, and tests flip the flag directly). The early
return left the shared zmq.Context with live sockets open, and
`Context.term()` blocks until every socket is closed — the blocked
context then surfaced in pytest's `gc_collect_harder` as a hang inside
`Context.__del__` during GC at interpreter shutdown.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zre.node import ZreNode


def run(coro):
    return asyncio.run(coro)


def test_stop_after_manual_flag_clear_closes_context():
    """`_running = False` + stop() must tear the sockets and context down."""

    async def body():
        node = ZreNode("teardown")
        await node.start()
        run_task = asyncio.create_task(node.run())
        try:
            await asyncio.sleep(0.2)
            # The pattern every pre-existing test uses to end a node's
            # run loop; stop() used to be a no-op afterwards and leaked.
            node._running = False
            await asyncio.wait_for(run_task, timeout=5.0)
            await node.stop()
            assert node._inbox.closed
            assert node._beacon_sock is None
            assert node._ctx.closed
        finally:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    run(body())


def test_stop_is_idempotent():
    """Repeated stop() calls close and re-terminate cleanly, once."""

    async def body():
        node = ZreNode("teardown")
        await node.start()
        run_task = asyncio.create_task(node.run())
        try:
            await asyncio.sleep(0.2)
            first = node._ctx
            await asyncio.wait_for(
                asyncio.gather(node.stop(), run_task, return_exceptions=True),
                timeout=5.0,
            )
            assert first.closed
            # Calling stop() again must not raise, hang, or re-create state.
            await asyncio.wait_for(node.stop(), timeout=5.0)
            assert first.closed
        finally:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    run(body())


def test_stop_without_start_closes_context():
    """stop() on a never-started node releases the context too."""

    async def body():
        node = ZreNode("never-started")
        await node.stop()
        assert node._inbox is None
        assert node._ctx.closed

    run(body())
