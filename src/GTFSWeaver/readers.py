"""
I/O: read source files and construct a validated ProtoFeed.

Entry points
------------
- ``read_protofeed(directory)`` — classic multi-file format
- ``read_protofeed_from_excel(xlsx, routes_geo, ...)`` — Excel + geo

Shape ID generation (Excel path)
---------------------------------
The geo file has ``route_short_name`` and ``direction`` per feature.
The Excel has ``route_short_name`` and ``direction_id`` per row.

``shape_id`` is auto-generated on both sides as::

    make_shape_id(route_short_name, direction) → "328-1"

This ensures consistent matching without the user maintaining a
separate shape_id column.
"""

from __future__ import annotations

import pathlib as pl
import uuid

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd

from . import constants as cs
from .models import Direction, ProtoFeed, make_route_id, make_shape_id, parse_service_pattern
from .validators import validate_excel_tables, validate_tables


# ── Multi-format geo reading ─────────────────────────────────────────

def read_geo_file(
    path: str | pl.Path,
    target_crs: int | str | None = None,
) -> gpd.GeoDataFrame:
    """Read GeoJSON, GeoPackage, Shapefile, or KML/KMZ."""
    path = pl.Path(path)
    suffix = path.suffix.lower()
    if suffix not in cs.GEO_EXTENSIONS:
        raise ValueError(
            f"Unsupported geo format '{suffix}'. "
            f"Supported: {sorted(cs.GEO_EXTENSIONS)}"
        )

    if suffix in (".kml", ".kmz"):
        fiona.drvsupport.supported_drivers["KML"] = "rw"
        layers = fiona.listlayers(str(path))
        frames = [gpd.read_file(str(path), layer=lyr) for lyr in layers]
        gdf = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
        )
        if gdf.crs is None:
            gdf = gdf.set_crs(cs.WGS84)
    else:
        gdf = gpd.read_file(str(path))

    if target_crs and gdf.crs is not None:
        gdf = gdf.to_crs(target_crs)
    return gdf


def _normalise_shapes_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Ensure shapes GeoDataFrame has a ``shape_id`` column.

    - If ``shape_id`` already exists, use it (backward compat).
    - If ``route_short_name`` + ``direction`` exist, auto-generate
      using ``make_shape_id()``.
    - Otherwise try common alternatives (Name, name, id).
    """
    if "shape_id" in gdf.columns:
        gdf["shape_id"] = gdf["shape_id"].astype(str)
        return gdf

    if "route_short_name" in gdf.columns and "direction" in gdf.columns:
        gdf["shape_id"] = gdf.apply(
            lambda r: make_shape_id(r["route_short_name"], r["direction"]),
            axis=1,
        )
        return gdf

    for alt in ("Name", "name", "id", "ID"):
        if alt in gdf.columns:
            return gdf.rename(columns={alt: "shape_id"}).assign(
                shape_id=lambda df: df["shape_id"].astype(str)
            )

    raise ValueError(
        "Routes geo file must have 'shape_id', or "
        "'route_short_name' + 'direction' columns."
    )


def _normalise_stops_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ensure stops GeoDataFrame has ``stop_id`` and ``stop_name``."""
    rename = {
        "name": "stop_name", "Name": "stop_name",
        "description": "stop_desc", "Description": "stop_desc",
        "id": "stop_id", "ID": "stop_id",
    }
    gdf = gdf.rename(columns={k: v for k, v in rename.items() if k in gdf.columns})
    if "stop_id" not in gdf.columns:
        gdf["stop_id"] = [str(uuid.uuid4())[:8] for _ in range(len(gdf))]
    if "stop_name" not in gdf.columns:
        gdf["stop_name"] = "stop " + gdf["stop_id"]
    return gdf


def _stops_gdf_to_table(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    wgs = gdf.to_crs(cs.WGS84)
    return wgs.assign(
        stop_lat=wgs.geometry.y,
        stop_lon=wgs.geometry.x,
    ).drop(columns="geometry", errors="ignore")


# ── Classic directory reader ─────────────────────────────────────────

def _read_raw_tables(path: pl.Path) -> dict:
    tables: dict = {}
    tables["meta"] = pd.read_csv(
        path / "meta.csv", dtype={"start_date": str, "end_date": str},
    )
    tables["service_windows"] = pd.read_csv(path / "service_windows.csv")
    tables["shapes"] = gpd.read_file(path / "shapes.geojson")
    tables["frequencies"] = pd.read_csv(
        path / "frequencies.csv",
        dtype={"route_short_name": str, "service_window_id": str,
               "shape_id": str, "direction": int, "frequency": int},
    )
    stops_path = path / "stops.csv"
    tables["stops"] = (
        pd.read_csv(stops_path, dtype={"stop_id": str})
        if stops_path.exists() else None
    )
    zones_path = path / "speed_zones.geojson"
    if zones_path.exists():
        gz = gpd.read_file(zones_path)
        gz["route_type"] = gz["route_type"].astype(int)
        gz["speed_zone_id"] = gz["speed_zone_id"].astype(str)
        tables["speed_zones"] = gz
    else:
        tables["speed_zones"] = None
    return tables


def read_protofeed(path: str | pl.Path) -> ProtoFeed:
    """Read from a directory of CSV/GeoJSON files."""
    path = pl.Path(path)
    tables = _read_raw_tables(path)
    validate_tables(tables)
    return ProtoFeed(**tables)


# ── Excel transforms ─────────────────────────────────────────────────

def _excel_to_service_windows(routes_df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract unique service windows from routes sheet.

    ``service_window_id`` uses single underscores (no ``cs.SEP``)
    so it's safe inside compound TripKey IDs.
    """
    df = routes_df.copy()
    df["end_time"] = df["end_time"].fillna(df["start_time"])

    key_cols = ["start_time", "end_time", "service_pattern"]
    windows = df[key_cols].drop_duplicates().reset_index(drop=True)

    windows["_sp"] = windows["service_pattern"].str.strip().str.upper()
    parsed = windows["service_pattern"].apply(
        lambda p: parse_service_pattern(str(p))
    )
    for i, day in enumerate(cs.WEEKDAYS):
        windows[day] = parsed.apply(lambda t: t[0][i])
    windows["holiday"] = parsed.apply(lambda t: int(t[1]))

    windows["service_window_id"] = windows.apply(
        lambda r: f"sw_{r['start_time']}_{r['end_time']}_{r['_sp']}",
        axis=1,
    )

    return windows[
        ["service_window_id", "start_time", "end_time", "service_pattern"]
        + list(cs.WEEKDAYS) + ["holiday"]
    ]


def _excel_to_frequencies(
    routes_df: pd.DataFrame,
    service_windows: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert routes sheet → internal frequencies format.

    - Auto-generates ``shape_id`` via ``make_shape_id``
    - Converts ``headway_mins`` → ``frequency`` (preserving headway_mins)
    - Normalises ``direction_id`` → integer ``direction``
    """
    df = routes_df.copy()
    df["end_time"] = df["end_time"].fillna(df["start_time"])

    # Auto-generate shape_id from route + direction
    df["shape_id"] = df.apply(
        lambda r: make_shape_id(r["route_short_name"], r["direction_id"]),
        axis=1,
    )

    # Merge service_window_id via natural key
    df["_sp"] = df["service_pattern"].str.strip().str.upper()
    sw = service_windows.copy()
    sw["_sp"] = sw["service_pattern"].str.strip().str.upper()
    merged = df.merge(
        sw[["service_window_id", "start_time", "end_time", "_sp"]],
        on=["start_time", "end_time", "_sp"],
        how="left",
    ).drop(columns="_sp")

    # Schedule type
    merged["schedule_type"] = (
        merged["schedule_type"].astype(str).str.strip().str.lower()
        .fillna(cs.SCHEDULE_HEADWAY)
    )

    # Frequency: headway → trips/hour, fixed → 1
    is_hw = merged["schedule_type"] == cs.SCHEDULE_HEADWAY
    merged["frequency"] = np.where(
        is_hw,
        (60.0 / merged["headway_mins"].fillna(60)).round().astype(int),
        1,
    )
    merged["frequency"] = merged["frequency"].clip(lower=1)

    # Direction → integer
    merged["direction"] = (
        merged["direction_id"].map(Direction.from_label).astype(int)
    )

    # Route type default
    if "route_type" not in merged.columns:
        merged["route_type"] = 3
    merged["route_type"] = merged["route_type"].fillna(3).astype(int)

    # Route ID (sanitised for compound IDs)
    merged["route_id"] = merged["route_short_name"].map(make_route_id)

    out = [
        "route_id", "route_short_name", "route_long_name", "route_type",
        "service_window_id", "shape_id", "direction", "frequency",
        "schedule_type",
    ]
    for optional in ("speed", "travel_time_mins", "headway_mins"):
        if optional in merged.columns:
            out.append(optional)

    return merged[out].reset_index(drop=True)


# ── Excel reader ─────────────────────────────────────────────────────

def read_protofeed_from_excel(
    xlsx_path: str | pl.Path,
    routes_geo_path: str | pl.Path,
    stops_geo_path: str | pl.Path | None = None,
    speed_zones_path: str | pl.Path | None = None,
    boundary: gpd.GeoDataFrame | None = None,
) -> ProtoFeed:
    """
    Build a validated ProtoFeed from an Excel workbook + geo files.

    Excel sheets
    -------------
    **"agency"** (1 row):
        agency_name, agency_url, agency_timezone,
        start_date (YYYYMMDD), end_date (YYYYMMDD).

    **"routes"** (1 row per route × direction × service window):

    ================== =============== ======================================
    Column             Required        Notes
    ================== =============== ======================================
    route_short_name   yes             e.g. "328"
    route_long_name    yes             e.g. "São Cristóvão / Peró"
    route_type         no              GTFS type (default 3 = bus)
    direction_id       yes             "ida"/"volta"/"both" or 0/1/2
    schedule_type      yes             "headway" or "fixed"
    service_pattern    yes             DU / SAB / DOM / FER / custom
    start_time         yes             HH:MM:SS
    end_time           headway only    HH:MM:SS
    headway_mins       headway only    minutes between vehicles
    travel_time_mins   no              one-way trip duration (minutes)
    speed              no              average speed (km/h)
    ================== =============== ======================================

    **"holidays"** (optional): date (YYYYMMDD), description.

    Geo files (GeoJSON / GeoPackage / Shapefile / KML)
    ---------------------------------------------------
    Routes: LineStrings with ``route_short_name`` + ``direction``.
    Stops (optional): Points with ``stop_name``.
    """
    xlsx_path = pl.Path(xlsx_path)
    sheets = pd.read_excel(xlsx_path, sheet_name=None, dtype=str)

    for required in (cs.EXCEL_SHEET_AGENCY, cs.EXCEL_SHEET_ROUTES):
        if required not in sheets:
            raise ValueError(f"Excel must have a '{required}' sheet")

    agency_df = sheets[cs.EXCEL_SHEET_AGENCY]
    routes_df = sheets[cs.EXCEL_SHEET_ROUTES]

    # Coerce numeric columns
    for col in ("headway_mins", "speed", "travel_time_mins", "route_type"):
        if col in routes_df.columns:
            routes_df[col] = pd.to_numeric(routes_df[col], errors="coerce")
    if "route_type" not in routes_df.columns:
        routes_df["route_type"] = 3
    routes_df["route_type"] = routes_df["route_type"].fillna(3).astype(int)

    holidays_df = sheets.get(cs.EXCEL_SHEET_HOLIDAYS)

    # Geo files
    shapes_gdf = _normalise_shapes_gdf(read_geo_file(routes_geo_path))
    stops_gdf = (
        _normalise_stops_gdf(read_geo_file(stops_geo_path))
        if stops_geo_path else None
    )

    # Validate raw inputs
    validate_excel_tables(agency_df, routes_df, shapes_gdf, stops_gdf, holidays_df)

    # Transform
    meta = agency_df[[
        "agency_name", "agency_url", "agency_timezone",
        "start_date", "end_date",
    ]].copy()

    service_windows = _excel_to_service_windows(routes_df)
    frequencies = _excel_to_frequencies(routes_df, service_windows)

    shapes = (
        shapes_gdf[["shape_id", "geometry"]]
        .drop_duplicates("shape_id")
        .pipe(lambda df: df.to_crs(cs.WGS84) if df.crs is not None else df)
    )

    stops_table = _stops_gdf_to_table(stops_gdf) if stops_gdf is not None else None
    speed_zones = read_geo_file(speed_zones_path) if speed_zones_path else None

    return ProtoFeed(
        meta=meta,
        service_windows=service_windows,
        shapes=gpd.GeoDataFrame(shapes, crs=cs.WGS84),
        frequencies=frequencies,
        stops=stops_table,
        speed_zones=speed_zones,
        holidays=holidays_df,
        boundary=boundary,
    )