#!/usr/bin/env python3
"""
Secure Chat — Encrypted messaging

Uses ChaCha20-Poly1305 for encryption. Requires `cryptography` package:
  pip install cryptography  or  pip install -e .[secure]

Usage:
  python secure_chat.py <name> [--shared-key HEX] [--port PORT]
"""

import argparse
import asyncio
import os
import sys

from zre import ZreNode

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError:
    ChaCha20Poly1305 = None  # type: ignore


class SecureChannel:
    def __init__(self, key: bytes | None = None):
        if ChaCha20Poly1305 is None:
            raise RuntimeError(
                "cryptography package required: pip install cryptography"
            )
        self.key = key or os.urandom(32)
        self.cipher = ChaCha20Poly1305(self.key)

    def encrypt(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, plaintext, associated_data)
        return nonce + ciphertext

    def decrypt(self, ciphertext: bytes, associated_data: bytes = b"") -> bytes:
        nonce = ciphertext[:12]
        actual = ciphertext[12:]
        return self.cipher.decrypt(nonce, actual, associated_data)


class SecureChatNode:
    def __init__(self, name: str, shared_key: bytes | None = None):
        self.name = name
        self.node = ZreNode(f"secure-{name}")
        self.node.set_header("X-ENCRYPTED", "true")
        self.channel = SecureChannel(shared_key)

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
        await self.node.join(b"SECURE_CHAT")

    async def send_encrypted(self, group: bytes, message: str):
        plaintext = f"{self.name}: {message}".encode()
        encrypted = self.channel.encrypt(plaintext)
        payload = b"ENCRYPTED:" + encrypted
        await self.node.shout(group, payload)

    async def whisper_encrypted(self, peer_hex: str, message: str):
        plaintext = f"{self.name} (private): {message}".encode()
        encrypted = self.channel.encrypt(plaintext)
        payload = b"ENCRYPTED:" + encrypted
        await self.node.whisper(peer_hex, payload)

    async def handle_event(self, event):
        etype = event["type"]
        peer = event.get("peer_name", "unknown")
        if etype == "SHOUT" and event.get("group") == "SECURE_CHAT":
            payload = event.get("payload", b"")
            if payload.startswith(b"ENCRYPTED:"):
                enc = payload[10:]
                try:
                    dec = self.channel.decrypt(enc).decode()
                    print(f"[{self.name}] {dec}")
                except Exception as exc:
                    print(
                        f"[{self.name}] Decrypt failed from {peer}: {exc} "
                        "(both ends need the same --shared-key)"
                    )
        elif etype == "WHISPER":
            payload = event.get("payload", b"")
            if payload.startswith(b"ENCRYPTED:"):
                enc = payload[10:]
                try:
                    dec = self.channel.decrypt(enc).decode()
                    print(f"[{self.name}] (private) {dec}")
                except Exception as exc:
                    print(
                        f"[{self.name}] Private decrypt failed: {exc} "
                        "(both ends need the same --shared-key)"
                    )

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        await self.start(port, interface, verbose)
        print(
            f"[{self.name}] Secure chat started. Type messages to send. (port {port})"
        )
        loop = asyncio.get_running_loop()

        async def event_handler():
            async for event in self.node.events():
                await self.handle_event(event)

        async def input_handler():
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
                            await self.whisper_encrypted(
                                peer.decode(), msg.decode(errors="replace")
                            )
                    else:
                        await self.send_encrypted(
                            b"SECURE_CHAT", line.decode(errors="replace")
                        )

        try:
            await asyncio.gather(self.node.run(), event_handler(), input_handler())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.node.stop()


def main():
    parser = argparse.ArgumentParser(description="ZRE secure chat")
    parser.add_argument("name", help="node name")
    parser.add_argument(
        "--shared-key", type=str, default=None, help="hex-encoded 32-byte shared key"
    )
    parser.add_argument("--port", type=int, default=15670)
    parser.add_argument("--interface", type=str, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if ChaCha20Poly1305 is None:
        print(
            "ERROR: cryptography not installed. Run: python3 -m pip install cryptography",
            file=sys.stderr,
        )
        sys.exit(1)
    shared_key = bytes.fromhex(args.shared_key) if args.shared_key else None
    node = SecureChatNode(args.name, shared_key)
    try:
        asyncio.run(node.run(args.port, args.interface, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
