"""Curve security: encrypted peers talk, insecure peers stay out."""

import asyncio
import time

import pytest
import zmq
import zmq.auth
from zmq.auth.thread import ThreadAuthenticator

from zre.node import ZreNode

DOMAIN = "zre-test"


def allow_any(ctx):
    authenticator = ThreadAuthenticator(ctx)
    authenticator.start()
    authenticator.configure_curve(domain="*", location=zmq.auth.CURVE_ALLOW_ANY)
    return authenticator


async def wait_event(node, event_type, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            event = node._event_queue.get_nowait()
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.05)
    return None


async def quiet_drain(node, seconds=3.0):
    deadline = time.monotonic() + seconds
    found = []
    while time.monotonic() < deadline:
        while not node._event_queue.empty():
            found.append(node._event_queue.get_nowait())
        await asyncio.sleep(0.05)
    return found


class TestCurve:
    async def test_secure_pair_talks(self):
        first = ZreNode("sec-a")
        second = ZreNode("sec-b")
        public_first, secret_first = zmq.curve_keypair()
        public_second, secret_second = zmq.curve_keypair()
        first.set_zcert(public_first, secret_first)
        second.set_zcert(public_second, secret_second)
        first.set_zap_domain(DOMAIN)
        second.set_zap_domain(DOMAIN)
        first.set_beacon_peer_port(24921)
        second.set_beacon_peer_port(24922)
        await first.start()
        await second.start()
        auth_first = allow_any(first._ctx)
        auth_second = allow_any(second._ctx)
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        try:
            await first.connect_peer("127.0.0.1", 24922, public_key=public_second)
            await second.connect_peer("127.0.0.1", 24921, public_key=public_first)
            assert await wait_event(first, "ENTER") is not None
            assert await wait_event(second, "ENTER") is not None
            await first.join("VAULT")
            await second.join("VAULT")
            assert await wait_event(second, "JOIN") is not None
            await first.shout("VAULT", b"secret Rosie")
            event = await wait_event(second, "SHOUT")
            assert event is not None
            assert event["payload"] == b"secret Rosie"
            from zmq.utils import z85 as z85_codec

            assert first._curve_public == z85_codec.decode(public_first)
            assert second._curve_public == z85_codec.decode(public_second)
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.3)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()
            auth_first.stop()
            auth_second.stop()

    async def test_insecure_node_cannot_join_secure_mesh(self):
        first = ZreNode("sec-a")
        second = ZreNode("sec-b")
        public_first, secret_first = zmq.curve_keypair()
        public_second, secret_second = zmq.curve_keypair()
        first.set_zcert(public_first, secret_first)
        second.set_zcert(public_second, secret_second)
        first.set_beacon_peer_port(24923)
        second.set_beacon_peer_port(24924)
        await first.start()
        await second.start()
        auth_first = allow_any(first._ctx)
        auth_second = allow_any(second._ctx)
        first_task = asyncio.create_task(first.run())
        second_task = asyncio.create_task(second.run())
        outsider = ZreNode("plain")
        outsider.set_beacon_peer_port(24925)
        try:
            await first.connect_peer("127.0.0.1", 24924, public_key=public_second)
            await second.connect_peer("127.0.0.1", 24923, public_key=public_first)
            assert await wait_event(first, "ENTER") is not None
            await outsider.start()
            outsider_task = asyncio.create_task(outsider.run())
            try:
                await outsider.connect_peer("127.0.0.1", 24923)
                events = await quiet_drain(outsider, seconds=3.0)
                assert [event for event in events if event.get("type") == "ENTER"] == []
                live = [
                    peer_id for peer_id, peer in outsider._peers.items() if peer.ready
                ]
                assert live == []
            finally:
                outsider._running = False
                await asyncio.sleep(0.3)
                outsider_task.cancel()
                await asyncio.gather(outsider_task, return_exceptions=True)
                await outsider.stop()
        finally:
            first._running = False
            second._running = False
            await asyncio.sleep(0.3)
            first_task.cancel()
            second_task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)
            await first.stop()
            await second.stop()
            auth_first.stop()
            auth_second.stop()

    def test_bad_key_rejected(self):
        node = ZreNode("keys")
        with pytest.raises(ValueError):
            node.set_zcert(b"short", b"short")
        with pytest.raises(ValueError):
            node.set_zap_domain("")
