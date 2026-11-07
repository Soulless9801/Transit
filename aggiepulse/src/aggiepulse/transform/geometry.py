"""Metre-based shape projection with continuity at crossings and loop seams."""

from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_008.8


class Shape:
    """Polyline in one local equirectangular frame (adequate at campus scale)."""

    def __init__(self, points: pd.DataFrame):
        points = points.assign(
            shape_pt_sequence=pd.to_numeric(points.shape_pt_sequence)
        ).sort_values("shape_pt_sequence")
        self.lat0 = float(pd.to_numeric(points.shape_pt_lat).mean())
        self.lon0 = float(pd.to_numeric(points.shape_pt_lon).mean())
        xy = self.xy(points.shape_pt_lat, points.shape_pt_lon)
        lengths = np.linalg.norm(np.diff(xy, axis=0), axis=1)
        # Duplicate shape vertices have no projection interval.
        xy = xy[np.r_[True, lengths > 1e-6]]
        if len(xy) < 2:
            raise ValueError("A shape needs two distinct points")
        self.a, self.delta = xy[:-1], np.diff(xy, axis=0)
        self.lengths = np.linalg.norm(self.delta, axis=1)
        self.cumulative = np.r_[0.0, self.lengths.cumsum()]
        self.length = float(self.cumulative[-1])
        self.loop = np.linalg.norm(xy[-1] - xy[0]) < 80 and self.length > 200
        self.source_distance = None
        if "shape_dist_traveled" in points:
            source = pd.to_numeric(
                points.shape_dist_traveled, errors="coerce"
            ).to_numpy()
            source = source[np.r_[True, lengths > 1e-6]]
            if np.isfinite(source).all() and np.all(np.diff(source) > 0):
                self.source_distance = source

    def xy(self, lat, lon):
        return np.column_stack(
            [
                np.deg2rad(np.asarray(lon, dtype=float) - self.lon0)
                * EARTH_RADIUS_M
                * np.cos(np.deg2rad(self.lat0)),
                np.deg2rad(np.asarray(lat, dtype=float) - self.lat0) * EARTH_RADIUS_M,
            ]
        )

    def candidates(self, lat: float, lon: float):
        point = self.xy([lat], [lon])[0]
        fraction = np.clip(
            ((point - self.a) * self.delta).sum(axis=1) / self.lengths**2, 0, 1
        )
        offset = np.linalg.norm(
            point - (self.a + fraction[:, None] * self.delta), axis=1
        )
        distance = self.cumulative[:-1] + fraction * self.lengths
        return distance, offset

    def project(
        self,
        pings: pd.DataFrame,
        *,
        monotonic=True,
        backward_tolerance_m=40.0,
        max_offset_m=100.0,
        max_gap_s=180.0,
        max_speed_mps=35.0,
    ):
        """Return along-shape position, perpendicular offset and unwrapped progress.

        Reject reversals, implausible travel and off-route points. A long outage
        starts a new segment; no visit may be interpolated across that boundary.
        The nearest geometry remains available as offset even on rejection.
        """
        output = pings.copy()
        output["ts_gps"] = pd.to_datetime(output.ts_gps, utc=True)
        output = output.sort_values("ts_gps").reset_index(drop=True)
        previous = previous_time = None
        segment = 0
        records = []
        for row in output.itertuples():
            elapsed = (
                (row.ts_gps - previous_time).total_seconds()
                if previous_time is not None
                else 0
            )
            if elapsed > max_gap_s:
                previous = previous_time = None
                segment += 1
            distance, offset = self.candidates(row.lat, row.lon)
            nearest = int(np.argmin(offset))
            best_offset = float(offset[nearest])
            progress = distance.copy()
            if monotonic and previous is not None:
                if self.loop:
                    # Test the adjacent laps, including the seam at distance 0.
                    lap = np.floor(previous / self.length)
                    progress = np.concatenate(
                        [distance + (lap + shift) * self.length for shift in (-1, 0, 1)]
                    )
                    offset = np.tile(offset, 3)
                    distance = np.tile(distance, 3)
                delta = progress - previous
                eligible = (
                    (delta >= -backward_tolerance_m)
                    & (delta <= max_speed_mps * max(elapsed, 1) + 40)
                    & (offset <= min(best_offset + 15, max_offset_m))
                )
                score = offset + np.abs(delta) * 0.015
                score[~eligible] = np.inf
                chosen = int(np.argmin(score))
                valid = np.isfinite(score[chosen]) and best_offset <= max_offset_m
            else:
                chosen = nearest
                valid = best_offset <= max_offset_m
            if valid:
                current = float(progress[chosen])
                # Clamp small backwards jitter without inventing a crossing.
                if monotonic and previous is not None:
                    current = max(previous, current)
                previous, previous_time = current, row.ts_gps
                records.append(
                    (float(distance[chosen]), float(offset[chosen]), current, segment)
                )
            else:
                # A rejected ping breaks crossing interpolation, while the last
                # accepted progress still constrains subsequent projections.
                segment += 1
                records.append((np.nan, best_offset, np.nan, segment))
        output[["shape_dist_m", "offset_m", "progress_m", "segment_id"]] = records
        return output


def build_shapes(shapes: pd.DataFrame) -> dict[str, Shape]:
    return {str(key): Shape(group) for key, group in shapes.groupby("shape_id")}
