#!/usr/bin/env python3
"""
Multiplayer Game State Sync — Real-time game state synchronization

Usage:
  python game_sync.py client <name> [--port PORT]
  python game_sync.py server [--port PORT]
"""

import argparse
import asyncio
import contextlib
import json
import time
from dataclasses import asdict, dataclass

from zre import ZreNode


@dataclass
class PlayerState:
    player_id: str
    x: float
    y: float
    rotation: float
    health: int
    score: int
    timestamp: float


class GameClient:
    def __init__(self, player_name: str):
        self.player_name = player_name
        self.player_id = f"player-{player_name}"
        self.node = ZreNode(f"game-{player_name}")
        self.node.set_header("X-GAME", "demo")
        self.node.set_header("X-PLAYER", player_name)
        self.state = PlayerState(
            player_id=self.player_id,
            x=0.0,
            y=0.0,
            rotation=0.0,
            health=100,
            score=0,
            timestamp=time.time(),
        )
        self.other_players: dict[str, PlayerState] = {}
        self.running = True

    async def start(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        await self.node.join(b"GAME_WORLD")

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        await self.start(port, interface, verbose)
        print(f"[{self.player_name}] Joined game world (port {port})")

        async def broadcaster():
            while self.running:
                self.state.timestamp = time.time()
                payload = json.dumps(asdict(self.state)).encode()
                await self.node.shout(b"GAME_WORLD", payload)
                await asyncio.sleep(0.05)

        bc = asyncio.create_task(broadcaster())

        async def ev_loop():
            async for event in self.node.events():
                if event["type"] == "SHOUT" and event.get("group") == "GAME_WORLD":
                    await self._handle_game_message(event)

        try:
            await asyncio.gather(self.node.run(), ev_loop())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            self.running = False
            bc.cancel()
            with asyncio.CancelledError, contextlib.suppress(asyncio.CancelledError):
                await bc
            await self.node.stop()

    async def _handle_game_message(self, event):
        payload = event.get("payload", b"")
        peer_name = event.get("peer_name", "unknown")
        if peer_name == self.player_name:
            return
        try:
            data = json.loads(payload.decode())
            other_state = PlayerState(**data)
            self.other_players[other_state.player_id] = other_state
            print(
                f"[{self.player_name}] {peer_name} at ({other_state.x:.1f}, {other_state.y:.1f})"
            )
        except Exception:
            pass

    def move(self, dx: float, dy: float):
        self.state.x += dx
        self.state.y += dy


class GameServer:
    def __init__(self):
        self.node = ZreNode("game-server")
        self.node.set_header("X-ROLE", "server")
        self.players: dict[str, dict] = {}

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        await self.node.join(b"GAME_WORLD")
        print(f"[Server] Game server started (port {port})")

        async def ev_loop():
            async for event in self.node.events():
                if event["type"] == "SHOUT" and event.get("group") == "GAME_WORLD":
                    try:
                        payload = event.get("payload", b"")
                        data = json.loads(payload.decode())
                        player_id = data.get("player_id")
                        if player_id not in self.players:
                            print(f"[Server] {player_id} joined the world")
                        self.players[player_id] = data
                    except Exception:
                        pass

        try:
            await asyncio.gather(self.node.run(), ev_loop())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.node.stop()


def main():
    parser = argparse.ArgumentParser(description="ZRE game sync")
    sub = parser.add_subparsers(dest="mode", required=True)
    pc = sub.add_parser("client", help="run client")
    pc.add_argument("name", help="player name")
    pc.add_argument("--port", type=int, default=15670)
    pc.add_argument("--interface", type=str, default=None)
    pc.add_argument("--verbose", action="store_true")
    ps = sub.add_parser("server", help="run server")
    ps.add_argument("--port", type=int, default=15670)
    ps.add_argument("--interface", type=str, default=None)
    ps.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.mode == "client":
        client = GameClient(args.name)
        try:
            asyncio.run(client.run(args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")
    else:
        server = GameServer()
        try:
            asyncio.run(server.run(args.port, args.interface, args.verbose))
        except KeyboardInterrupt:
            print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
