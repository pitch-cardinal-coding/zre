# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- UDP-free gossip discovery: `gossip_bind()` / `gossip_connect()` /
  `gossip_unpublish()` / `gossip_connect_curve()` plus a standalone hub
  (`python3 -m zre.gossip`) and a `GOODBYE` command for gossip shutdowns.
- CurveZMQ transport security (`set_zcert`, `set_zap_domain`) with 54-byte
  v3 beacons and downgrade protection.
- Per-group leader elections (`set_contest_in_group`, `LEADER` event,
  `ELECT(8)` / `LEADER(9)` commands).
- IPv6 for TCP dialing and endpoints (`set_ipv6`); the UDP beacon stays
  IPv4.
- Peer query helpers: `peers_by_group()`, `peer_groups()`,
  `peer_address()`, `peer_header_value()`.
- `whispers()` / `shouts()` convenience sends, `set_silent_timeout()`,
  `print()` / `dump()` / `version()`.
- `examples/gossip_mesh.py` and `examples/leader_election.py`, both
  covered by `scripts/cross_smoke.py` in both role directions.

### Fixed

- Pending dials now adopt by port when HELLO reports another interface IP.
- Gossip publishes a reachable endpoint on wildcard-bound hubs.
- Elections restart when a peer leaves via beacon EXIT.
- ROUTER handover so reconnects replace stale routes.

## [0.1.2] - 2026-09-13

### Changed

- Housekeeping release: no library code changes since 0.1.1.

[0.1.2]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.1.2

## [0.1.1] - 2026-09-13

### Added

- Pure Python ZRE (ZeroMQ Realtime Exchange Protocol, RFC 36) implementation
  built on asyncio + pyzmq — single event loop, no threads, no C extensions
  beyond pyzmq.
- Zero-configuration LAN peer discovery via UDP beacons (port 15670).
- Direct peer dialing with `connect_peer(host, port)` across subnets/WAN,
  including NAT port-forwarding via `set_advertised_endpoint()`.
- Group messaging (`join`/`leave`/`shout`) and direct messaging (`whisper`).
- Heartbeating with evasive/expired detection (`ENTER`/`EXIT`/`EVASIVE` events).
- Custom headers exchanged during discovery via `set_header()` (`X-` headers).
- Fixed ROUTER port (`set_beacon_peer_port`) and interface pinning
  (`set_interface`) for repeatable deployments.
- Wire-compatible with any RFC 36 peer: UDP beacon (`ZRE\x01` + UUID + port)
  and `HELLO/WHISPER/SHOUT/JOIN/LEAVE/PING/PING_OK` with `0xAAA1`/v2/seq.
- 65 tests covering LAN discovery, WAN direct connect, and protocol edge cases.
- 17 validated examples: chat, service discovery, sensor network, file
  transfer, media streaming, game sync, task queue, whiteboard, presence,
  config sync, health monitor, distributed lock, secure chat, benchmark.
- GitHub Actions release workflow (`.github/workflows/release.yml`): test
  matrix on Python 3.9/3.12/3.14, sdist+wheel build with `twine check` and a
  clean-venv import smoke test, and tokenless publishing to TestPyPI and PyPI
  via Trusted Publishing (OIDC) on `v*` tags.

### Changed

- Package author contact uses the GitHub-assigned noreply address
  (`30695966+ichux@users.noreply.github.com`) in package metadata,
  `SECURITY.md`, and `CODE_OF_CONDUCT.md`.

[0.1.1]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.1.1
