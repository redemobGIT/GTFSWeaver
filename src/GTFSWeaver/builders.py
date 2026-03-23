"""
GTFS table builders and feed orchestrator.

GTFS compliance notes
---------------------
- ``agency.txt``: agency_name, agency_url, agency_timezone.
- ``routes.txt``: route_id (unique), route_short_name, route_long_name,
  route_type.
- ``trips.txt``: route_id → routes, service_id → calendar, trip_id
  (unique), direction_id (0/1), shape_id → shapes.
- ``stop_times.txt``: trip_id → trips, arrival_time (HH:MM:SS),
  departure_time, stop_id → stops, stop_sequence (int ≥ 0),
  shape_dist_traveled (non-decreasing).
- ``calendar.txt``: service_id (unique), monday–sunday, start_date,
  end_date (YYYYMMDD).
- ``calendar_dates.txt``: service_id → calendar, date, exception_type
  (1=added, 2=removed).
- ``shapes.txt``: shape_id, shape_pt_lat, shape_pt_lon,
  shape_pt_sequence (int ≥ 0).
- ``frequencies.txt``: trip_id → trips, start_time, end_time,
  headway_secs (int > 0).

All FK references are validated by ``gtfs_kit.Feed.drop_zombies()``.
"""

from __future__ import annotations

import geopandas as gpd
import gtfs_kit as gk
import numpy as np
import pandas as pd

from . import constants as cs
from .geometry import (
    cluster_stops_h3,
    compute_shape_point_speeds,
    get_stops_nearby,
    make_stop_points,
)
from .models import ProtoFeed, TripKey, make_route_id

KMH_TO_MS = 1000 / 3600


# ── Helpers ──────────────────────────────────────────────────────────

def _time_duration(start: str, end: str, units: str = "s") -> float:
    divisor = {"s": 1, "min": 60, "h": 3600}
    if units not in divisor:
        raise ValueError(f"units must be one of {list(divisor)}")
    return (gk.timestr_to_seconds(end) - gk.timestr_to_seconds(start)) / divisor[units]


def _get_headway_secs(row: pd.Series) -> float:
    """Prefer headway_mins (exact) over frequency (lossy int)."""
    if "headway_mins" in row.index and pd.notna(row.get("headway_mins")):
        return float(row["headway_mins"]) * 60
    return 3600 / row["frequency"]


def _get_num_trips(row: pd.Series) -> int:
    """Prefer headway_mins for precision."""
    if "headway_mins" in row.index and pd.notna(row.get("headway_mins")):
        dur = _time_duration(row["start_time"], row["end_time"], "min")
        return max(1, int(dur / float(row["headway_mins"])))
    dur = _time_duration(row["start_time"], row["end_time"], "h")
    return max(1, int(row["frequency"] * dur))


def _expand_direction_both(df: pd.DataFrame) -> pd.DataFrame:
    """Expand direction=2 (BOTH) → paired 0 and 1 rows."""
    mask = df["direction"] == 2
    if not mask.any():
        return df
    both = df.loc[mask]
    rest = df.loc[~mask]
    return pd.concat(
        [rest, both.assign(direction=0), both.assign(direction=1)],
        ignore_index=True,
    )


# ── Agency ───────────────────────────────────────────────────────────

def build_agency(pfeed: ProtoFeed) -> pd.DataFrame:
    r = pfeed.meta.iloc[0]
    return pd.DataFrame({
        "agency_name": [r["agency_name"]],
        "agency_url": [r["agency_url"]],
        "agency_timezone": [r["agency_timezone"]],
    })


# ── Calendar + Calendar Dates ────────────────────────────────────────

def build_calendar(pfeed: ProtoFeed):
    weekdays = list(cs.WEEKDAYS)
    windows = pfeed.service_windows.copy()
    has_hol = "holiday" in windows.columns

    def _sid(bits, hol):
        tag = "srv" + "".join(str(b) for b in bits)
        return f"{tag}_FER" if hol else tag

    w2s: dict[str, str] = {}
    seen: set[tuple[tuple[int, ...], bool]] = set()
    for _, row in windows.iterrows():
        bits = tuple(int(row[d]) for d in weekdays)
        hol = bool(row.get("holiday", 0)) if has_hol else False
        w2s[row["service_window_id"]] = _sid(bits, hol)
        seen.add((bits, hol))

    sd, ed = pfeed.meta["start_date"].iat[0], pfeed.meta["end_date"].iat[0]
    cal = pd.DataFrame(
        [[_sid(b, h)] + ([0]*7 if h else list(b)) + [sd, ed] for b, h in seen],
        columns=["service_id"] + weekdays + ["start_date", "end_date"],
    )

    cdates = None
    if pfeed.has_holidays:
        hdates = pfeed.holidays["date"].tolist()
        cdates = pd.DataFrame([
            {"service_id": _sid(b, h), "date": d,
             "exception_type": 1 if h else 2}
            for b, h in seen for d in hdates
        ])

    return cal, cdates, w2s


# ── Routes ───────────────────────────────────────────────────────────

def build_routes(pfeed: ProtoFeed) -> pd.DataFrame:
    return (
        pfeed.resolved_frequencies
        .filter(["route_short_name", "route_long_name", "route_type"])
        .drop_duplicates()
        .assign(route_id=lambda df: df["route_short_name"].map(make_route_id))
    )


# ── Shapes ───────────────────────────────────────────────────────────

def build_shapes(pfeed: ProtoFeed) -> pd.DataFrame:
    rows: list[list] = []
    for shape_id, geom in pfeed.shapes[
        ["shape_id", "geometry"]
    ].itertuples(index=False):
        d = pfeed.shapes_extra.get(shape_id)
        if d is None:
            continue
        if d == 2:
            _append(rows, f"{shape_id}{cs.SEP}1", geom.coords)
            _append(rows, f"{shape_id}{cs.SEP}0", reversed(geom.coords))
        else:
            _append(rows, f"{shape_id}{cs.SEP}{d}", geom.coords)
    return pd.DataFrame(
        rows,
        columns=["shape_id", "shape_pt_sequence", "shape_pt_lon", "shape_pt_lat"],
    )


def _append(rows, shape_id, coords):
    rows.extend(
        [shape_id, seq, lon, lat]
        for seq, (lon, lat) in enumerate(coords)
    )


# ── Stops ────────────────────────────────────────────────────────────

def build_stops(
    pfeed: ProtoFeed,
    shapes: pd.DataFrame | None = None,
    offset: float = cs.STOP_OFFSET,
    num_stops: int = 2,
    spacing: float | None = None,
    cluster_h3: bool = False,
    h3_resolution: int = cs.DEFAULT_H3_RESOLUTION,
) -> pd.DataFrame:
    if pfeed.stops is not None:
        return pfeed.stops.copy()
    if shapes is None:
        raise ValueError("Must provide shapes when pfeed.stops is None")

    shapes_g = (
        gk.geometrize_shapes(shapes, use_utm=True)
        .assign(base_shape=lambda df: df["shape_id"].str.rsplit(cs.SEP, n=1).str[0])
        .drop_duplicates("base_shape")
    )
    stops = (
        make_stop_points(
            shapes_g, id_col="shape_id", offset=offset,
            side=pfeed.traffic_side, num_stops=num_stops, spacing=spacing,
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
        stops = cluster_stops_h3(stops.set_crs(cs.WGS84), resolution=h3_resolution)
    return stops.filter(["stop_id", "stop_name", "stop_lon", "stop_lat"])


# ── Trips ────────────────────────────────────────────────────────────

def build_trips(pfeed, routes, window_to_service):
    resolved = pfeed.resolved_frequencies.drop(columns=["route_id"], errors="ignore")
    route_freq = (
        routes[["route_id", "route_short_name"]]
        .merge(resolved, on="route_short_name")
        .merge(pfeed.service_windows)
    )
    trip_rows: list[dict] = []
    for _, row in route_freq.iterrows():
        sid = window_to_service.get(row["service_window_id"])
        if sid is None:
            continue
        sched = row.get("schedule_type", cs.SCHEDULE_HEADWAY)
        dirs = [0, 1] if row["direction"] == 2 else [int(row["direction"])]
        n = 1 if sched == cs.SCHEDULE_FIXED else _get_num_trips(row)
        for d in dirs:
            shape_id = f"{row['shape_id']}{cs.SEP}{d}"
            for seq in range(n):
                key = TripKey(row["route_id"], row["service_window_id"],
                              row["start_time"], d, seq)
                trip_rows.append({
                    "route_id": row["route_id"],
                    "trip_id": key.to_trip_id(),
                    "direction_id": d,
                    "shape_id": shape_id,
                    "service_id": sid,
                })
    return pd.DataFrame(trip_rows)


# ── Stop times ───────────────────────────────────────────────────────

def _st_proportional(tid, nearby, line, tt_s, t0):
    total = line.length
    if total == 0:
        return pd.DataFrame()
    proj = nearby.assign(
        shape_dist_traveled=nearby.geometry.apply(line.project),
    ).sort_values("shape_dist_traveled", ignore_index=True)
    proj["arrival_time"] = t0 + tt_s * (proj["shape_dist_traveled"] / total)
    proj["departure_time"] = proj["arrival_time"]
    proj["trip_id"] = tid
    proj["stop_sequence"] = range(len(proj))
    return proj.filter([
        "trip_id", "stop_id", "stop_sequence",
        "arrival_time", "departure_time", "shape_dist_traveled",
    ])


def _st_zones(tid, nearby, sid, line, zones_utm, rt, sps, dflt_spd, t0):
    sp = (
        sps.loc[lambda df: df["shape_id"] == sid]
        .assign(speed=lambda df: df["speed"] * KMH_TO_MS)
        .filter(["shape_id", "shape_dist_traveled", "speed_zone_id", "speed"])
    )
    zn = (
        zones_utm.loc[lambda df: df["route_type"] == rt]
        .assign(speed=lambda df: df["speed"] * KMH_TO_MS)
    )
    dflt_ms = dflt_spd * KMH_TO_MS

    stop_pos = (
        nearby
        .assign(shape_dist_traveled=lambda df: df.geometry.apply(line.project))
        .sjoin(zn).sort_values("shape_dist_traveled", ignore_index=True)
        .filter(["stop_id", "shape_dist_traveled", "speed_zone_id", "speed"])
    )
    m = (
        pd.concat([stop_pos, sp])
        .sort_values("shape_dist_traveled", ignore_index=True)
        .assign(
            speed=lambda df: df["speed"].replace({np.inf: dflt_ms}),
            dist_to_next=lambda df: df["shape_dist_traveled"].diff().shift(-1).fillna(0),
            weight_to_next=lambda df: df["dist_to_next"] * df["speed"],
            swt=lambda df: df["weight_to_next"].cumsum().shift(1).fillna(0),
        )
        .loc[lambda df: df["stop_id"].notna()]
        .assign(
            trip_id=tid,
            d2n=lambda df: df["shape_dist_traveled"].diff().shift(-1).fillna(0),
            w2n=lambda df: df["swt"].diff().shift(-1).fillna(0),
            s2n=lambda df: (df["w2n"] / df["d2n"]).fillna(0),
            dur=lambda df: (df["d2n"] / df["s2n"]).fillna(0),
            arrival_time=lambda df: df["dur"].shift(1).cumsum().fillna(0) + t0,
            departure_time=lambda df: df["arrival_time"],
            stop_sequence=lambda df: range(len(df)),
        )
        .filter(["trip_id", "stop_id", "stop_sequence",
                 "arrival_time", "departure_time", "shape_dist_traveled"])
    )
    return m


def build_stop_times(pfeed, routes, shapes, stops, trips,
                     buffer=cs.BUFFER, speed_mode="zones"):
    freq = pfeed.resolved_frequencies
    freq_exp = _expand_direction_both(
        freq.drop(columns=["shape_id", "route_id"], errors="ignore")
    )
    rf = routes.filter(["route_id", "route_short_name"]).merge(
        freq_exp, on="route_short_name",
    )

    tk = trips.rename(columns={"direction_id": "direction"}).assign(
        service_window_id=lambda df: df["trip_id"].map(
            lambda t: TripKey.from_trip_id(t).service_window_id
        ),
    )
    # Merge on 3 keys — prevents cross-product when ida/volta share window
    te = tk.merge(rf, on=["route_id", "service_window_id", "direction"])

    su = gk.geometrize_shapes(shapes, use_utm=True).set_index("shape_id")
    stu = gk.geometrize_stops(stops, use_utm=True)
    szu = pfeed.resolved_speed_zones.to_crs(pfeed.utm_crs)

    has_tt = "travel_time_mins" in freq.columns
    if speed_mode == "proportional" and not has_tt:
        raise ValueError("speed_mode='proportional' requires 'travel_time_mins'.")

    frames: list[pd.DataFrame] = []
    for (rt, sid, spd), grp in te.groupby(["route_type", "shape_id", "speed"]):
        if sid not in su.index:
            continue
        line = su.loc[sid].geometry
        nearby = get_stops_nearby(stu, line, pfeed.traffic_side, buffer)
        if nearby.empty:
            continue

        if speed_mode == "proportional":
            tmpl = _st_proportional("_t", nearby, line,
                                     float(grp["travel_time_mins"].iloc[0]) * 60, 0)
        else:
            sps = compute_shape_point_speeds(
                shapes, pfeed.resolved_speed_zones, rt, use_utm=True)
            tmpl = _st_zones("_t", nearby, sid, line, szu, rt, sps, spd, 0)

        if tmpl.empty:
            continue

        for _, row in grp.iterrows():
            key = TripKey.from_trip_id(row["trip_id"])
            sched = row.get("schedule_type", cs.SCHEDULE_HEADWAY)
            base = gk.timestr_to_seconds(key.start_time)
            offset = base if sched == cs.SCHEDULE_FIXED else base + _get_headway_secs(row) * key.sequence

            frames.append(tmpl.assign(
                trip_id=row["trip_id"],
                arrival_time=tmpl["arrival_time"] + offset,
                departure_time=tmpl["arrival_time"] + offset,
            ))

    if not frames:
        return pd.DataFrame(columns=[
            "trip_id", "stop_id", "stop_sequence",
            "arrival_time", "departure_time",
        ])

    result = pd.concat(frames, ignore_index=True)
    result["shape_dist_traveled"] = result["shape_dist_traveled"].round()
    for col in ("arrival_time", "departure_time"):
        result[col] = result[col].apply(
            lambda x: gk.timestr_to_seconds(x, inverse=True)
        )
    return result


# ── Frequencies ──────────────────────────────────────────────────────

def build_frequencies(pfeed, trips):
    freq = pfeed.resolved_frequencies.copy()
    # Ensure route_id exists (directory path may lack it)
    if "route_id" not in freq.columns:
        freq["route_id"] = freq["route_short_name"].map(make_route_id)
    sw = pfeed.service_windows[["service_window_id", "start_time", "end_time"]]
    fe = _expand_direction_both(freq).merge(sw, on="service_window_id",
                                             suffixes=("", "_sw"))
    fe = fe.loc[fe["schedule_type"] == cs.SCHEDULE_HEADWAY]
    if fe.empty:
        return None

    rows, seen = [], set()
    for _, tr in trips.iterrows():
        key = TripKey.from_trip_id(tr["trip_id"])
        gk_ = (key.route_id, key.service_window_id, key.direction)
        if gk_ in seen:
            continue
        seen.add(gk_)
        m = fe.loc[
            (fe["route_id"] == key.route_id)
            & (fe["service_window_id"] == key.service_window_id)
            & (fe["direction"] == key.direction)
        ]
        if m.empty:
            continue
        fr = m.iloc[0]
        rows.append({
            "trip_id": tr["trip_id"],
            "start_time": fr.get("start_time_sw", fr["start_time"]),
            "end_time": fr.get("end_time_sw", fr["end_time"]),
            "headway_secs": int(_get_headway_secs(fr)),
            "exact_times": 0,
        })
    return pd.DataFrame(rows) if rows else None


# ── Orchestrator ─────────────────────────────────────────────────────

def build_feed(
    pfeed: ProtoFeed,
    buffer: float = cs.BUFFER,
    stop_offset: float = cs.STOP_OFFSET,
    num_stops_per_shape: int = 2,
    stop_spacing: float | None = None,
    speed_mode: str = "zones",
    cluster_h3: bool = False,
    h3_resolution: int = cs.DEFAULT_H3_RESOLUTION,
    use_frequencies: bool = False,
) -> gk.Feed:
    """Convert a ProtoFeed into a complete ``gtfs_kit.Feed``."""
    agency = build_agency(pfeed)
    cal, cdates, w2s = build_calendar(pfeed)
    routes = build_routes(pfeed)
    shapes = build_shapes(pfeed)
    stops = build_stops(pfeed, shapes, offset=stop_offset,
                        num_stops=num_stops_per_shape, spacing=stop_spacing,
                        cluster_h3=cluster_h3, h3_resolution=h3_resolution)
    trips = build_trips(pfeed, routes, w2s)
    st = build_stop_times(pfeed, routes, shapes, stops, trips,
                          buffer=buffer, speed_mode=speed_mode)
    freqs = build_frequencies(pfeed, trips) if use_frequencies else None

    return gk.Feed(
        agency=agency, calendar=cal, calendar_dates=cdates,
        routes=routes, shapes=shapes, stops=stops,
        stop_times=st, trips=trips, frequencies=freqs,
        dist_units="m",
    ).drop_zombies()