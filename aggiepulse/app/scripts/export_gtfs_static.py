"""Export TAMU GTFS as browser-friendly GeoJSON; no geospatial dependencies."""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def export(cache=ROOT / "app/.cache", output=ROOT / "app/frontend/public/static"):
    landing = list((ROOT / "data/landing/gtfs").rglob("*.zip"))
    cached = list(Path(cache).glob("*.zip"))
    candidates = landing or cached
    if candidates:
        archive = max(candidates, key=lambda p: p.stat().st_mtime)
    else:
        Path(cache).mkdir(parents=True, exist_ok=True)
        archive = Path(cache) / "google_transit.zip"
        with urlopen(
            "https://transport.tamu.edu/webFS/gtfs/google_transit.zip", timeout=60
        ) as response:
            archive.write_bytes(response.read())
    with ZipFile(archive) as z:

        def read(name):
            member = next(n for n in z.namelist() if n.endswith(name + ".txt"))
            return pd.read_csv(z.open(member), dtype=str).fillna("")

        routes, trips, stops, times, shapes = [
            read(n) for n in ("routes", "trips", "stops", "stop_times", "shapes")
        ]
    route_rows = routes.to_dict("records")
    metadata = {r["route_id"]: r for r in route_rows}
    geometry = {}
    shapes["sequence"] = pd.to_numeric(shapes.shape_pt_sequence)
    for sid, group in shapes.sort_values("sequence").groupby("shape_id"):
        geometry[sid] = [
            [float(r.shape_pt_lon), float(r.shape_pt_lat)] for r in group.itertuples()
        ]
    features = []
    for (rid, direction), group in trips.groupby(["route_id", "direction_id"]):
        ids = [
            s for s in group.shape_id.unique() if s in geometry and len(geometry[s]) > 1
        ]
        if not ids:
            continue
        props = {
            k: metadata[rid].get(k, "")
            for k in (
                "route_id",
                "route_short_name",
                "route_long_name",
                "route_color",
                "route_text_color",
            )
        }
        props.update(direction_id=direction, shape_id=ids[0] if len(ids) == 1 else ids)
        coords = [geometry[s] for s in ids]
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {
                    "type": "LineString" if len(ids) == 1 else "MultiLineString",
                    "coordinates": coords[0] if len(ids) == 1 else coords,
                },
            }
        )
    served = (
        times[["trip_id", "stop_id"]]
        .merge(trips[["trip_id", "route_id"]], on="trip_id")
        .groupby("stop_id")
        .route_id.agg(lambda x: sorted(set(x)))
        .to_dict()
    )
    stop_features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(s["stop_lon"]), float(s["stop_lat"])],
            },
            "properties": {
                **{k: s.get(k, "") for k in ("stop_id", "stop_code", "stop_name")},
                "routes": served.get(s["stop_id"], []),
            },
        }
        for s in stops.to_dict("records")
    ]
    planner = []

    def seconds(value):
        h, m, s = map(int, value.split(":"))
        return h * 3600 + m * 60 + s

    times["seconds"] = times.arrival_time.map(seconds)
    times["seq"] = times.stop_sequence.astype(int)
    joined = times.merge(trips[["trip_id", "route_id", "direction_id"]], on="trip_id")
    for (rid, direction), group in joined.groupby(["route_id", "direction_id"]):
        departures = group.sort_values("seq").groupby("trip_id").first().seconds
        headways = {}
        for hour, stamps in departures.groupby(departures // 3600):
            unique = sorted(set(stamps))
            headways[str(hour % 24)] = (
                float(pd.Series(unique).diff().dropna().median() / 60)
                if len(unique) > 1
                else 60
            )
        for _, pattern in group.groupby("trip_id"):
            pattern = pattern.sort_values("seq")
            ids = pattern.stop_id.tolist()
            if any(
                p["route"] == metadata[rid]["route_short_name"]
                and p["direction"] == str(direction)
                and p["stops"] == ids
                for p in planner
            ):
                continue
            planner.append(
                {
                    "route": metadata[rid]["route_short_name"],
                    "direction": str(direction),
                    "stops": ids,
                    "times": (pattern.seconds - pattern.seconds.iloc[0]).tolist(),
                    "headways": headways,
                    "headway": float(pd.Series(list(headways.values())).median()),
                }
            )
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "planner.json").write_text(json.dumps(planner))
    Path(output).mkdir(parents=True, exist_ok=True)
    for name, data in [
        ("routes.geojson", {"type": "FeatureCollection", "features": features}),
        ("stops.geojson", {"type": "FeatureCollection", "features": stop_features}),
        ("routes.json", route_rows),
    ]:
        (Path(output) / name).write_text(json.dumps(data))
    print(
        f"Exported {len(features)} route-directions, {len(stop_features)} stops, {len(route_rows)} routes from {archive}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "app/.cache")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "app/frontend/public/static"
    )
    args = parser.parse_args()
    export(args.cache, args.output)
