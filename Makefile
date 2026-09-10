.PHONY: lint fix format test test-monitor clean check-format style ci help install-dev demo-chat demo-discovery demo-sensor demo-benchmark demo-all

# Python and tool — use the zre venv (override via make PYTHON=... RUFF=...)
PYTHON ?= /home/iam/devcode/.env/zre/bin/python3
RUFF   := $(dir $(PYTHON))ruff
PYTEST := pytest

# Source files — relative to repo root (this Makefile lives in zre/)
SRC := zre tests examples

# Default target — show help
help:
	@echo "Targets:"
	@echo "  make lint / fix / format / style / check-format / test / test-monitor / ci"
	@echo "  make example-chat / example-service-discovery / example-sensor-network / example-benchmark"
	@echo "  make demo-chat / demo-discovery / demo-sensor / demo-benchmark  (tmux isolated)"
	@echo "  make clean / install-dev"

# Lint with ruff
lint:
	@echo "Running ruff lint..."
	$(RUFF) check $(SRC)

# Format with ruff
format:
	@echo "Running ruff format..."
	$(RUFF) format $(SRC)

# Fix lint issues
fix:
	@echo "Fixing ruff lint..."
	$(RUFF) check --fix $(SRC)

# Style: fix + format + verify
style: fix
	$(RUFF) format $(SRC)
	$(RUFF) format --check $(SRC)
	@echo "style: clean"

# Check formatting without modifying
check-format:
	@echo "Checking format..."
	$(RUFF) format --check $(SRC)

# Run tests
test:
	@echo "Running tests..."
	$(PYTHON) -m $(PYTEST) tests/ -v

# Run tests with monitoring (py-spy RSS watcher)
test-monitor:
	@echo "Running tests with monitoring..."
	./run_tests_with_monitor.sh

# Clean build artifacts
clean:
	@echo "Cleaning..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache 2>/dev/null || true
	rm -f /tmp/zyre-py-spy-watch.log /tmp/zre-py-spy-watch.log 2>/dev/null || true

# Run all checks (CI pipeline)
ci: check-format lint test

# Install dev dependencies
install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

# Tmux helpers
TMUX := tmux
DEMO_PREFIX := zre-demo

# Run examples (direct, without tmux — for quick manual test)
example-chat:
	$(PYTHON) examples/chat.py --help
	@echo "Usage: $(PYTHON) examples/chat.py <name> [--port PORT] [--interface IFACE]"

example-service-discovery:
	$(PYTHON) examples/service_discovery.py --help

example-sensor-network:
	$(PYTHON) examples/sensor_network.py --help

example-benchmark:
	$(PYTHON) examples/benchmark.py --help

# Tmux-isolated demos — each spawns server+client in split panes
demo-chat:
	@echo "Starting tmux demo-chat (server + 2 clients)..."
	$(TMUX) kill-session -t $(DEMO_PREFIX)-chat 2>/dev/null || true
	$(TMUX) new-session -d -s $(DEMO_PREFIX)-chat "$(PYTHON) examples/chat.py alice --port 5670; echo '--- alice done ---'; read"
	$(TMUX) split-window -h -t $(DEMO_PREFIX)-chat "$(PYTHON) examples/chat.py bob --port 5670; echo '--- bob done ---'; read"
	$(TMUX) split-window -v -t $(DEMO_PREFIX)-chat:0.1 "$(PYTHON) examples/chat.py charlie --port 5670; echo '--- charlie done ---'; read"
	$(TMUX) select-layout -t $(DEMO_PREFIX)-chat tiled
	@echo "Attached to tmux session $(DEMO_PREFIX)-chat — detach with Ctrl-b d, kill with tmux kill-session -t $(DEMO_PREFIX)-chat"
	$(TMUX) attach -t $(DEMO_PREFIX)-chat || true

demo-discovery:
	@echo "Starting tmux service-discovery demo..."
	$(TMUX) kill-session -t $(DEMO_PREFIX)-discovery 2>/dev/null || true
	$(TMUX) new-session -d -s $(DEMO_PREFIX)-discovery "$(PYTHON) examples/service_discovery.py registry --port 5670; read"
	$(TMUX) split-window -h -t $(DEMO_PREFIX)-discovery "$(PYTHON) examples/service_discovery.py service my-svc api 8080 --port 5670; read"
	$(TMUX) split-window -h -t $(DEMO_PREFIX)-discovery "$(PYTHON) examples/service_discovery.py client --port 5670; read"
	$(TMUX) select-layout -t $(DEMO_PREFIX)-discovery even-horizontal
	$(TMUX) attach -t $(DEMO_PREFIX)-discovery || true

demo-sensor:
	@echo "Starting tmux sensor demo (aggregator + sensors)..."
	$(TMUX) kill-session -t $(DEMO_PREFIX)-sensor 2>/dev/null || true
	$(TMUX) new-session -d -s $(DEMO_PREFIX)-sensor "$(PYTHON) examples/sensor_network.py aggregator --port 5670; read"
	$(TMUX) split-window -h -t $(DEMO_PREFIX)-sensor "$(PYTHON) examples/sensor_network.py sensors --port 5670; read"
	$(TMUX) attach -t $(DEMO_PREFIX)-sensor || true

demo-benchmark:
	$(PYTHON) examples/benchmark.py discovery --nodes 5 --port 5670

demo-all: demo-chat
