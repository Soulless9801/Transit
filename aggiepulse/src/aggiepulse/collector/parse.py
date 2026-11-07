"""Pure parsing of TripSpark's route → direction → vehicle payload."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from aggiepulse.common.paths import LOCAL_TZ

_LOCAL_ZONE = ZoneInfo(LOCAL_TZ)


class VehicleRow(TypedDict):
    vehicle_id: str | None
    vehicle_name: str | None
    ts_gps_utc: datetime | None
    route_key: str | None
    route_short_name: str | None
    direction_key: str | None
    direction_name: str | None
    lat: float | None
    lon: float | None
    speed: float | None
    heading: float | None
    onboard: int | None
    capacity: int | None
    load_factor: float | None
    is_extra_trip: bool | None


def _utc(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("ingest_ts must include a timezone")
    return parsed.astimezone(UTC)


def gps_time_utc(value: str | datetime, ingest_ts: str | datetime) -> datetime:
    """Resolve local GPS time using the latest possible instant <= ingestion.

    At fall-back, select the closest past fold. Reject a nonexistent spring
    time or an ambiguous time whose two folds are both after ingestion. Normal
    unambiguous timestamps retain their value even if the source clock is fast.
    """
    ingest = _utc(ingest_ts)
    local = datetime.fromisoformat(value) if isinstance(value, str) else value
    if local.tzinfo is not None:
        return local.astimezone(UTC)
    candidates = {
        local.replace(tzinfo=_LOCAL_ZONE, fold=fold).astimezone(UTC)
        for fold in (0, 1)
        if local.replace(tzinfo=_LOCAL_ZONE, fold=fold)
        .astimezone(UTC)
        .astimezone(_LOCAL_ZONE)
        .replace(tzinfo=None)
        == local
    }
    if not candidates:
        raise ValueError(f"Nonexistent {LOCAL_TZ} GPS time: {local.isoformat()}")
    if len(candidates) == 1:
        return candidates.pop()
    past = [candidate for candidate in candidates if candidate <= ingest]
    if not past:
        raise ValueError("Both ambiguous GPS folds are after ingest_ts")
    return max(past)


def iter_vehicles(
    payload: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Yield (route group, direction group, vehicle), skipping empty groups."""
    for route in payload:
        for direction in route.get("vehiclesByDirections") or []:
            for vehicle in direction.get("vehicles") or []:
                yield route, direction, vehicle


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def flatten_vehicles(
    payload: list[dict[str, Any]],
    ingest_ts: str | datetime,
    base_data: Mapping[str, Any] | None = None,
) -> list[VehicleRow]:
    """Flatten measured values; speed retains the feed's undocumented units.

    ``vehicle_id`` is the vendor GUID; ``vehicle_name`` is the fleet name
    (e.g. B2011). Route short names are enriched from optional base data.
    Missing values stay None; zero/nonpositive capacity has no load factor.
    """
    ingest = _utc(ingest_ts)
    routes = {route["key"]: route for route in (base_data or {}).get("routes", [])}
    rows: list[VehicleRow] = []
    for route, direction, vehicle in iter_vehicles(payload):
        location = vehicle.get("location") or {}
        route_key = vehicle.get("routeKey") or route.get("routeKey")
        metadata = routes.get(route_key, {})
        onboard = _integer(vehicle.get("passengersOnboard"))
        capacity = _integer(vehicle.get("passengerCapacity"))
        gps = location.get("lastGpsDate")
        rows.append(
            VehicleRow(
                vehicle_id=vehicle.get("key"),
                vehicle_name=vehicle.get("name"),
                ts_gps_utc=gps_time_utc(gps, ingest) if gps else None,
                route_key=route_key,
                route_short_name=vehicle.get("routeShortName")
                or route.get("routeShortName")
                or metadata.get("shortName"),
                direction_key=vehicle.get("directionKey")
                or direction.get("directionKey"),
                direction_name=vehicle.get("directionName")
                or direction.get("directionName"),
                lat=_number(location.get("latitude")),
                lon=_number(location.get("longitude")),
                speed=_number(location.get("speed")),
                heading=_number(location.get("heading")),
                onboard=onboard,
                capacity=capacity,
                load_factor=onboard / capacity
                if onboard is not None and capacity is not None and capacity > 0
                else None,
                is_extra_trip=vehicle.get("isExtraTrip")
                if isinstance(vehicle.get("isExtraTrip"), bool)
                else None,
            )
        )
    return rows
