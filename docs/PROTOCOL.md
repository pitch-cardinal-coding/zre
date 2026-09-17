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

Secure nodes (Curve configured) send 54-byte v3 beacons instead:

```
+---+---+---+------+ +------+------+---------------------------+
| Z | R | E | 0x03 | | UUID | port | 32-byte Curve public key  |
+---+---+---+------+ +------+------+---------------------------+
```

A secure node tosses v1 beacons without a key (downgrade protection,
mirroring libzyre); insecure nodes ignore the trailing key bytes.

## TCP Message Format

```
+--------+---------+---------+----------+------+
| 0xAA   | 0xA1    | version | sequence | ...  |
+--------+---------+---------+----------+------+
  Signature  Cmd     Proto v2  Cyclic seq
```

Commands: `HELLO(1)`, `WHISPER(2)`, `SHOUT(3)`, `JOIN(4)`, `LEAVE(5)`,
`PING(6)`, `PING_OK(7)`, `ELECT(8)`, `LEADER(9)`, `GOODBYE(10)`.

`ELECT` carries `group` + `challenger id` (short strings); `LEADER`
carries `group` + `leader id`. Both run the per-group bully election:
lowest peer id wins, supporters echo up the wave tree, the winner
broadcasts `LEADER`, and every member emits a local `LEADER` event once
its leader-message count covers the group. `GOODBYE` carries nothing and
is sent to every peer on gossip-mode shutdown (beacon mode uses the
port-0 beacon instead).

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

## Gossip hub protocol (zre-native)

UDP-free discovery runs through a hub (`python3 -m zre.gossip`) over
plain TCP. Frames are a 4-byte big-endian length plus a JSON object:

- node → hub: `{"cmd": "HELLO", "uuid": ...}` (subscribe),
  `{"cmd": "PUBLISH", "uuid": ..., "endpoint": ...}` (announce
  `tcp://host:port`, with `|z85-pubkey` appended when Curve is on),
  `{"cmd": "UNPUBLISH", "uuid": ...}` (leave).
- hub → node: `{"cmd": "SNAPSHOT", "known": [{uuid, endpoint}, ...]}`
  (current table on HELLO), `{"cmd": "DELIVER", "uuid": ..., "endpoint":
  ...}` (new/changed tuple, fanned out to every other link),
  `{"cmd": "FORGET", "uuid": ...}` (unpublished).

This protocol is zre-native, not czmq-`zgossip` wire compatible; the ZRE
peer traffic it bootstraps is byte-identical RFC 36. Nodes behind the
same hub should pin `--interface` (or set an advertised endpoint) on
multi-homed/VPN hosts so the published address is reachable — probing
picks the default-route IP otherwise.
