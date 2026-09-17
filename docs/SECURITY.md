# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a vulnerability

Please report security vulnerabilities privately by email to
**30695966+ichux@users.noreply.github.com**. Do not open a public GitHub issue for anything you
believe is exploitable.

Include:

- The version you tested against (`python -c "import zre; print(zre.__version__)"`)
- Your OS and Python version
- A minimal reproduction (script or example invocation)
- Your assessment of impact

You can expect an initial response within 7 days. We will credit reporters in
the release notes unless you ask to remain anonymous.

## Scope and known limitations

Things that are **by design** and therefore not vulnerabilities:

- **ZRE traffic is plaintext unless you enable Curve.** RFC 36 defines no
  transport crypto, so by default group (`SHOUT`) and direct (`WHISPER`)
  payloads travel in plaintext over TCP, and beacons are plaintext UDP.
  Call `set_zcert(public, secret)` + `set_zap_domain(domain)` on every
  node (with a `zmq.auth` authenticator per process, since each node owns
  its context) to get CurveZMQ-encrypted, ZAP-authenticated links —
  secure nodes emit v3 beacons and refuse keyless peers. Treat LANs as
  untrusted either way, or add application-layer encryption on top (see
  `examples/secure_chat.py` for a pattern using the optional `secure`
  extra).
- **Beacon-based discovery is LAN-only and unauthenticated.** Any process that
  can send UDP packets to the beacon port can join the mesh and spoof peer
  names. This matches the RFC 36 threat model.
- **No rate limiting on inbound protocol messages.** A hostile LAN peer can
  flood the event queue (bounded at 1000 events) and CPU.

Things we do consider in scope:

- Bugs that let remote peers crash a node (unhandled exceptions from crafted
  wire input — the `Codec` decoder is supposed to reject malformed frames)
- Sequence/heartbeat logic that lets a peer bypass or permanently wedge
  discovery or reaping
- Anything that breaks the asyncio single-loop model (e.g. accidental
  blocking calls) in a way that stalls all peers on the same loop
