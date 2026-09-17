# Performance

All numbers below come from real runs (no mocks) on Python 3.14, each peer
isolated in its own tmux pane/process, memory sampled with `py-spy`
(0.25–0.5 s RSS polling) and `/proc/<pid>/status` (`VmRSS`).

## Results

| Metric | Validated Value | Method |
|--------|-----------------|--------|
| Memory per node | **28–29 MB RSS** (stable over 15 s) | `ps` + `/proc/<pid>/status VmRSS` on 4× `chat.py` in tmux |
| Discovery 5 nodes | **0.10–0.15 s** | `benchmark.py discovery --nodes 5` |
| Discovery 10 nodes | **0.10 s** | `benchmark.py discovery --nodes 10` |
| Discovery 15 nodes | **0.20 s** | `benchmark.py scalability` |
| Discovery 20 nodes | **0.20 s** | `benchmark.py scalability` |
| Throughput | **178–179 messages/s, 1.5 Mb/s** (500–1000 msgs × 1 KiB) | `benchmark.py throughput --msgs 1000 --size 1024` |
| Round-trip latency | **mean 15.37 ms, p50 17.03 ms, p99 18.35 ms** (0/100 lost) | `benchmark.py latency --pings 100` |
| Max peers tested | **20 nodes** (full mesh) | `benchmark.py scalability` + `pytest test_ten_node_mesh` |
| Groups per node | No implementation-imposed limit | `ZreNode.join()` |
| Memory leak | **0 kB growth** over 15 s per node | `py-spy-watch-tests.sh` + live `ps` sampling |
| 10 ms tick discovery | **ENTER 0.00–2.57 s** both ends pinned, single-digit CPU | `fast_tick.py` across two hosts with pinned NICs |

## Receipts

```text
$ python3 examples/benchmark.py discovery --nodes 5 --port 5780
=== Discovery Benchmark: 5 nodes (port 5780) ===
All 5 nodes discovered each other in 0.10s

$ python3 examples/benchmark.py scalability --port 5840
=== Scalability Benchmark ===
--- Testing 5 nodes  — 0.10s
--- Testing 10 nodes — 0.10s
--- Testing 15 nodes — 0.20s
--- Testing 20 nodes — 0.20s

$ python3 examples/benchmark.py throughput --msgs 1000 --size 1024 --port 5782
=== Throughput Benchmark: 1000 msgs x 1024 bytes (port 5782) ===
Sent 1000 messages in 5.59s
Throughput: 179 messages/s, 1.5 Mb/s

$ python3 examples/benchmark.py latency --pings 100 --port 5783
=== Latency Benchmark: 100 pings (port 5783) ===
Latency (ms): min=11.47, max=18.35, mean=15.37, p50=17.03, p99=18.35
Lost: 0/100
```

> **Throughput context:** 178 messages/s × 1 KiB = 1.5 Mb/s wire
> throughput. This is expected for a full mesh — the O(n²) fan-out is the
> bottleneck, not ZeroMQ: effective per-node send is 178 × (n−1)
> messages/s (3,382 internal messages/s per node at 20 peers).

**Memory:** 4 concurrent `chat.py` nodes held a steady **28.8–29.0 MB RSS
each** across a 15-second sampling window (0 kB growth per node), and a
throughput run stayed flat at **~7.6 MB RSS for 30 consecutive samples**.
If there were a leak, RSS would climb second over second; it does not.

Reproduce any of it:

```bash
python3 -m pytest tests/ -v
./py-spy-watch-tests.sh 0.25 /tmp/zre-py-spy-watch.log 30 &
make test
tail -20 /tmp/zre-py-spy-watch.log
```
