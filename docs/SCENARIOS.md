# Use Cases & Scenarios

ZRE gives you zero-configuration peer discovery and messaging on a LAN
(or direct dial across subnets). These are the scenarios that map onto it,
with the example that demonstrates each one — runnable commands live in
[`examples/README.md`](../examples/README.md).

## 🏠 Local Network Applications
- **Local service discovery** — services announce themselves on the LAN
  without a central registry → `service_discovery.py`
- **Clustering services** — microservices discover each other →
  `service_discovery.py`
- **Smart home automation** — devices discover and coordinate locally →
  `sensor_network.py`, `presence.py`
- **Local chat/messaging** — LAN chat without internet → `chat.py`,
  `secure_chat.py`

## 🤖 IoT & Embedded
- **IoT device coordination** — sensors/actuators discover and coordinate →
  `sensor_network.py`
- **Sensor networks** — distributed sensor data aggregation →
  `sensor_network.py`
- **Robot swarms** — coordinated robot behaviors → `game_sync.py`
- **Edge computing clusters** — ad-hoc clusters at the edge →
  `task_queue.py`

## 🎮 Real-time Applications
- **Multiplayer gaming** — LAN multiplayer without a central server →
  `game_sync.py`
- **Real-time collaboration** — whiteboards, editors, shared tools →
  `whiteboard.py`
- **Live streaming** — local audience interaction → `media_stream.py`
- **AR/VR multi-user** — shared augmented/virtual reality sessions →
  `whiteboard.py`

## 🏢 Enterprise & DevOps
- **Service mesh** — lightweight discovery for containers →
  `service_discovery.py`
- **Distributed task queues** — workers discover coordinators →
  `task_queue.py`
- **Configuration sync** — distributed config propagation →
  `config_sync.py`
- **Health monitoring** — peer health/status propagation →
  `health_monitor.py`

## 🔬 Research & Testing
- **Protocol research** — ZRE/RFC 36 experimentation → any example +
  `--verbose`
- **Network simulation** — testing mesh behaviors → `benchmark.py`
- **Distributed systems teaching** — demonstrations → the tour in
  [`examples/README.md`](../examples/README.md)
- **Chaos engineering** — partition tolerance: kill peers mid-run and
  watch `EVASIVE`/`EXIT` → `health_monitor.py`, `presence.py`

## 🌐 Specialized Scenarios
- **Disaster recovery** — communication when infrastructure fails →
  `chat.py`
- **Offline-first apps** — no internet needed → all of them
- **Air-gapped networks** — secure environments → `secure_chat.py`
- **Event venues** — conferences, festivals → `presence.py`
- **Vehicle-to-vehicle (V2X)** — cars, drones → `game_sync.py` +
  `wan_direct.py` (direct dial when infrastructure is missing)
