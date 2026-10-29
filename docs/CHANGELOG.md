# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Stable tag pushes publish to TestPyPI and PyPI without a manual
  environment approval; pre-release tags still stop at TestPyPI. Add
  required reviewers to the environments to restore the gate.

## [0.3.0] - 2026-10-29

### Added

- **Android app integration (plain ZRE).** The app's native node, service,
  JNI surface, and NDK tooling are documented end to end. The app now
  runs plain ZRE; the earlier Curve-secured design, its evidence, and a
  restoration checklist are preserved in `scripts/ndk/CURVE-MESH.md`,
  with the removed implementation kept runnable in
  `scripts/ndk/curve-reference/`.
- `scripts/ndk/GETTING-STARTED.md` — fresh-machine walkthrough: toolchain,
  app build (local and build-host flows), emulator run, mesh trials,
  leak proof, release and deploy.
- `scripts/ndk/valgrind-harness/` — fake-JNI lifecycle and
  production-topology probes with run scripts and recorded zero-leak
  verdicts for both the plain and preserved Curve nodes.
- `phone_trial.py --plain` — plain-ZRE trial mode that matches the app's
  current mode (Curve remains the default for `zc_peer` trials).
- `scripts/ndk/Makefile`: build-host app pipeline (`app-sync`,
  `app-build`, `app-wait`, `app-log`, `app-pull`, `emu-install`,
  `emu-launch`, `emu-logcat`, `emu-bootlog`), plus `keys-xml`,
  `make_identity_xml.py` and `zre_feed.sh` for identity fixtures.
- `docs/LIBZYRE_INTEROP.md`, `docs/RELEASING.md`, and the NDK embedding
  guide (`scripts/ndk/ANDROID_LIBZYRE.md`).

### Changed

- **Gossip is wire-compatible with czmq `zgossip` (ZGS.1) now.** The hub
  binds a ZMQ ROUTER and clients connect DEALER; frames are the ZGS
  protocol (`0xAA 0xA0` signature, `PUBLISH key value ttl`), replacing
  the previous length-prefixed JSON scheme. `zre/gossip.py` was
  rewritten accordingly (codec unit-tested in `tests/test_gossip.py`) and
  is verified against a libzyre 2.0.1 peer on Android — a libzyre node
  can `zyre_gossip_connect()` straight to `python3 -m zre.gossip`.
- `gossip_unpublish()` is a local table delete (ZGS.1 has no forget
  command on the wire), mirroring zgossip's UNPUBLISH actor method.
- Version is single-sourced in `zre/__init__.py`; `pyproject.toml`
  declares `dynamic = ["version"]`, ending the two-file bump.
- Release pipeline: a tag must point at a commit on `main`; pre-release
  tags (`vX.Y.ZrcN`) publish to TestPyPI only; PyPI refuses a version
  that already exists (checked before upload).
- `interop/zc_peer.c`: Curve is always on (the `--curve` toggle is gone;
  keys come from `--curve-pub/--curve-sec` or an ephemeral zcert, and a
  broken pair is refused instead of running plain). The peer announces
  `X-IP` (always) and `X-MAC` (when the kernel allows) HELLO headers —
  the values the Android app reproduces.
- `scripts/ndk/`: `curve_keys.py` + `make curve-keys/check/release`
  manage the avedge-format key pair for the PC/`zc_peer` trials (the
  mobile app is plain since 2026-10-23, see `CURVE-MESH.md`); trial
  targets build their phone args from the avedge store.
- `zre_static_all.sh` checks out the pinned libzmq/czmq/zyre commits
  (full SHA) and creates its dependency directory, so a cleaned build
  host reproduces the verified stack instead of building HEAD.

### Fixed

- pytest could hang at interpreter shutdown (`term()` blocked inside
  `Context.__del__`): `ZreNode.stop()` tore down sockets/context only
  when the run loop was still marked running — the UUID-collision path
  (and every test that flips `_running` directly) leaked the sockets and
  blocked forever. `stop()` now tears down always and terminates via
  `destroy(linger=0)`; regression tests in `tests/test_teardown.py`.
- Gossip announcements use `zuuid_str()`-style UPPERCASE keys; the
  require-peer path now normalizes the uuid case, ending duplicate
  peers when Python nodes meet libzyre gossip clients.
- Hub links are kept alive with 1 s PING heartbeats and dead links are
  re-dialled after 5 s of silence (zgossip's socket-monitor behaviour).
- `make_identity_xml.py` XML-escapes Z85 values (`&`, `<`, `>`); an
  unescaped key made Android discard the SharedPreferences file.
- `app-sync` works through fish login shells (`--rsync-path`), and
  `app-log` now fails the make invocation when the build log says
  `BUILD FAILED` instead of silently succeeding.
- Packaging: `MANIFEST.in` and `license-files` point at `docs/LICENSE`
  and `docs/CHANGELOG.md`, so sdists now carry the license and changelog;
  the wheel metadata records `License-File`.

### Security

- Removed a generated trial arguments file (`scripts/ndk/args.txt`) that
  contained a Curve private key from version control, and ignored
  `scripts/ndk/args.txt`, `scripts/ndk/lab.env`, and `*.priv`.
- The NDK Makefile no longer ships a plaintext SSH password; password
  auth reads the git-ignored `scripts/ndk/lab.env` or the environment.

## [0.2.0] - 2026-09-17

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

[0.3.0]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.3.0
[0.2.0]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.2.0
[0.1.2]: https://github.com/pitch-cardinal-coding/zre/releases/tag/v0.1.2

## [0.1.1] - 2026-09-13

### Added

- Pure Python ZRE (ZeroMQ Realtime Exchange Protocol, RFC 36) implementation
  built on asyncio + pyzmq — single event loop, no threads, no C extensions
  beyond pyzmq.
- Zero-configuration LAN peer discovery via UDP beacons (port 5670).
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
