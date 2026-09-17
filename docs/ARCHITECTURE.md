# Architecture

## Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      ZreNode (asyncio)                      │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  UDP     │  │  ROUTER  │  │  API     │                  │
│  │  Beacon  │  │  Socket  │  │  Queue   │                  │
│  │  (port   │  │  (TCP    │  │  (join/  │                  │
│  │  15670)  │  │  socket) │  │  leave)  │                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
│       │             │             │                         │
│       ▼             ▼             ▼                         │
│  ┌─────────────────────────────────────┐                   │
│  │         asyncio Event Loop          │                   │
│  └─────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

## Design

- **Single asyncio event loop** — no threads, no race conditions.
- **zmq.Context (sync)** with `zmq.NOBLOCK` instead of `zmq.asyncio` —
  non-blocking I/O without the zmq.asyncio integration pitfalls.
- **UDP beacon** — non-blocking socket; asyncio drives the timing.
- **Main loop** — one `poll(inbox | beacon)` wait per iteration with a
  bounded timeout, then process whichever socket is ready, drain the API
  queue, reap timers, and `await asyncio.sleep(0)` so the rest of the
  application keeps running.
- **Lazy queues** — event/API queues are created on first use inside the
  running loop (a leftover from Python ≤ 3.9, where constructing
  `asyncio.Queue` outside a loop broke; kept because it is harmless).
- **Beacon send socket** — built lazily, bound to the pinned interface IP
  when one is set; targets are the subnet broadcast plus loopback. The
  listen socket stays on the wildcard: bound sockets miss broadcasts on
  bridged/multi-homed stacks.
- **EVASIVE emission** — probes run every reap cycle (like libzyre), but
  the EVASIVE event is emitted at most once per 20 s per quiet episode;
  app-level traffic or a fresh HELLO re-arms it.
- **UUID-collision guard** — an own-uuid beacon/HELLO from a foreign
  endpoint (a second node with our stable uuid) raises
  `UUIDCollisionError` out of `run()`; the node tears its sockets down
  without sending the goodbye beacon (that would announce the *other*
  node's departure to its peers).
- **Gossip mode** — `gossip_bind()` hosts a `GossipHub` in-process and
  `gossip_connect()` dials one; while either is set, the UDP beacon
  socket is never created. A `_gossip_loop` task (same event loop)
  PUBLISHes our endpoint, retries hub connects until one answers, and
  feeds `DELIVER`/`SNAPSHOT` tuples into the normal require-peer path,
  so gossip and beacon peers end up in the same tables. Departures send
  `GOODBYE` on ZRE links plus hub `UNPUBLISH`.
- **Elections** — per-`Group` contest flag + `{caw, father, erec, lrec,
  leader}` record; triggers on self/peer JOIN, HELLO-carried groups,
  LEAVE, peer expiry/removal, and goodbye. Lowest id wins; supporters
  echo toward their father; winner broadcasts LEADER; everyone emits the
  local LEADER event on lrec-complete. A lone contestant leads itself.
- **Curve/ZAP** — `set_zcert()` stores raw key bytes (32 B or z85);
  `start()` applies them to the ROUTER inbox as server + ZAP domain
  (`global` default, like libzyre); every dial applies our keypair plus
  the peer's server key (from v3 beacons, `|pubkey` endpoint suffixes,
  or explicit `connect_peer(..., public_key=...)`). Peers with unknown
  keys are refused, and secure nodes toss keyless v1 beacons.
