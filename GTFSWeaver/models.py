"""
Domain models: ProtoFeed, TripKey, Direction, parse_service_pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property
from typing import NamedTuple

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely.geometry as sg

from . import constants as cs


# ── Direction ────────────────────────────────────────────────────────

class Direction(IntEnum):
    REVERSE = 0
    FORWARD = 1
    BOTH = 2

    @classmethod
    def from_label(cls, label: str | int) -> Direction:
        """Parse from int, digit string, or PT/EN label."""
        if isinstance(label, (int, np.integer)):
            return cls(int(label))
        key = str(label).strip().lower()
        mapping = {
            "forward": cls.FORWARD, "reverse": cls.REVERSE,
            "both": cls.BOTH, "ida": cls.FORWARD, "volta": cls.REVERSE,
            "0": cls.REVERSE, "1": cls.FORWARD, "2": cls.BOTH,
        }
        if key in mapping:
            return mapping[key]
        raise ValueError(
            f"Invalid direction '{label}'. "
            f"Expected: 0/1/2, ida/volta/both, forward/reverse."
        )


# ── Service pattern ──────────────────────────────────────────────────

def parse_service_pattern(pattern: str) -> tuple[tuple[int, ...], bool]:
    """
    Convert a service pattern label to (weekday_bits, holiday_flag).

    Predefined: DU, SAB, DOM, FER, DU_SAB, TODOS.
    Custom: 7-digit bitstring (``"1010100"``) or comma-separated
    day names (``"mon,wed,fri"``).
    """
    key = pattern.strip().upper()
    if key in cs.SERVICE_PATTERNS:
        return cs.SERVICE_PATTERNS[key]

    if len(key) == 7 and all(c in "01" for c in key):
        return tuple(int(c) for c in key), False

    day_abbrevs = {d[:3]: i for i, d in enumerate(cs.WEEKDAYS)}
    parts = [p.strip().lower()[:3] for p in pattern.split(",")]
    if all(p in day_abbrevs for p in parts):
        bits = [0] * 7
        for p in parts:
            bits[day_abbrevs[p]] = 1
        return tuple(bits), False

    raise ValueError(
        f"Invalid service_pattern '{pattern}'. "
        f"Use: {', '.join(cs.SERVICE_PATTERNS)}, "
        f"a 7-digit bitstring, or comma-separated day names."
    )


# ── ID helpers ───────────────────────────────────────────────────────

def make_shape_id(route_short_name: str | int, direction: int | str) -> str:
    """
    Generate a shape_id from route name and direction.

    Any hyphens in the route name are replaced with underscores to
    keep ``cs.SEP`` unambiguous in compound IDs.
    """
    name = str(route_short_name).replace(cs.SEP, "_")
    d = Direction.from_label(direction) if not isinstance(direction, int) else direction
    return f"{name}{cs.SEP}{d}"


def make_route_id(route_short_name: str | int) -> str:
    """Generate a route_id, sanitised for use in compound IDs."""
    return "r" + str(route_short_name).replace(cs.SEP, "_")


# ── TripKey ──────────────────────────────────────────────────────────

class TripKey(NamedTuple):
    route_id: str
    service_window_id: str
    start_time: str
    direction: int
    sequence: int

    def to_trip_id(self) -> str:
        return cs.SEP.join([
            "t", self.route_id, self.service_window_id,
            self.start_time, str(self.direction), str(self.sequence),
        ])

    @classmethod
    def from_trip_id(cls, trip_id: str) -> TripKey:
        parts = trip_id.split(cs.SEP)
        if len(parts) != 6 or parts[0] != "t":
            raise ValueError(f"Malformed trip_id: '{trip_id}'")
        _, route, window, start, direction, seq = parts
        return cls(route, window, start, int(direction), int(seq))


# ── ProtoFeed ────────────────────────────────────────────────────────

@dataclass
class ProtoFeed:
    """
    Intermediate representation between user input and GTFS output.

    Internal schema (normalised regardless of input format):
    - ``service_windows``: service_window_id, start_time, end_time,
      monday–sunday, holiday.
    - ``frequencies``: route_short_name, route_long_name, route_type,
      service_window_id, shape_id, direction, frequency, schedule_type,
      [speed, travel_time_mins, headway_mins].
    """

    meta: pd.DataFrame
    service_windows: pd.DataFrame
    shapes: gpd.GeoDataFrame
    frequencies: pd.DataFrame
    stops: pd.DataFrame | None = None
    speed_zones: gpd.GeoDataFrame | None = None
    holidays: pd.DataFrame | None = None
    boundary: gpd.GeoDataFrame | None = None

    @cached_property
    def utm_crs(self) -> str:
        source = self.boundary if self.boundary is not None else self.shapes
        return source.estimate_utm_crs()

    @cached_property
    def resolved_frequencies(self) -> pd.DataFrame:
        df = self.frequencies.copy()
        if "speed" not in df.columns:
            df["speed"] = np.nan
        df["speed"] = df["speed"].fillna(
            df["route_type"].map(cs.SPEED_BY_ROUTE_TYPE)
        )
        df["direction"] = df["direction"].map(Direction.from_label).astype(int)
        if "schedule_type" not in df.columns:
            df["schedule_type"] = cs.SCHEDULE_HEADWAY
        df["schedule_type"] = (
            df["schedule_type"].astype(str).str.strip().str.lower()
            .fillna(cs.SCHEDULE_HEADWAY)
        )
        return df

    @cached_property
    def shapes_extra(self) -> dict[str, int]:
        return (
            self.resolved_frequencies
            .groupby("shape_id")["direction"]
            .agg(lambda d: 2 if d.nunique() > 1 or 2 in d.values else d.iloc[0])
            .to_dict()
        )

    @cached_property
    def service_area(self) -> gpd.GeoDataFrame:
        bbox = sg.box(*self.shapes.total_bounds).buffer(0.01)
        return gpd.GeoDataFrame(geometry=[bbox], crs=cs.WGS84)

    @cached_property
    def resolved_speed_zones(self) -> gpd.GeoDataFrame:
        if self.speed_zones is None:
            return pd.concat([
                self.service_area.assign(
                    route_type=rt,
                    speed_zone_id=f"default{cs.SEP}{rt}",
                    speed=np.inf,
                )
                for rt in self.resolved_frequencies["route_type"].unique()
            ], ignore_index=True)

        def _clean(group):
            rt = group["route_type"].iloc[0]
            return _clean_speed_zones(
                group, self.service_area, f"default{cs.SEP}{rt}",
            )
        return (
            self.speed_zones
            .groupby("route_type", group_keys=False).apply(_clean)
            .filter(["route_type", "speed_zone_id", "speed", "geometry"])
        )

    @property
    def traffic_side(self) -> str:
        tz = self.meta["agency_timezone"].iat[0]
        return cs.TRAFFIC_SIDE_BY_TIMEZONE.get(tz, "right")

    @property
    def has_holidays(self) -> bool:
        return self.holidays is not None and not self.holidays.empty

    def route_types(self) -> list[int]:
        return self.resolved_frequencies["route_type"].unique().tolist()

    def copy(self) -> ProtoFeed:
        kw = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            kw[k] = v.copy() if isinstance(v, (pd.DataFrame, gpd.GeoDataFrame)) else v
        return ProtoFeed(**kw)


def _clean_speed_zones(
    speed_zones: gpd.GeoDataFrame,
    service_area: gpd.GeoDataFrame,
    default_zone_id: str,
    default_speed: float = np.inf,
) -> gpd.GeoDataFrame:
    if service_area.geom_equals(speed_zones.union_all()).all():
        return speed_zones
    return (
        speed_zones.clip(service_area)
        .overlay(service_area, how="union")
        .assign(
            route_type=lambda x: x["route_type"].ffill().astype(int),
            speed_zone_id=lambda x: x["speed_zone_id"].fillna(default_zone_id),
            speed=lambda x: x["speed"].fillna(default_speed),
        )
        .filter(["route_type", "speed_zone_id", "speed", "geometry"])
        .sort_values("speed_zone_id", ignore_index=True)
    )