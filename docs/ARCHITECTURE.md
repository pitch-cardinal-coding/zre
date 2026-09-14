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
  running loop (constructing `asyncio.Queue` outside a loop breaks on
  Python ≤ 3.9).
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
