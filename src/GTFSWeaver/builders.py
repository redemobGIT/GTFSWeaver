"""GTFS table builders and feed orchestrator.

GTFS compliance notes
---------------------
- agency.txt: agency_name, agency_url, agency_timezone
- routes.txt: route_id, route_short_name, route_long_name, route_type
- trips.txt: route_id, service_id, trip_id, direction_id, shape_id
- stop_times.txt: trip_id, arrival_time, departure_time, stop_id,
  stop_sequence, shape_dist_traveled
- calendar.txt: service_id, monday-sunday, start_date, end_date
- calendar_dates.txt: service_id, date, exception_type
- shapes.txt: shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence
- frequencies.txt: trip_id, start_time, end_time, headway_secs
"""

from __future__ import annotations

from typing import Iterable

import geopandas as gpd
import gtfs_kit as gk
import numpy as np
import pandas as pd

from . import constants as cs
from .geometry import (
    cluster_stops_h3,
    compute_shape_point_speeds,
    make_stop_points,
    project_stops_to_shape,
)
from .models import (
    ProtoFeed,
    holiday_action_from_pattern,
    make_route_id,
    make_trip_id,
)
from .time_utils import duration_seconds, format_gtfs_time, parse_gtfs_time

KMH_TO_MS = 1000 / 3600
DEFAULT_MAX_SEGMENT_SPEED_KMH = 80.0


def _time_duration(start: str, end: str, units: str = "s") -> float:
    """Return the duration between two GTFS times."""
    divisors = {"s": 1, "min": 60, "h": 3600}
    if units not in divisors:
        raise ValueError(f"units must be one of {list(divisors)}")

    seconds = duration_seconds(start, end, allow_wrap=True)
    return seconds / divisors[units]


def _get_headway_secs(row: pd.Series) -> float:
    """Return headway in seconds.

    Prefer ``headway_mins`` when available, since it is exact.
    """
    if "headway_mins" in row.index and pd.notna(row.get("headway_mins")):
        return float(row["headway_mins"]) * 60
    return 3600 / float(row["frequency"])


def _get_num_trips(row: pd.Series) -> int:
    """Return the number of trips represented by a headway row."""
    if "headway_mins" in row.index and pd.notna(row.get("headway_mins")):
        duration = _time_duration(row["start_time"], row["end_time"], "min")
        return max(1, int(duration / float(row["headway_mins"])))

    duration = _time_duration(row["start_time"], row["end_time"], "h")
    return max(1, int(float(row["frequency"]) * duration))


def _empty_stop_times() -> pd.DataFrame:
    """Return an empty stop_times table with GTFS columns."""
    return pd.DataFrame(
        columns=[
            "trip_id",
            "stop_id",
            "stop_sequence",
            "arrival_time",
            "departure_time",
            "shape_dist_traveled",
            "timepoint",
        ]
    )


def _iter_shape_rows(
    shape_id: str,
    coords: Iterable[tuple[float, float]],
) -> list[list[object]]:
    """Return shape rows for ``shapes.txt``."""
    return [[shape_id, seq, coord[0], coord[1]] for seq, coord in enumerate(coords)]


def _allocate_integer_seconds(
    total_secs: int,
    segment_distances: np.ndarray,
    min_segment_secs: np.ndarray,
) -> np.ndarray:
    """Distribute integer seconds across segments, preserving totals."""
    if len(segment_distances) == 0:
        return np.array([], dtype=int)

    total_secs = max(int(total_secs), int(min_segment_secs.sum()))
    slack = total_secs - int(min_segment_secs.sum())

    if slack == 0:
        return min_segment_secs.astype(int)

    weights = segment_distances.astype(float)
    if np.all(weights <= 0):
        weights = np.ones(len(segment_distances), dtype=float)

    raw_extra = slack * (weights / weights.sum())
    extra = np.floor(raw_extra).astype(int)

    remainder = slack - int(extra.sum())
    if remainder > 0:
        frac = raw_extra - extra
        order = np.argsort(-frac, kind="stable")
        extra[order[:remainder]] += 1

    return min_segment_secs.astype(int) + extra


def _filter_used_stops(
    stops: pd.DataFrame,
    stop_times: pd.DataFrame,
) -> pd.DataFrame:
    """Keep only stops referenced by stop_times."""
    if stops is None or stops.empty:
        return stops

    if stop_times is None or stop_times.empty:
        return stops.iloc[0:0].copy()

    used_stop_ids = stop_times["stop_id"].dropna().unique()
    return (
        stops.loc[stops["stop_id"].isin(used_stop_ids)]
        .drop_duplicates("stop_id")
        .copy()
    )


def _mark_timepoints(df: pd.DataFrame) -> pd.DataFrame:
    """Add GTFS ``timepoint`` flags to a stop-time template."""
    out = df.copy()
    out["timepoint"] = 0
    if len(out) >= 1:
        out.loc[out.index[0], "timepoint"] = 1
        out.loc[out.index[-1], "timepoint"] = 1
    return out


def build_agency(pfeed: ProtoFeed) -> pd.DataFrame:
    """Build ``agency.txt``."""
    row = pfeed.meta.iloc[0]
    return pd.DataFrame(
        {
            "agency_name": [row["agency_name"]],
            "agency_url": [row["agency_url"]],
            "agency_timezone": [row["agency_timezone"]],
        }
    )


def build_calendar(
    pfeed: ProtoFeed,
) -> tuple[pd.DataFrame, pd.DataFrame | None, dict[str, str]]:
    """Build ``calendar.txt`` and ``calendar_dates.txt``."""
    weekdays = list(cs.WEEKDAYS)
    profiles = pfeed.service_profiles.copy()

    profiles["weekday_bits"] = profiles.apply(
        lambda row: tuple(int(row[day]) for day in weekdays),
        axis=1,
    )
    profiles["holiday_action"] = profiles["service_pattern"].map(
        holiday_action_from_pattern
    )

    def make_service_id(bits: tuple[int, ...], action: str) -> str:
        bit_str = "".join(str(bit) for bit in bits)
        return f"srv{bit_str}_{action}"

    profile_to_service: dict[str, str] = {}
    service_defs: set[tuple[tuple[int, ...], str]] = set()

    for _, row in profiles.iterrows():
        bits = row["weekday_bits"]
        action = row["holiday_action"]
        service_id = make_service_id(bits, action)
        profile_to_service[row["service_profile_id"]] = service_id
        service_defs.add((bits, action))

    start_date = pfeed.meta["start_date"].iat[0]
    end_date = pfeed.meta["end_date"].iat[0]

    calendar_rows = [
        [make_service_id(bits, action), *bits, start_date, end_date]
        for bits, action in sorted(service_defs)
    ]
    calendar = pd.DataFrame(
        calendar_rows,
        columns=["service_id", *weekdays, "start_date", "end_date"],
    )

    calendar_dates = None
    if pfeed.has_holidays:
        rows: list[dict[str, object]] = []
        for bits, action in service_defs:
            if action == "none":
                continue

            exception_type = 1 if action == "add" else 2
            service_id = make_service_id(bits, action)

            for date in pfeed.holidays["date"].tolist():
                rows.append(
                    {
                        "service_id": service_id,
                        "date": date,
                        "exception_type": exception_type,
                    }
                )

        if rows:
            calendar_dates = pd.DataFrame(rows)

    return calendar, calendar_dates, profile_to_service


def build_routes(pfeed: ProtoFeed) -> pd.DataFrame:
    """Build ``routes.txt``."""
    routes = (
        pfeed.resolved_frequencies.filter(
            ["route_short_name", "route_long_name", "route_type"]
        )
        .drop_duplicates()
        .assign(route_id=lambda df: df["route_short_name"].map(make_route_id))
    )
    return routes[["route_id", "route_short_name", "route_long_name", "route_type"]]


def build_shapes(pfeed: ProtoFeed) -> pd.DataFrame:
    """Build shapes.txt from final ProtoFeed shape geometries."""
    rows: list[list[object]] = []

    for shape_id, geometry in pfeed.shapes[
        ["shape_id", "geometry"]
    ].itertuples(index=False):
        rows.extend(_iter_shape_rows(shape_id, list(geometry.coords)))

    return pd.DataFrame(
        rows,
        columns=[
            "shape_id",
            "shape_pt_sequence",
            "shape_pt_lon",
            "shape_pt_lat",
        ],
    )


def build_stops(
    pfeed: ProtoFeed,
    shapes: pd.DataFrame | None = None,
    offset: float = cs.STOP_OFFSET,
    num_stops: int = 2,
    spacing: float | None = None,
    cluster_h3: bool = False,
    h3_resolution: int = cs.DEFAULT_H3_RESOLUTION,
) -> pd.DataFrame:
    """Build stops.txt."""
    if pfeed.stops is not None:
        return pfeed.stops.copy()

    if shapes is None:
        raise ValueError("Must provide shapes when pfeed.stops is None")

    shapes_gdf = gk.geometrize_shapes(shapes, use_utm=True)

    stops = (
        make_stop_points(
            shapes_gdf,
            id_col="shape_id",
            offset=offset,
            side=pfeed.traffic_side,
            num_stops=num_stops,
            spacing=spacing,
        )
        .to_crs(cs.WGS84)
        .rename(columns={"point_id": "stop_id"})
        .assign(
            stop_name=lambda df: "stop " + df["stop_id"],
            stop_lon=lambda df: df.geometry.x,
            stop_lat=lambda df: df.geometry.y,
        )
    )

    if cluster_h3:
        stops = cluster_stops_h3(
            stops.set_crs(cs.WGS84),
            resolution=h3_resolution,
        )

    return stops.filter(["stop_id", "stop_name", "stop_lon", "stop_lat"])


def build_trips(
    pfeed: ProtoFeed,
    routes: pd.DataFrame,
    profile_to_service: dict[str, str],
) -> pd.DataFrame:
    """Build trips.txt."""
    resolved = pfeed.resolved_frequencies.copy()

    if "route_id" not in resolved.columns:
        resolved["route_id"] = resolved["route_short_name"].map(make_route_id)

    valid_route_ids = set(routes["route_id"])
    route_freq = resolved.loc[resolved["route_id"].isin(valid_route_ids)]

    rows: list[dict[str, object]] = []

    for _, row in route_freq.iterrows():
        service_id = profile_to_service.get(row["service_profile_id"])
        if service_id is None:
            continue

        direction = int(row["direction"])
        schedule_type = row.get("schedule_type", cs.SCHEDULE_HEADWAY)

        num_trips = 1
        if schedule_type != cs.SCHEDULE_FIXED:
            num_trips = _get_num_trips(row)

        for sequence in range(num_trips):
            trip_id = make_trip_id(
                row["route_id"],
                row["service_profile_id"],
                direction,
                sequence,
            )

            rows.append(
                {
                    "route_id": row["route_id"],
                    "trip_id": trip_id,
                    "direction_id": direction,
                    "shape_id": row["shape_id"],
                    "service_id": service_id,
                    "trip_start_time": row["start_time"],
                    "sequence": sequence,
                    "service_profile_id": row["service_profile_id"],
                }
            )

    return pd.DataFrame(rows)


def _build_proportional_template(
    projected: gpd.GeoDataFrame,
    total_time_secs: float,
    *,
    max_segment_speed_kmh: float = DEFAULT_MAX_SEGMENT_SPEED_KMH,
) -> pd.DataFrame:
    """Build a proportional stop-time template for one trip."""
    if projected.empty:
        return _empty_stop_times()

    ordered = projected.sort_values(
        "shape_dist_traveled",
        ignore_index=True,
    ).copy()

    if len(ordered) == 1:
        ordered["arrival_time"] = 0.0
        ordered["departure_time"] = 0.0
        ordered["stop_sequence"] = 0
        ordered = _mark_timepoints(ordered)
        return ordered.filter(
            [
                "stop_id",
                "stop_sequence",
                "arrival_time",
                "departure_time",
                "shape_dist_traveled",
                "timepoint",
            ]
        )

    positions = ordered["shape_dist_traveled"].to_numpy(dtype=float)
    segment_distances = np.diff(positions)
    segment_distances = np.maximum(segment_distances, 0.0)

    max_speed_ms = max_segment_speed_kmh * KMH_TO_MS
    min_segment_secs = np.ceil(segment_distances / max_speed_ms).astype(int)
    min_segment_secs = np.maximum(min_segment_secs, 1)

    segment_secs = _allocate_integer_seconds(
        total_secs=int(round(total_time_secs)),
        segment_distances=segment_distances,
        min_segment_secs=min_segment_secs,
    )

    arrivals = np.concatenate(([0], np.cumsum(segment_secs))).astype(float)

    ordered["arrival_time"] = arrivals
    ordered["departure_time"] = ordered["arrival_time"]
    ordered["stop_sequence"] = range(len(ordered))
    ordered = _mark_timepoints(ordered)

    return ordered.filter(
        [
            "stop_id",
            "stop_sequence",
            "arrival_time",
            "departure_time",
            "shape_dist_traveled",
            "timepoint",
        ]
    )


def _build_zone_template(
    stop_candidates: gpd.GeoDataFrame,
    shape_id: str,
    line,
    zones_utm: gpd.GeoDataFrame,
    route_type: int,
    shape_point_speeds: gpd.GeoDataFrame,
    default_speed: float,
) -> pd.DataFrame:
    """Build a stop-time template using speed zones."""
    shape_speeds = (
        shape_point_speeds.loc[lambda df: df["shape_id"] == shape_id]
        .assign(speed=lambda df: df["speed"] * KMH_TO_MS)
        .filter(["shape_id", "shape_dist_traveled", "speed_zone_id", "speed"])
    )

    zones = zones_utm.loc[lambda df: df["route_type"] == route_type].assign(
        speed=lambda df: df["speed"] * KMH_TO_MS
    )

    default_speed_ms = default_speed * KMH_TO_MS

    stops_with_pos = (
        stop_candidates.assign(
            shape_dist_traveled=lambda df: df.geometry.apply(line.project)
        )
        .sjoin(zones)
        .sort_values("shape_dist_traveled", ignore_index=True)
        .filter(["stop_id", "shape_dist_traveled", "speed_zone_id", "speed"])
    )

    merged = (
        pd.concat([stops_with_pos, shape_speeds], ignore_index=True)
        .sort_values("shape_dist_traveled", ignore_index=True)
        .assign(
            speed=lambda df: df["speed"].replace({np.inf: default_speed_ms}),
            dist_to_next=lambda df: (
                df["shape_dist_traveled"].diff().shift(-1).fillna(0)
            ),
            weight_to_next=lambda df: df["dist_to_next"] * df["speed"],
            speed_weight_total=lambda df: (
                df["weight_to_next"].cumsum().shift(1).fillna(0)
            ),
        )
        .loc[lambda df: df["stop_id"].notna()]
        .assign(
            dist_to_next=lambda df: (
                df["shape_dist_traveled"].diff().shift(-1).fillna(0)
            ),
            weight_to_next=lambda df: (
                df["speed_weight_total"].diff().shift(-1).fillna(0)
            ),
            speed_to_next=lambda df: (df["weight_to_next"] / df["dist_to_next"]).fillna(
                0
            ),
            duration=lambda df: (df["dist_to_next"] / df["speed_to_next"]).fillna(0),
            arrival_time=lambda df: (df["duration"].shift(1).cumsum().fillna(0)),
        )
    )

    merged["departure_time"] = merged["arrival_time"]
    merged["stop_sequence"] = range(len(merged))
    merged = _mark_timepoints(merged)

    return merged.filter(
        [
            "stop_id",
            "stop_sequence",
            "arrival_time",
            "departure_time",
            "shape_dist_traveled",
            "timepoint",
        ]
    )


def build_stop_times(
    pfeed: ProtoFeed,
    shapes: pd.DataFrame,
    stops: pd.DataFrame,
    trips: pd.DataFrame,
    buffer: float = cs.BUFFER,
    speed_mode: str = "proportional",
    projected_stop_tolerance: float = 5.0,
    max_segment_speed_kmh: float = DEFAULT_MAX_SEGMENT_SPEED_KMH,
) -> pd.DataFrame:
    """Build ``stop_times.txt``."""
    frequencies = pfeed.resolved_frequencies.copy()

    if "route_id" not in frequencies.columns:
        frequencies["route_id"] = frequencies["route_short_name"].map(make_route_id)

    # 101 Principle: No parsing! trips already has direction_id, sequence, etc.
    trips_for_merge = trips.rename(columns={"direction_id": "direction"})

    trip_expanded = trips_for_merge.merge(
        frequencies.drop(columns=["shape_id"], errors="ignore"),
        on=["route_id", "service_profile_id", "direction"],
        how="left",
        suffixes=("_trip", ""),
    )

    if "shape_id_trip" in trip_expanded.columns:
        trip_expanded["shape_id"] = trip_expanded["shape_id_trip"]
        trip_expanded = trip_expanded.drop(columns=["shape_id_trip"])

    if trip_expanded.empty:
        return _empty_stop_times()

    shapes_utm = gk.geometrize_shapes(shapes, use_utm=True).set_index("shape_id")
    stops_utm = gk.geometrize_stops(stops, use_utm=True)

    if speed_mode == "proportional":
        if "travel_time_mins" not in trip_expanded.columns:
            raise ValueError(
                "speed_mode='proportional' requires "
                "'travel_time_mins' in resolved frequencies."
            )

    projected_by_shape: dict[str, gpd.GeoDataFrame] = {}
    for shape_id in trip_expanded["shape_id"].dropna().unique():
        if shape_id not in shapes_utm.index:
            continue

        line = shapes_utm.loc[shape_id].geometry
        side = (
            "both"
            if pfeed.stops is not None and speed_mode == "proportional"
            else pfeed.traffic_side
        )

        projected_by_shape[shape_id] = project_stops_to_shape(
            stops_utm,
            line,
            buffer=buffer,
            side=side,
            distance_tolerance=projected_stop_tolerance,
        )

    frames: list[pd.DataFrame] = []

    if speed_mode == "proportional":
        for _, row in trip_expanded.iterrows():
            shape_id = row["shape_id"]
            if shape_id not in shapes_utm.index:
                continue

            if pd.isna(row.get("travel_time_mins")):
                continue

            projected = projected_by_shape.get(shape_id)
            if projected is None or projected.empty:
                continue

            # 101 Principle: Access the data directly from the column!
            base_time = parse_gtfs_time(row["trip_start_time"])
            schedule_type = row.get("schedule_type", cs.SCHEDULE_HEADWAY)

            offset = base_time
            if schedule_type != cs.SCHEDULE_FIXED:
                offset += _get_headway_secs(row) * row["sequence"]

            template = _build_proportional_template(
                projected=projected,
                total_time_secs=float(row["travel_time_mins"]) * 60,
                max_segment_speed_kmh=max_segment_speed_kmh,
            )
            if template.empty:
                continue

            result = template.copy()
            result["trip_id"] = row["trip_id"]
            result["arrival_time"] = result["arrival_time"] + offset
            result["departure_time"] = result["departure_time"] + offset
            frames.append(result)

    elif speed_mode == "zones":
        zones_utm = pfeed.resolved_speed_zones.to_crs(pfeed.utm_crs)
        speed_cache: dict[int, gpd.GeoDataFrame] = {}

        group_cols = ["route_type", "shape_id", "speed"]
        for (route_type, shape_id, speed), group in trip_expanded.groupby(group_cols):
            if shape_id not in shapes_utm.index:
                continue

            projected = projected_by_shape.get(shape_id)
            if projected is None or projected.empty:
                continue

            if route_type not in speed_cache:
                speed_cache[route_type] = compute_shape_point_speeds(
                    shapes,
                    pfeed.resolved_speed_zones,
                    route_type,
                    use_utm=True,
                )

            line = shapes_utm.loc[shape_id].geometry
            template = _build_zone_template(
                stop_candidates=projected,
                shape_id=shape_id,
                line=line,
                zones_utm=zones_utm,
                route_type=route_type,
                shape_point_speeds=speed_cache[route_type],
                default_speed=speed,
            )
            if template.empty:
                continue

            for _, row in group.iterrows():
                # 101 Principle: Access the data directly from the column!
                base_time = parse_gtfs_time(row["trip_start_time"])
                schedule_type = row.get(
                    "schedule_type",
                    cs.SCHEDULE_HEADWAY,
                )

                offset = base_time
                if schedule_type != cs.SCHEDULE_FIXED:
                    offset += _get_headway_secs(row) * row["sequence"]

                result = template.copy()
                result["trip_id"] = row["trip_id"]
                result["arrival_time"] = result["arrival_time"] + offset
                result["departure_time"] = result["departure_time"] + offset
                frames.append(result)

    else:
        raise ValueError("speed_mode must be either 'proportional' or 'zones'")

    if not frames:
        return _empty_stop_times()

    stop_times = pd.concat(frames, ignore_index=True)
    stop_times["shape_dist_traveled"] = (
        stop_times["shape_dist_traveled"].astype(float).round(3)
    )

    for col in ("arrival_time", "departure_time"):
        stop_times[col] = stop_times[col].apply(format_gtfs_time)

    return stop_times[
        [
            "trip_id",
            "stop_id",
            "stop_sequence",
            "arrival_time",
            "departure_time",
            "shape_dist_traveled",
            "timepoint",
        ]
    ]


def build_frequencies(
    pfeed: ProtoFeed,
    trips: pd.DataFrame,
) -> pd.DataFrame | None:
    """Build ``frequencies.txt`` for headway-based rows only."""
    frequencies = pfeed.resolved_frequencies.copy()

    if "route_id" not in frequencies.columns:
        frequencies["route_id"] = frequencies["route_short_name"].map(make_route_id)

    service_profiles = pfeed.service_profiles[
        ["service_profile_id", "start_time", "end_time"]
    ]

    freq_expanded = frequencies.merge(
        service_profiles,
        on="service_profile_id",
        suffixes=("", "_prf"),
    )
    freq_expanded = freq_expanded.loc[
        freq_expanded["schedule_type"] == cs.SCHEDULE_HEADWAY
    ]
    if freq_expanded.empty:
        return None

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()

    for _, trip in trips.iterrows():
        # 101 Principle: Read straight from the columns
        group_key = (trip["route_id"], trip["service_profile_id"], trip["direction_id"])
        if group_key in seen:
            continue

        seen.add(group_key)
        match = freq_expanded.loc[
            (freq_expanded["route_id"] == trip["route_id"])
            & (freq_expanded["service_profile_id"] == trip["service_profile_id"])
            & (freq_expanded["direction"] == trip["direction_id"])
        ]
        if match.empty:
            continue

        row = match.iloc[0]
        rows.append(
            {
                "trip_id": trip["trip_id"],
                "start_time": row.get("start_time_prf", row["start_time"]),
                "end_time": row.get("end_time_prf", row["end_time"]),
                "headway_secs": int(_get_headway_secs(row)),
                "exact_times": 0,
            }
        )

    if not rows:
        return None

    return pd.DataFrame(rows)


def build_feed(
    pfeed: ProtoFeed,
    buffer: float = cs.BUFFER,
    stop_offset: float = cs.STOP_OFFSET,
    num_stops_per_shape: int = 2,
    stop_spacing: float | None = None,
    speed_mode: str = "proportional",
    cluster_h3: bool = False,
    h3_resolution: int = cs.DEFAULT_H3_RESOLUTION,
    use_frequencies: bool = False,
    projected_stop_tolerance: float = 5.0,
    max_segment_speed_kmh: float = DEFAULT_MAX_SEGMENT_SPEED_KMH,
    used_stops_only: bool = False,
    drop_orphans: bool = False,
) -> gk.Feed:
    """Convert a ``ProtoFeed`` into a ``gtfs_kit.Feed``."""
    agency = build_agency(pfeed)
    calendar, calendar_dates, profile_to_service = build_calendar(pfeed)
    routes = build_routes(pfeed)
    shapes = build_shapes(pfeed)

    stops = build_stops(
        pfeed,
        shapes=shapes,
        offset=stop_offset,
        num_stops=num_stops_per_shape,
        spacing=stop_spacing,
        cluster_h3=cluster_h3,
        h3_resolution=h3_resolution,
    )
    trips = build_trips(pfeed, routes, profile_to_service)
    stop_times = build_stop_times(
        pfeed,
        shapes=shapes,
        stops=stops,
        trips=trips,
        buffer=buffer,
        speed_mode=speed_mode,
        projected_stop_tolerance=projected_stop_tolerance,
        max_segment_speed_kmh=max_segment_speed_kmh,
    )

    frequencies = None
    if use_frequencies:
        frequencies = build_frequencies(pfeed, trips)

    if used_stops_only:
        stops = _filter_used_stops(stops, stop_times)

    if "agency_id" not in agency.columns:
        agency["agency_id"] = "1"

    if "agency_id" not in routes.columns and not routes.empty:
        routes["agency_id"] = agency["agency_id"].iat[0]

    feed = gk.Feed(
        agency=agency,
        calendar=calendar,
        calendar_dates=calendar_dates,
        routes=routes,
        shapes=shapes,
        stops=stops,
        stop_times=stop_times,
        trips=trips,
        frequencies=frequencies,
        dist_units="m",
    )

    if drop_orphans:
        return feed.drop_zombies()

    return feed
