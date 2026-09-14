# Monitoring & Debugging

## Test monitoring (py-spy)

```bash
# Run tests with memory/performance monitoring (requires py-spy)
./run_tests_with_monitor.sh
# uses python3 -m pytest tests/ -v
# and monitors RSS via py-spy every 0.25s, dumps via sudo py-spy every 30s

# Monitor specific test (custom intervals)
./py-spy-watch-tests.sh 0.25 /tmp/zre-py-spy-watch.log 30 &
python3 -m pytest tests/test_lan.py -v
# log at /tmp/zre-py-spy-watch.log
```

## Lint / format / CI

```bash
make lint          # ruff check zre tests examples
make check-format  # ruff format --check
make format        # ruff format
make ci            # check-format + lint + test
```

## Memory profiling with memray (optional)

```bash
PYTHONPATH=zre python3 -m memray run -o /tmp/zre-mem.bin examples/service_discovery.py registry --port 15670
python3 -m memray summary /tmp/zre-mem.bin
python3 -m memray stats /tmp/zre-mem.bin
```

## Tmux-isolated demos

Each demo spawns server + clients in split panes (session `zre-demo-*`):

```bash
make demo-chat        # chat with 3 peers
make demo-discovery   # service discovery trio
make demo-sensor      # aggregator + sensors
make demo-benchmark   # discovery benchmark

# Manual equivalent:
tmux new-session -d -s zre-demo-chat "python3 examples/chat.py alice --port 15670"
tmux split-window -h -t zre-demo-chat "python3 examples/chat.py bob --port 15670"
tmux split-window -v -t zre-demo-chat:0.1 "python3 examples/chat.py charlie --port 15670"
tmux attach -t zre-demo-chat   # detach Ctrl-b d; kill: tmux kill-session -t zre-demo-chat
```

Guide for any example: pick a free beacon port, start peers in separate
panes with the same `--port`, observe `ENTER`/`JOIN`/`SHOUT`/`WHISPER`/
`EXIT` events. See `examples/README.md` for per-example walkthroughs.
