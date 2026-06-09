"""
Domain models: ProtoFeed, Direction, and identity generators.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import IntEnum
from functools import cached_property

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely.geometry as sg

from . import constants as cs

# ── Core Domain Models ───────────────────────────────────────────────


@dataclass
class ProtoFeed:
    """
    Intermediate representation between user input and GTFS output.

    Internal schema (normalised regardless of input format):
    - ``service_profiles``: service_profile_id, start_time, end_time,
      monday–sunday, holiday.
    - ``frequencies``: route_short_name, route_long_name, route_type,
      service_profile_id, shape_id, direction, frequency, schedule_type,
      [speed, travel_time_mins, headway_mins].
    """

    meta: pd.DataFrame
    service_profiles: pd.DataFrame
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
        df["speed"] = df["speed"].fillna(df["route_type"].map(cs.SPEED_BY_ROUTE_TYPE))
        if "schedule_type" not in df.columns:
            df["schedule_type"] = cs.SCHEDULE_HEADWAY
        df["schedule_type"] = (
            df["schedule_type"]
            .astype(str)
            .str.strip()
            .str.lower()
            .fillna(cs.SCHEDULE_HEADWAY)
        )
        return df

    @cached_property
    def shapes_extra(self) -> dict[str, int]:
        return (
            self.resolved_frequencies.groupby("shape_id")["direction"]
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
            return pd.concat(
                [
                    self.service_area.assign(
                        route_type=rt,
                        speed_zone_id=f"default_{rt}",
                        speed=np.inf,
                    )
                    for rt in self.resolved_frequencies["route_type"].unique()
                ],
                ignore_index=True,
            )

        def _clean(group):
            rt = group["route_type"].iloc[0]
            return _clean_speed_zones(
                group,
                self.service_area,
                f"default_{rt}",
            )

        return (
            self.speed_zones.groupby("route_type", group_keys=False)
            .apply(_clean)
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


# ── Secondary Domain Models ──────────────────────────────────────────


class Direction(IntEnum):
    FORWARD = 0
    REVERSE = 1
    BOTH = 2

    @classmethod
    def from_label(cls, label: str | int) -> "Direction":
        """Parse direction from int, digit string, or PT/EN label."""
        if isinstance(label, (int, np.integer)):
            return cls(int(label))

        key = str(label).strip().lower()
        mapping = {
            "inbound": cls.FORWARD,
            "ida": cls.FORWARD,
            "forward": cls.FORWARD,
            "outbound": cls.REVERSE,
            "volta": cls.REVERSE,
            "reverse": cls.REVERSE,
            "both": cls.BOTH,
            "ambos": cls.BOTH,
            "0": cls.FORWARD,
            "1": cls.REVERSE,
            "2": cls.BOTH,
        }

        if key in mapping:
            return mapping[key]

        raise ValueError(
            f"Invalid direction {label!r}. "
            "Expected 0/1/2, ida/volta, inbound/outbound, or both."
        )


# ── Identity Generators ──────────────────────────────────────────────


def _make_slug(text: str | int) -> str:
    """
    Convert arbitrary text into a strict ASCII, lowercase alphanumeric slug.

    Transforms messy inputs like "Linha 101 - Rápido!" into "linha_101_rapido".
    """
    raw = str(text).strip().lower()
    # Normalize Unicode to separate accents from base characters, then drop non-ASCII
    ascii_text = (
        unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    )
    # Replace anything that isn't a lowercase letter or number with an underscore
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text)

    return slug.strip("_")


def make_route_id(route_short_name: str | int) -> str:
    """Generate a clean, URL-safe route_id."""
    return f"r_{_make_slug(route_short_name)}"


def make_service_profile_id(
    *,
    schedule_type: str,
    start_time: str,
    end_time: str | None,
    pattern: str,
) -> str:
    """
    Create a deterministic surrogate key for a service profile.

    The 10-character hash is long enough for normal feed sizes, while still
    keeping IDs compact.
    """
    raw_parts = [schedule_type, start_time, end_time, pattern]
    clean_parts = [
        str(part).strip() for part in raw_parts if pd.notna(part) and part != ""
    ]

    signature = "|".join(clean_parts)
    short_hash = hashlib.md5(signature.encode()).hexdigest()[:10]

    return f"prf_{short_hash}"


def make_trip_id(
    route_id: str,
    service_profile_id: str,
    direction: int,
    sequence: int,
) -> str:
    """Generate a deterministic trip_id unique across service profiles."""
    return f"t_{route_id}_{service_profile_id}_{direction}_{sequence}"


# ── Public API Helpers ───────────────────────────────────────────────


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

    # TODO: Below is legacy and will likely be removed once we fully switch to
    # service profiles, but it can stay for now since it's not harmful
    # and allows some flexibility in user input.
    if len(key) == 7 and set(key).issubset({"0", "1"}):
        return tuple(int(c) for c in key), False

    # Comma-separated days (e.g., "mon, wed, fri")
    valid_days = {day[:3].upper() for day in cs.WEEKDAYS}
    user_days = {p.strip()[:3] for p in key.split(",")}

    if user_days.issubset(valid_days):
        bits = tuple(1 if day[:3].upper() in user_days else 0 for day in cs.WEEKDAYS)
        return bits, False

    raise ValueError(
        f"Invalid service_pattern '{pattern}'. "
        f"Expected predefined key ({', '.join(cs.SERVICE_PATTERNS)}), "
        "a 7-digit bitstring, or comma-separated days."
    )


# TODO: incorporate this on pipeflow side and use it to infer holiday behavior in the absence of a holidays table
def holiday_action_from_pattern(pattern: str) -> str:
    """
    Map a service pattern to holiday behavior.

    Returns:
        "add"    -> service is ADDED on holidays
        "remove" -> service is REMOVED on holidays
        "none"   -> holidays do not alter the weekly pattern
    """
    key = str(pattern).strip().upper()

    if key in {"DOM", "FER"}:
        return "add"

    if key in {"DU", "SAB", "DU_SAB"}:
        return "remove"

    if key == "TODOS":
        return "none"

    # conservative default for custom bitstrings / day lists
    return "none"


# ── Private Internal Helpers ─────────────────────────────────────────


def create_shape_id_label(route_short_name: str | int, direction: str | int) -> str:
    """Generate the final directional shape_id used inside ProtoFeed."""
    parsed = Direction.from_label(direction)

    if parsed == Direction.BOTH:
        raise ValueError(
            "Cannot create one final shape_id for direction='both'. "
            "Expand it into directions 0 and 1 first."
        )

    return f"sh_{_make_slug(route_short_name)}_{int(parsed)}"


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
