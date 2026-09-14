# Protocol (ZRE / RFC 36)

zre speaks the [ZeroMQ Realtime Exchange Protocol (RFC 36)](https://rfc.zeromq.org/spec/36/)
on the wire, so it interoperates with any other compliant peer on the same
network (including libzyre itself).

## UDP Beacon (22 bytes)

```
+---+---+---+------+ +------+------+
| Z | R | E | 0x01 | | UUID | port |
+---+---+---+------+ +------+------+
  Header              Body
```

Sent as both `255.255.255.255` and the subnet broadcast on the pinned
interface (plus loopback targets so same-host tests just work). A beacon
with port `0` means "this peer is leaving" and triggers a local `EXIT`.

## TCP Message Format

```
+--------+---------+---------+----------+------+
| 0xAA   | 0xA1    | version | sequence | ...  |
+--------+---------+---------+----------+------+
  Signature  Cmd     Proto v2  Cyclic seq
```

Commands: `HELLO(1)`, `WHISPER(2)`, `SHOUT(3)`, `JOIN(4)`, `LEAVE(5)`,
`PING(6)`, `PING_OK(7)`.

## Conformance notes

- `HELLO` carries the sender's TCP endpoint (the authoritative dial-back
  address), its groups, group-status sequence, name and `X-` headers.
- The DEALER identity is `0x01 + uuid`; the ROUTER socket is mandatory.
- A peer that has been silent for `evasive_timeout` (default 5000 ms) is
  probed with `PING` every reap cycle and answers `PING_OK`; after
  `expired_timeout` (default 30000 ms) of silence it is dropped.
- Per-peer `want_seq` uses a lenient update (not drop) to absorb the
  `HELLO-before-JOIN` ordering race.
- `HELLO` is answered with `HELLO` so both sides reach readiness; duplicate
  `HELLO`s for a live peer only refresh it and can never restart the
  handshake or storm the event queue.
- Own-uuid beacons/HELLOs: a looped-back beacon (carrying our inbox port)
  and a HELLO from our own endpoint are ignored (libzyre semantics,
  zyre_node.c:1092); an own-uuid beacon with a *foreign* inbox port, or a
  HELLO from a foreign endpoint, means a second node runs with our stable
  uuid — the node raises `UUIDCollisionError` and stops.
- `Codec.decode` bounds-checks every field read.
