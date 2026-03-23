"""
Input validation via pandera schemas.

Two schema sets: classic directory format and Excel format.
All validation runs *before* ProtoFeed construction.
"""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd
import pandera as pa
import pytz

from . import constants as cs

_URL = re.compile(
    r"^https?://(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|\d{1,3}(?:\.\d{1,3}){3})"
    r"(?::\d+)?(?:/?|[/?]\S+)$",
    re.IGNORECASE | re.UNICODE,
)
_DATE = r"\d{8}"
_TIME = r"([01]\d|2[0-3]):[0-5]\d:[0-5]\d"
_NONBLANK = r"(?!\s*$).+"
_TIMEZONES = set(pytz.all_timezones)


# ── Classic directory schemas ────────────────────────────────────────

SCHEMA_META = pa.DataFrameSchema({
    "agency_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "agency_url": pa.Column(str, pa.Check.str_matches(_URL)),
    "agency_timezone": pa.Column(str, pa.Check.isin(_TIMEZONES)),
    "start_date": pa.Column(str, pa.Check.str_matches(_DATE)),
    "end_date": pa.Column(str, pa.Check.str_matches(_DATE)),
}, checks=pa.Check(lambda df: len(df) == 1, error="meta: expected 1 row"),
   index=pa.Index(int), strict=True)

SCHEMA_SERVICE_WINDOWS = pa.DataFrameSchema({
    "service_window_id": pa.Column(str, pa.Check.str_matches(_NONBLANK), unique=True),
    "start_time": pa.Column(str, pa.Check.str_matches(_TIME)),
    "end_time": pa.Column(str, pa.Check.str_matches(_TIME)),
    **{d: pa.Column(int, pa.Check.isin(range(2))) for d in cs.WEEKDAYS},
    "holiday": pa.Column(int, pa.Check.isin(range(2)), required=False),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict="filter")

SCHEMA_SHAPES = pa.DataFrameSchema({
    "shape_id": pa.Column(str, pa.Check.str_matches(_NONBLANK), unique=True),
    "geometry": pa.Column(checks=[
        pa.Check(lambda s: s.geom_type == "LineString"),
        pa.Check(lambda s: s.is_valid),
        pa.Check(lambda s: ~s.is_empty),
    ]),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict="filter")

SCHEMA_FREQUENCIES = pa.DataFrameSchema({
    "route_short_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "route_long_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "route_type": pa.Column(int, pa.Check.isin(cs.VALID_ROUTE_TYPES)),
    "service_window_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "shape_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "direction": pa.Column(int, pa.Check.isin(range(3))),
    "frequency": pa.Column(int, pa.Check.gt(0)),
    "speed": pa.Column(float, pa.Check.gt(0), required=False),
    "schedule_type": pa.Column(str, required=False),
    "travel_time_mins": pa.Column(float, pa.Check.gt(0), required=False, nullable=True),
    "headway_mins": pa.Column(float, pa.Check.gt(0), required=False, nullable=True),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict="filter")

SCHEMA_STOPS = pa.DataFrameSchema({
    "stop_id": pa.Column(str, pa.Check.str_matches(_NONBLANK), unique=True),
    "stop_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "stop_lat": pa.Column(float),
    "stop_lon": pa.Column(float),
    "stop_code": pa.Column(str, nullable=True, required=False, coerce=True),
    "stop_desc": pa.Column(str, nullable=True, required=False, coerce=True),
    "zone_id": pa.Column(str, nullable=True, required=False, coerce=True),
    "location_type": pa.Column(int, pa.Check.isin(range(5)), nullable=True, required=False),
    "parent_station": pa.Column(str, nullable=True, required=False),
    "stop_timezone": pa.Column(str, pa.Check.isin(_TIMEZONES), nullable=True, required=False),
    "wheelchair_boarding": pa.Column(int, pa.Check.isin(range(3)), nullable=True, required=False),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict="filter")

SCHEMA_SPEED_ZONES = pa.DataFrameSchema({
    "speed_zone_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "route_type": pa.Column(int, pa.Check.isin(cs.VALID_ROUTE_TYPES)),
    "speed": pa.Column(float, pa.Check.gt(0)),
    "geometry": pa.Column(checks=[
        pa.Check(lambda s: s.geom_type == "Polygon"),
        pa.Check(lambda s: s.is_valid),
        pa.Check(lambda s: ~s.is_empty),
    ]),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict=True)


# ── Excel schemas ────────────────────────────────────────────────────
#
# Excel routes sheet has NO shape_id.  shape_id is auto-generated from
# (route_short_name, direction_id) by the reader, matching the geo
# file's (route_short_name, direction) attributes.

SCHEMA_EXCEL_AGENCY = pa.DataFrameSchema({
    "agency_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "agency_url": pa.Column(str, pa.Check.str_matches(_URL)),
    "agency_timezone": pa.Column(str, pa.Check.isin(_TIMEZONES)),
    "start_date": pa.Column(str, pa.Check.str_matches(_DATE)),
    "end_date": pa.Column(str, pa.Check.str_matches(_DATE)),
    "agency_lang": pa.Column(str, required=False, nullable=True),
}, checks=pa.Check(lambda df: len(df) == 1, error="agency: expected 1 row"),
   index=pa.Index(int), strict="filter")

SCHEMA_EXCEL_ROUTES = pa.DataFrameSchema({
    "route_short_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "route_long_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "route_type": pa.Column(int, pa.Check.isin(cs.VALID_ROUTE_TYPES), required=False),
    "direction_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "schedule_type": pa.Column(str, pa.Check.isin([cs.SCHEDULE_HEADWAY, cs.SCHEDULE_FIXED])),
    "service_pattern": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
    "start_time": pa.Column(str, pa.Check.str_matches(_TIME)),
    "end_time": pa.Column(str, required=False, nullable=True),
    "headway_mins": pa.Column(float, required=False, nullable=True),
    "travel_time_mins": pa.Column(float, pa.Check.gt(0), required=False, nullable=True),
    "speed": pa.Column(float, pa.Check.gt(0), required=False, nullable=True),
}, checks=pa.Check(lambda df: len(df) >= 1, error="routes: expected ≥1 row"),
   index=pa.Index(int), strict="filter")

SCHEMA_EXCEL_HOLIDAYS = pa.DataFrameSchema({
    "date": pa.Column(str, pa.Check.str_matches(_DATE)),
    "description": pa.Column(str, required=False, nullable=True),
}, checks=pa.Check(lambda df: len(df) >= 1),
   index=pa.Index(int), strict="filter")


# ── Cross-checks ─────────────────────────────────────────────────────

def _check_id_subset(
    id_col: str,
    source: pd.DataFrame, source_name: str,
    target: pd.DataFrame, target_name: str,
) -> None:
    missing = set(source[id_col].unique()) - set(target[id_col].unique())
    if missing:
        raise ValueError(
            f"{id_col} in {source_name} not found in {target_name}: {missing}"
        )


def _check_headway_completeness(routes_df: pd.DataFrame) -> None:
    mask = routes_df["schedule_type"].str.lower() == cs.SCHEDULE_HEADWAY
    hw = routes_df.loc[mask]
    if hw.empty:
        return
    if "end_time" in hw.columns:
        bad = hw.loc[hw["end_time"].isna()]
        if not bad.empty:
            raise ValueError(
                f"Headway rows missing 'end_time' at index: {bad.index.tolist()}"
            )
    else:
        raise ValueError("Headway schedule requires 'end_time' column")
    if "headway_mins" in hw.columns:
        bad = hw.loc[hw["headway_mins"].isna() | (hw["headway_mins"] <= 0)]
        if not bad.empty:
            raise ValueError(
                f"Headway rows invalid 'headway_mins' at index: {bad.index.tolist()}"
            )
    else:
        raise ValueError("Headway schedule requires 'headway_mins' column")


def _check_service_patterns(routes_df: pd.DataFrame) -> None:
    from .models import parse_service_pattern
    for idx, pattern in routes_df["service_pattern"].items():
        try:
            parse_service_pattern(str(pattern))
        except ValueError as exc:
            raise ValueError(f"Row {idx}: {exc}") from None


def _check_route_direction_coverage(
    routes_df: pd.DataFrame,
    shapes_gdf: gpd.GeoDataFrame,
) -> None:
    """
    Every (route_short_name, direction_id) in Excel must have a
    matching (route_short_name, direction) feature in the geo file.
    """
    from .models import Direction

    # Normalise Excel directions to int
    excel_pairs = set()
    for _, row in routes_df.iterrows():
        d = Direction.from_label(row["direction_id"])
        if d == Direction.BOTH:
            excel_pairs.add((str(row["route_short_name"]), 0))
            excel_pairs.add((str(row["route_short_name"]), 1))
        else:
            excel_pairs.add((str(row["route_short_name"]), int(d)))

    # Normalise geo directions to int
    geo_pairs = set()
    for _, row in shapes_gdf.iterrows():
        d = Direction.from_label(row["direction"])
        geo_pairs.add((str(row["route_short_name"]), int(d)))

    missing = excel_pairs - geo_pairs
    if missing:
        formatted = [f"({r}, dir={d})" for r, d in sorted(missing)]
        raise ValueError(
            f"Routes in Excel not found in geo file: {', '.join(formatted)}"
        )


# ── Public entry points ──────────────────────────────────────────────

def validate_tables(tables: dict) -> None:
    """Validate classic directory-format tables."""
    for name, schema in [
        ("meta", SCHEMA_META), ("service_windows", SCHEMA_SERVICE_WINDOWS),
        ("shapes", SCHEMA_SHAPES), ("frequencies", SCHEMA_FREQUENCIES),
    ]:
        if tables.get(name) is None:
            raise ValueError(f"Required table '{name}' is missing")
        try:
            schema.validate(tables[name])
        except pa.errors.SchemaError as exc:
            raise ValueError(f"'{name}' validation failed: {exc}") from exc

    if tables.get("stops") is not None:
        SCHEMA_STOPS.validate(tables["stops"])
    if tables.get("speed_zones") is not None:
        SCHEMA_SPEED_ZONES.validate(tables["speed_zones"])

    _check_id_subset("shape_id", tables["frequencies"], "frequencies",
                     tables["shapes"], "shapes")
    _check_id_subset("service_window_id", tables["frequencies"], "frequencies",
                     tables["service_windows"], "service_windows")


def validate_excel_tables(
    agency_df: pd.DataFrame,
    routes_df: pd.DataFrame,
    shapes_gdf: gpd.GeoDataFrame,
    stops_gdf: gpd.GeoDataFrame | None = None,
    holidays_df: pd.DataFrame | None = None,
) -> None:
    """Validate Excel-format tables."""
    for name, schema, df in [
        ("agency", SCHEMA_EXCEL_AGENCY, agency_df),
        ("routes", SCHEMA_EXCEL_ROUTES, routes_df),
    ]:
        try:
            schema.validate(df)
        except pa.errors.SchemaError as exc:
            raise ValueError(f"'{name}' sheet: {exc}") from exc

    _check_headway_completeness(routes_df)
    _check_service_patterns(routes_df)

    # Geo file must have route_short_name + direction + LineStrings
    for col in ("route_short_name", "direction"):
        if col not in shapes_gdf.columns:
            raise ValueError(f"Routes geo file must have a '{col}' column")
    bad = shapes_gdf.loc[shapes_gdf.geometry.geom_type != "LineString"]
    if not bad.empty:
        raise ValueError(
            f"Routes geo file must contain only LineStrings. "
            f"Found: {bad.geometry.geom_type.unique().tolist()}"
        )

    _check_route_direction_coverage(routes_df, shapes_gdf)

    if stops_gdf is not None:
        if "stop_name" not in stops_gdf.columns:
            raise ValueError("Stops geo file must have a 'stop_name' column")
        bad = stops_gdf.loc[stops_gdf.geometry.geom_type != "Point"]
        if not bad.empty:
            raise ValueError("Stops geo file must contain only Point geometries")

    if holidays_df is not None and not holidays_df.empty:
        try:
            SCHEMA_EXCEL_HOLIDAYS.validate(holidays_df)
        except pa.errors.SchemaError as exc:
            raise ValueError(f"'holidays' sheet: {exc}") from exc