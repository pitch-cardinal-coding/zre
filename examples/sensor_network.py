#!/usr/bin/env python3
"""
IoT Sensor Network — Distributed sensor data aggregation

Sensors publish readings to groups; aggregators collect and process data.

Usage:
  python sensor_network.py sensors [--port PORT]
  python sensor_network.py aggregator [--port PORT]
"""

import argparse
import asyncio
import contextlib
import json
import random
import time
from dataclasses import asdict, dataclass

from zre import ZreNode


@dataclass
class SensorReading:
    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    timestamp: float
    location: str


class SensorNode:
    """Simulates an IoT sensor publishing readings."""

    def __init__(
        self, sensor_id: str, sensor_type: str, location: str, interval: float = 5.0
    ):
        self.sensor_id = sensor_id
        self.sensor_type = sensor_type
        self.location = location
        self.interval = interval
        self.node = ZreNode(f"sensor-{sensor_id}")
        self.node.set_header("X-SENSOR-TYPE", sensor_type)
        self.node.set_header("X-LOCATION", location)

    async def start(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        await self.node.join(b"SENSORS")
        await self.node.join(b"ALL")

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        await self.start(port, interface, verbose)
        print(
            f"[{self.sensor_id}] Started - Type: {self.sensor_type}, Location: {self.location}"
        )
        # Run beacon loop in background
        run_task = asyncio.create_task(self.node.run())
        try:
            while True:
                value = self._generate_reading()
                reading = SensorReading(
                    sensor_id=self.sensor_id,
                    sensor_type=self.sensor_type,
                    value=value,
                    unit=self._get_unit(),
                    timestamp=time.time(),
                    location=self.location,
                )
                payload = json.dumps(asdict(reading)).encode()
                await self.node.shout(b"SENSORS", payload)
                await asyncio.sleep(self.interval)
        finally:
            run_task.cancel()
            with asyncio.CancelledError, contextlib.suppress(asyncio.CancelledError):
                await run_task
            await self.node.stop()

    def _generate_reading(self) -> float:
        if self.sensor_type == "temperature":
            return round(20 + random.uniform(-5, 10), 1)
        if self.sensor_type == "humidity":
            return round(40 + random.uniform(-10, 20), 1)
        if self.sensor_type == "pressure":
            return round(1013 + random.uniform(-20, 20), 1)
        if self.sensor_type == "motion":
            return 1 if random.random() < 0.1 else 0
        if self.sensor_type == "light":
            return round(random.uniform(0, 1000), 1)
        return round(random.uniform(0, 100), 1)

    def _get_unit(self) -> str:
        units = {
            "temperature": "celsius",
            "humidity": "percent",
            "pressure": "hPa",
            "motion": "boolean",
            "light": "lux",
        }
        return units.get(self.sensor_type, "units")


class AggregatorNode:
    """Collects and processes sensor data."""

    def __init__(self, name: str = "aggregator"):
        self.name = name
        self.node = ZreNode(name)
        self.node.set_header("X-ROLE", "aggregator")
        self.readings: dict[str, dict] = {}

    async def run(
        self,
        port: int | None = None,
        interface: str | None = None,
        verbose: bool = False,
    ):
        if port:
            self.node.set_port(port)
        if interface:
            self.node.set_interface(interface)
        if verbose:
            self.node.set_verbose(True)
        await self.node.start()
        await self.node.join(b"SENSORS")
        await self.node.join(b"ALL")
        print(f"[{self.name}] Aggregator started")

        async def printer():
            async for event in self.node.events():
                if event["type"] == "SHOUT" and event.get("group") in (
                    "SENSORS",
                    "ALL",
                ):
                    try:
                        payload = event.get("payload", b"")
                        data = json.loads(payload.decode())
                        sensor_id = data.get("sensor_id")
                        self.readings[sensor_id] = data
                        print(
                            f"[{self.name}] Reading from {sensor_id}: {data['value']} {data['unit']}"
                        )
                    except Exception as exc:
                        print(f"[{self.name}] Error processing reading: {exc}")

        try:
            await asyncio.gather(self.node.run(), printer())
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.node.stop()


async def run_sensors(port: int, interface: str | None, verbose: bool):
    sensors = [
        SensorNode("temp-001", "temperature", "room-101", 3.0),
        SensorNode("temp-002", "temperature", "room-102", 3.0),
        SensorNode("humid-001", "humidity", "room-101", 5.0),
        SensorNode("pressure-001", "pressure", "outside", 10.0),
        SensorNode("motion-001", "motion", "hallway", 2.0),
        SensorNode("light-001", "light", "office", 4.0),
    ]
    await asyncio.gather(*(s.run(port, interface, verbose) for s in sensors))


async def run_aggregator(port: int, interface: str | None, verbose: bool):
    agg = AggregatorNode("main-aggregator")
    await agg.run(port, interface, verbose)


def main():
    p = argparse.ArgumentParser(description="ZRE sensor network")
    p.add_argument(
        "mode", choices=["sensors", "aggregator"], help="run sensors or aggregator"
    )
    p.add_argument("--port", type=int, default=5670, help="beacon port")
    p.add_argument("--interface", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    try:
        if args.mode == "sensors":
            asyncio.run(run_sensors(args.port, args.interface, args.verbose))
        else:
            asyncio.run(run_aggregator(args.port, args.interface, args.verbose))
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted, shutting down...")
