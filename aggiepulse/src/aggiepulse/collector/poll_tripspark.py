"""Run with ``uv run python -m aggiepulse.collector.poll_tripspark``."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aggiepulse.collector.parse import iter_vehicles
from aggiepulse.collector.tripspark import TripSparkClient, TripSparkError
from aggiepulse.common.paths import data_root, landing_dir


def utc_now() -> datetime:
    return datetime.now(UTC)


def next_boundary(now: float, interval: float) -> float:
    """Next epoch-aligned slot; skip missed slots instead of accumulating drift."""
    return (math.floor(now / interval) + 1) * interval


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode()


def append_gzip_json(path: Path, value: Any) -> None:
    """Append one finished gzip member, then flush and fsync its bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _json_bytes(value)
    with path.open("ab") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            compressed.write(line)
        raw.flush()
        os.fsync(raw.fileno())


def write_heartbeat(path: Path, value: dict[str, Any]) -> None:
    """Publish a complete heartbeat atomically, including after failed polls."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _host() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", socket.gethostname())


def vehicles_path(when: datetime, hostname: str) -> Path:
    return (
        landing_dir("tripspark_vehicles", when)
        / f"vehicles_{when:%Y%m%dT%H}_{hostname}.jsonl.gz"
    )


def _base_path(when: datetime, hostname: str) -> Path:
    return landing_dir("tripspark_base", when) / (
        f"base_{when:%Y%m%dT%H%M%S%fZ}_{hostname}_{uuid.uuid4().hex}.json.gz"
    )


def _positive(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def run(
    *,
    interval: float = 30,
    base_interval: float = 3600,
    max_polls: int | None = None,
    stop: threading.Event | None = None,
    client: TripSparkClient | None = None,
) -> dict[str, Any]:
    """Collect until stopped or bounded; return the final heartbeat counters."""
    if (
        not math.isfinite(interval)
        or interval <= 0
        or not math.isfinite(base_interval)
        or base_interval <= 0
        or (max_polls is not None and max_polls <= 0)
    ):
        raise ValueError("Intervals and max_polls must be positive")
    stop = stop if stop is not None else threading.Event()
    client = client if client is not None else TripSparkClient(wait=stop.wait)
    hostname = _host()
    heartbeat_path = data_root() / "collector_heartbeat.json"
    state: dict[str, Any] = {
        "started_ts": utc_now().isoformat(),
        "hostname": hostname,
        "pid": os.getpid(),
        "polls": 0,
        "successes": 0,
        "failures": 0,
        "base_fetches": 0,
        "base_failures": 0,
        "last_success_ts": None,
    }
    route_keys: list[str] = []
    next_base = 0.0
    try:
        while not stop.is_set() and (max_polls is None or state["polls"] < max_polls):
            base_error: str | None = None
            if time.time() >= next_base:
                base: Any = None
                base_status = 0
                try:
                    base = client.get_base_data()
                    base_status = client.last_http_status
                    route_keys = list(
                        dict.fromkeys(route["key"] for route in base["routes"])
                    )
                    state["base_fetches"] += 1
                except TripSparkError as exc:
                    base_status, base_error = exc.http_status, str(exc)
                    state["base_failures"] += 1
                when = utc_now()
                append_gzip_json(
                    _base_path(when, hostname),
                    {
                        "ingest_ts": when.isoformat(),
                        "http_status": base_status,
                        "payload": base,
                        "error": base_error,
                    },
                )
                # Retry absent metadata at the next poll; retain known routes on failure.
                next_base = (
                    next_boundary(time.time(), base_interval)
                    if base_error is None
                    else time.time() + interval
                )
                if base_error:
                    print(
                        f"{when.isoformat()} base_error={base_error!r}",
                        file=sys.stderr,
                        flush=True,
                    )
            if stop.is_set():
                break
            start = time.monotonic()
            payload: Any = None
            error: str | None = None
            status = 0
            try:
                if not route_keys:
                    raise TripSparkError(base_error or "No route keys available")
                payload = client.get_vehicles(route_keys)
                status = client.last_http_status
            except TripSparkError as exc:
                status, error = exc.http_status, str(exc)
            latency = time.monotonic() - start
            when = utc_now()
            record = {
                "poll_id": str(uuid.uuid4()),
                "ingest_ts": when.isoformat(),
                "http_status": status,
                "route_keys": route_keys.copy(),
                "payload": payload,
                "error": error,
            }
            path = vehicles_path(when, hostname)
            append_gzip_json(path, record)
            state["polls"] += 1
            state["last_poll_ts"] = when.isoformat()
            state["last_http_status"] = status
            state["last_error"] = error
            state["last_landing_file"] = str(path)
            state["last_latency_seconds"] = round(latency, 3)
            if error is None:
                state["successes"] += 1
                state["last_success_ts"] = when.isoformat()
            else:
                state["failures"] += 1
            vehicles = [vehicle for _, _, vehicle in iter_vehicles(payload or [])]
            onboard = sum(
                vehicle.get("passengersOnboard") or 0
                for vehicle in vehicles
                if isinstance(vehicle.get("passengersOnboard"), (int, float))
            )
            state["last_vehicles"] = len(vehicles)
            state["last_total_onboard"] = onboard
            write_heartbeat(heartbeat_path, state)
            print(
                f"{when.isoformat()} poll={state['polls']} status={status} "
                f"vehicles={len(vehicles)} onboard={onboard} latency={latency:.3f}s"
                + (f" error={error!r}" if error else ""),
                file=sys.stderr,
                flush=True,
            )
            if max_polls is not None and state["polls"] >= max_polls:
                break
            # Recheck wall time after wakeup, including after clock adjustments.
            deadline = next_boundary(time.time(), interval)
            while not stop.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                stop.wait(min(remaining, 1.0))
    finally:
        client.close()
        state["stopped_ts"] = utc_now().isoformat()
        write_heartbeat(heartbeat_path, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=_positive, default=30)
    parser.add_argument("--base-interval", type=_positive, default=3600)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-polls", type=_positive_int)
    args = parser.parse_args(argv)
    stop = threading.Event()

    def stopping(signum: int, frame: Any) -> None:
        stop.set()

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        state = run(
            interval=args.interval,
            base_interval=args.base_interval,
            max_polls=1 if args.once else args.max_polls,
            stop=stop,
        )
        return 0 if state["failures"] == 0 else 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
