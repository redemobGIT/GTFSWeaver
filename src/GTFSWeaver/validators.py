"""Input validation for GTFSWeaver source tables.

Two schema families are provided:
- classic directory format
- Excel + geo format

All validation runs before ProtoFeed construction.
"""

from __future__ import annotations

import re

import geopandas as gpd
import pandas as pd
import pandera.pandas as pa
import pytz

from . import constants as cs
from .models import Direction

_URL = re.compile(
    r"^https?://(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
    r"\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?:/?|[/?]\S+)$",
    re.IGNORECASE | re.UNICODE,
)
_DATE = r"\d{8}"
_TIME = r"(?:\d{1,3}):[0-5]\d:[0-5]\d"
_NONBLANK = r"(?!\s*$).+"
_TIMEZONES = set(pytz.all_timezones)


SCHEMA_META = pa.DataFrameSchema(
    {
        "agency_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "agency_url": pa.Column(str, pa.Check.str_matches(_URL)),
        "agency_timezone": pa.Column(str, pa.Check.isin(_TIMEZONES)),
        "start_date": pa.Column(str, pa.Check.str_matches(_DATE)),
        "end_date": pa.Column(str, pa.Check.str_matches(_DATE)),
    },
    checks=pa.Check(lambda df: len(df) == 1, error="meta: expected 1 row"),
    index=pa.Index(int),
    strict=True,
)

SCHEMA_SERVICE_PROFILES = pa.DataFrameSchema(
    {
        "service_profile_id": pa.Column(
            str,
            pa.Check.str_matches(_NONBLANK),
            unique=True,
        ),
        "schedule_type": pa.Column(
            str,
            pa.Check.isin([cs.SCHEDULE_HEADWAY, cs.SCHEDULE_FIXED]),
        ),
        "start_time": pa.Column(str, pa.Check.str_matches(_TIME)),
        "end_time": pa.Column(
            str,
            pa.Check.str_matches(_TIME),
            nullable=True,
            required=False,
        ),
        "service_pattern": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        **{
            day: pa.Column(int, pa.Check.isin(range(2)))
            for day in cs.WEEKDAYS
        },
        "holiday": pa.Column(
            int,
            pa.Check.isin(range(2)),
            required=False,
        ),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_SHAPES = pa.DataFrameSchema(
    {
        "shape_id": pa.Column(
            str,
            pa.Check.str_matches(_NONBLANK),
            unique=True,
        ),
        "geometry": pa.Column(
            checks=[
                pa.Check(lambda s: s.geom_type == "LineString"),
                pa.Check(lambda s: s.is_valid),
                pa.Check(lambda s: ~s.is_empty),
            ]
        ),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_FREQUENCIES = pa.DataFrameSchema(
    {
        "route_short_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "route_long_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "route_type": pa.Column(
            int,
            pa.Check.isin(cs.VALID_ROUTE_TYPES),
            coerce=True,
        ),
        "service_profile_id": pa.Column(
            str,
            pa.Check.str_matches(_NONBLANK),
        ),
        "shape_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "direction": pa.Column(int, pa.Check.isin(range(3))),
        "frequency": pa.Column(int, pa.Check.gt(0)),
        "speed": pa.Column(
            float,
            pa.Check.gt(0),
            required=False,
            nullable=True,
        ),
        "schedule_type": pa.Column(str, required=False),
        "travel_time_mins": pa.Column(
            float,
            pa.Check.gt(0),
            required=False,
            nullable=True,
        ),
        "headway_mins": pa.Column(
            float,
            pa.Check.gt(0),
            required=False,
            nullable=True,
        ),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_STOPS = pa.DataFrameSchema(
    {
        "stop_id": pa.Column(
            str,
            pa.Check.str_matches(_NONBLANK),
            unique=True,
        ),
        "stop_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "stop_lat": pa.Column(float),
        "stop_lon": pa.Column(float),
        "stop_code": pa.Column(
            str,
            nullable=True,
            required=False,
            coerce=True,
        ),
        "stop_desc": pa.Column(
            str,
            nullable=True,
            required=False,
            coerce=True,
        ),
        "zone_id": pa.Column(
            str,
            nullable=True,
            required=False,
            coerce=True,
        ),
        "location_type": pa.Column(
            int,
            pa.Check.isin(range(5)),
            nullable=True,
            required=False,
        ),
        "parent_station": pa.Column(str, nullable=True, required=False),
        "stop_timezone": pa.Column(
            str,
            pa.Check.isin(_TIMEZONES),
            nullable=True,
            required=False,
        ),
        "wheelchair_boarding": pa.Column(
            int,
            pa.Check.isin(range(3)),
            nullable=True,
            required=False,
        ),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_SPEED_ZONES = pa.DataFrameSchema(
    {
        "speed_zone_id": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "route_type": pa.Column(
            int,
            pa.Check.isin(cs.VALID_ROUTE_TYPES),
            coerce=True,
        ),
        "speed": pa.Column(float, pa.Check.gt(0)),
        "geometry": pa.Column(
            checks=[
                pa.Check(lambda s: s.geom_type == "Polygon"),
                pa.Check(lambda s: s.is_valid),
                pa.Check(lambda s: ~s.is_empty),
            ]
        ),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict=True,
)

SCHEMA_EXCEL_AGENCY = pa.DataFrameSchema(
    {
        "agency_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "agency_url": pa.Column(str, pa.Check.str_matches(_URL)),
        "agency_timezone": pa.Column(str, pa.Check.isin(_TIMEZONES)),
        "start_date": pa.Column(str, pa.Check.str_matches(_DATE)),
        "end_date": pa.Column(str, pa.Check.str_matches(_DATE)),
        "agency_lang": pa.Column(str, required=False, nullable=True),
    },
    checks=pa.Check(
        lambda df: len(df) == 1,
        error="agency: expected 1 row",
    ),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_EXCEL_ROUTES = pa.DataFrameSchema(
    {
        "route_short_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "route_long_name": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "route_type": pa.Column(
            int,
            pa.Check.isin(cs.VALID_ROUTE_TYPES),
            required=False,
            coerce=True,
        ),
        "direction": pa.Column(
            int,
            pa.Check.isin([0, 1]),
        ),
        "schedule_type": pa.Column(
            str,
            pa.Check.isin([cs.SCHEDULE_HEADWAY, cs.SCHEDULE_FIXED]),
        ),
        "service_pattern": pa.Column(str, pa.Check.str_matches(_NONBLANK)),
        "start_time": pa.Column(str, pa.Check.str_matches(_TIME)),
        "end_time": pa.Column(str, required=False, nullable=True),
        "headway_mins": pa.Column(
            float,
            required=False,
            nullable=True,
            coerce=True,
        ),
        "travel_time_mins": pa.Column(
            float,
            pa.Check.gt(0),
            required=False,
            nullable=True,
            coerce=True,
        ),
        "speed": pa.Column(
            float,
            pa.Check.gt(0),
            required=False,
            nullable=True,
            coerce=True,
        ),
    },
    checks=pa.Check(
        lambda df: len(df) >= 1,
        error="routes: expected at least 1 row",
    ),
    index=pa.Index(int),
    strict="filter",
)

SCHEMA_EXCEL_HOLIDAYS = pa.DataFrameSchema(
    {
        "date": pa.Column(str, pa.Check.str_matches(_DATE)),
        "description": pa.Column(str, required=False, nullable=True),
    },
    checks=pa.Check(lambda df: len(df) >= 1),
    index=pa.Index(int),
    strict="filter",
)


def _validate_schema(
    schema: pa.DataFrameSchema,
    df: pd.DataFrame,
    label: str,
) -> None:
    """Run a Pandera schema and re-raise as ValueError."""
    try:
        schema.validate(df)
    except pa.errors.SchemaError as exc:
        raise ValueError(f"{label}: {exc}") from exc


def _check_id_subset(
    id_col: str,
    source: pd.DataFrame,
    source_name: str,
    target: pd.DataFrame,
    target_name: str,
) -> None:
    """Ensure every ID in source exists in target."""
    missing = set(source[id_col].unique()) - set(target[id_col].unique())
    if missing:
        raise ValueError(
            f"{id_col} in {source_name} not found in {target_name}: {missing}"
        )


def _check_date_range_order(agency_df: pd.DataFrame) -> None:
    """Ensure agency start and end dates are ordered."""
    start_date = str(agency_df["start_date"].iat[0])
    end_date = str(agency_df["end_date"].iat[0])
    if start_date > end_date:
        raise ValueError(
            "agency sheet has start_date after end_date: "
            f"{start_date} > {end_date}"
        )


def _check_headway_completeness(routes_df: pd.DataFrame) -> None:
    """Ensure headway rows have the fields they require."""
    headway = routes_df.loc[
        routes_df["schedule_type"].str.lower() == cs.SCHEDULE_HEADWAY
    ]
    if headway.empty:
        return

    if "end_time" not in headway.columns:
        raise ValueError("Headway schedule requires an 'end_time' column")

    bad_end = headway.loc[headway["end_time"].isna()]
    if not bad_end.empty:
        raise ValueError(
            "Headway rows missing 'end_time' at index: "
            f"{bad_end.index.tolist()}"
        )

    if "headway_mins" not in headway.columns:
        raise ValueError("Headway schedule requires a 'headway_mins' column")

    headway_mins = pd.to_numeric(
        headway["headway_mins"],
        errors="coerce",
    )
    bad_headway = headway.loc[headway_mins.isna() | headway_mins.le(0)]
    if not bad_headway.empty:
        raise ValueError(
            "Headway rows invalid 'headway_mins' at index: "
            f"{bad_headway.index.tolist()}"
        )


def _check_fixed_rows_have_travel_time(routes_df: pd.DataFrame) -> None:
    """Ensure fixed rows carry trip-level travel times."""
    fixed = routes_df.loc[
        routes_df["schedule_type"].str.lower() == cs.SCHEDULE_FIXED
    ]
    if fixed.empty:
        return

    if "travel_time_mins" not in fixed.columns:
        raise ValueError("Fixed schedule requires a 'travel_time_mins' column")

    travel_time = pd.to_numeric(
        fixed["travel_time_mins"],
        errors="coerce",
    )
    bad = fixed.loc[travel_time.isna() | travel_time.le(0)]
    if not bad.empty:
        raise ValueError(
            "Fixed rows invalid 'travel_time_mins' at index: "
            f"{bad.index.tolist()}"
        )


def _check_service_patterns(routes_df: pd.DataFrame) -> None:
    """Ensure all service patterns are parseable."""
    from .models import parse_service_pattern

    for idx, pattern in routes_df["service_pattern"].items():
        try:
            parse_service_pattern(str(pattern))
        except ValueError as exc:
            raise ValueError(f"Row {idx}: {exc}") from None


def _direction_pairs(
    df: pd.DataFrame,
    *,
    route_col: str = "route_short_name",
    direction_col: str = "direction",
    expand_both: bool,
) -> set[tuple[str, int]]:
    """Return route-direction coverage pairs."""
    pairs: set[tuple[str, int]] = set()

    for _, row in df.iterrows():
        route = str(row[route_col])
        direction = Direction.from_label(row[direction_col])

        if direction == Direction.BOTH:
            if not expand_both:
                raise ValueError(
                    "direction='both' is not allowed in the routes sheet. "
                    "Use one row for ida and one row for volta."
                )

            pairs.add((route, int(Direction.FORWARD)))
            pairs.add((route, int(Direction.REVERSE)))
            continue

        pairs.add((route, int(direction)))

    return pairs


def _check_route_direction_coverage(
    routes_df: pd.DataFrame,
    shapes_gdf: gpd.GeoDataFrame,
) -> None:
    """Ensure every route-table direction is covered by the routes geo file."""
    route_pairs = _direction_pairs(
        routes_df,
        direction_col="direction",
        expand_both=False,
    )
    shape_pairs = _direction_pairs(
        shapes_gdf,
        direction_col="direction",
        expand_both=True,
    )

    missing = route_pairs - shape_pairs
    if missing:
        formatted = [
            f"({route}, dir={direction})"
            for route, direction in missing
        ]
        raise ValueError(
            "Routes in Excel not covered by routes geo file: "
            f"{', '.join(sorted(formatted))}"
        )


def _check_routes_geo(shapes_gdf: gpd.GeoDataFrame) -> None:
    """Validate the routes geo layer required by the Excel reader."""
    for col in ("route_short_name", "direction"):
        if col not in shapes_gdf.columns:
            raise ValueError(f"Routes geo file must have a {col!r} column")

    bad_geom = shapes_gdf.loc[shapes_gdf.geometry.geom_type != "LineString"]
    if not bad_geom.empty:
        found = bad_geom.geometry.geom_type.unique().tolist()
        raise ValueError(
            "Routes geo file must contain only LineStrings. "
            f"Found: {found}"
        )

    bad_valid = shapes_gdf.loc[~shapes_gdf.geometry.is_valid]
    if not bad_valid.empty:
        raise ValueError("Routes geo file contains invalid geometries")

    bad_empty = shapes_gdf.loc[shapes_gdf.geometry.is_empty]
    if not bad_empty.empty:
        raise ValueError("Routes geo file contains empty geometries")


def _check_stops_geo(stops_gdf: gpd.GeoDataFrame) -> None:
    """Validate the optional stops geo layer."""
    for col in ("stop_id", "stop_name"):
        if col not in stops_gdf.columns:
            raise ValueError(f"Stops geo file must have a {col!r} column")

    bad_geom = stops_gdf.loc[stops_gdf.geometry.geom_type != "Point"]
    if not bad_geom.empty:
        raise ValueError("Stops geo file must contain only Point geometries")

    bad_valid = stops_gdf.loc[~stops_gdf.geometry.is_valid]
    if not bad_valid.empty:
        raise ValueError("Stops geo file contains invalid geometries")

    bad_empty = stops_gdf.loc[stops_gdf.geometry.is_empty]
    if not bad_empty.empty:
        raise ValueError("Stops geo file contains empty geometries")

    stop_id = stops_gdf["stop_id"].astype("string").str.strip()
    if stop_id.isna().any() or stop_id.eq("").any():
        raise ValueError("Stops geo file contains blank stop_id values")

    if stop_id.duplicated().any():
        duplicated = stop_id.loc[stop_id.duplicated()].unique().tolist()
        raise ValueError(
            "Stops geo file contains duplicated stop_id values: "
            f"{duplicated[:10]}"
        )

    stop_name = stops_gdf["stop_name"].astype("string").str.strip()
    if stop_name.isna().any() or stop_name.eq("").any():
        raise ValueError("Stops geo file contains blank stop_name values")


def _check_holidays_within_feed_range(
    agency_df: pd.DataFrame,
    holidays_df: pd.DataFrame | None,
) -> None:
    """Ensure holiday dates fall inside the declared feed window."""
    if holidays_df is None or holidays_df.empty:
        return

    start_date = str(agency_df["start_date"].iat[0])
    end_date = str(agency_df["end_date"].iat[0])

    bad = holidays_df.loc[
        (holidays_df["date"] < start_date)
        | (holidays_df["date"] > end_date)
    ]
    if not bad.empty:
        dates = bad["date"].tolist()[:10]
        raise ValueError(
            "Some holiday dates fall outside the feed date range: "
            f"{dates}"
        )


def validate_speed_zones_gdf(speed_zones_gdf: gpd.GeoDataFrame) -> None:
    """Validate a speed-zones GeoDataFrame."""
    _validate_schema(SCHEMA_SPEED_ZONES, speed_zones_gdf, "speed_zones")


def validate_tables(tables: dict[str, object]) -> None:
    """Validate classic directory-format inputs."""
    required = [
        ("meta", SCHEMA_META),
        ("service_profiles", SCHEMA_SERVICE_PROFILES),
        ("shapes", SCHEMA_SHAPES),
        ("frequencies", SCHEMA_FREQUENCIES),
    ]
    for name, schema in required:
        if tables.get(name) is None:
            raise ValueError(f"Required table {name!r} is missing")
        _validate_schema(schema, tables[name], name)

    if tables.get("stops") is not None:
        _validate_schema(SCHEMA_STOPS, tables["stops"], "stops")

    if tables.get("speed_zones") is not None:
        _validate_schema(
            SCHEMA_SPEED_ZONES,
            tables["speed_zones"],
            "speed_zones",
        )

    _check_id_subset(
        "shape_id",
        tables["frequencies"],
        "frequencies",
        tables["shapes"],
        "shapes",
    )
    _check_id_subset(
        "service_profile_id",
        tables["frequencies"],
        "frequencies",
        tables["service_profiles"],
        "service_profiles",
    )


def validate_excel_tables(
    agency_df: pd.DataFrame,
    routes_df: pd.DataFrame,
    shapes_gdf: gpd.GeoDataFrame,
    stops_gdf: gpd.GeoDataFrame | None = None,
    holidays_df: pd.DataFrame | None = None,
) -> None:
    """Validate Excel-format sheets and companion geo files."""
    _validate_schema(SCHEMA_EXCEL_AGENCY, agency_df, "agency sheet")
    _validate_schema(SCHEMA_EXCEL_ROUTES, routes_df, "routes sheet")
    _check_date_range_order(agency_df)
    _check_headway_completeness(routes_df)
    _check_fixed_rows_have_travel_time(routes_df)
    _check_service_patterns(routes_df)

    _check_routes_geo(shapes_gdf)
    _check_route_direction_coverage(routes_df, shapes_gdf)

    if stops_gdf is not None:
        _check_stops_geo(stops_gdf)

    if holidays_df is not None and not holidays_df.empty:
        _validate_schema(SCHEMA_EXCEL_HOLIDAYS, holidays_df, "holidays sheet")
        _check_holidays_within_feed_range(agency_df, holidays_df)