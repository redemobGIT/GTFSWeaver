"""I/O utilities to read source files and construct a validated ProtoFeed.

Entry points
------------
- ``read_geo_file(path, ...)`` — read one or more geospatial layers.
- ``read_protofeed(xlsx, routes_geo, ...)`` — Excel workbook
  plus companion geofiles.

Excel shape ID generation
-------------------------
The routes geofile should contain either:

- ``shape_id``; or
- ``route_short_name`` and ``direction``.

If ``shape_id`` is not present, internal shape IDs are generated as
``make_shape_ids(route_short_name, direction)``.

Future importers
----------------
Additional importers should build the same internal table dictionary and pass
it through ``_make_protofeed()``.
"""

from __future__ import annotations

import pathlib as pl
import warnings
from collections.abc import Mapping
from typing import Any, TypeAlias, TypedDict, cast

import geopandas as gpd
import pandas as pd
import shapely.geometry as sg

from . import constants as cs
from .models import (
    Direction,
    ProtoFeed,
    make_shape_id,
    make_shape_ids,
    make_route_id,
    make_service_profile_id,
    parse_service_pattern,
)
from .validators import (
    validate_excel_tables,
    validate_tables,
)

Layer: TypeAlias = str | int
LayerSelection: TypeAlias = Layer | list[Layer] | None
ColumnMap: TypeAlias = Mapping[str, str]

ROUTES_GEO_ID_COLUMNS = frozenset({"route_short_name", "direction"})
STOPS_GEO_REQUIRED_COLUMNS = frozenset({"stop_id", "stop_name"})


class ExcelWorkbook(TypedDict):
    agency: pd.DataFrame
    routes: pd.DataFrame
    holidays: pd.DataFrame | None


class CompanionGeoFiles(TypedDict):
    shapes: gpd.GeoDataFrame
    stops: gpd.GeoDataFrame | None
    speed_zones: gpd.GeoDataFrame | None


class ProtoFeedTables(TypedDict):
    meta: pd.DataFrame
    service_profiles: pd.DataFrame
    shapes: gpd.GeoDataFrame
    frequencies: pd.DataFrame
    stops: pd.DataFrame | None
    speed_zones: gpd.GeoDataFrame | None
    holidays: pd.DataFrame | None
    boundary: gpd.GeoDataFrame | None


# ── Public APIs (The Headlines) ──────────────────────────────────────────────


def read_protofeed(
    xlsx_path: str | pl.Path,
    routes_geo_path: str | pl.Path,
    stops_geo_path: str | pl.Path | None = None,
    speed_zones_path: str | pl.Path | None = None,
    boundary: gpd.GeoDataFrame | None = None,
    *,
    source_crs: int | str | None = None,
    routes_layer: LayerSelection = None,
    stops_layer: LayerSelection = None,
    speed_zones_layer: LayerSelection = None,
    routes_geo_column_map: ColumnMap | None = None,
    stops_geo_column_map: ColumnMap | None = None,
    speed_zones_column_map: ColumnMap | None = None,
) -> ProtoFeed:
    """Build a validated ProtoFeed from an Excel workbook and companion geofiles.

    Parameters
    ----------
    xlsx_path
        Excel workbook with agency and routes sheets.
    routes_geo_path
        Geospatial file containing route geometries.
    stops_geo_path
        Optional geospatial file containing stops.
    speed_zones_path
        Optional geospatial file containing speed zones.
    boundary
        Optional study-area boundary.
    source_crs
        CRS to assign to companion geofiles that have no CRS metadata.
    routes_layer, stops_layer, speed_zones_layer
        Optional layer selections for multi-layer geospatial files.
    routes_geo_column_map, stops_geo_column_map, speed_zones_column_map
        Optional mappings from source column names to expected column names,
        using the same convention as ``DataFrame.rename(columns=...)``.
    """
    workbook = _read_excel_workbook(xlsx_path)
    workbook["routes"] = _prepare_routes_data(workbook["routes"])

    geofiles = _read_companion_geo_files(
        routes_geo_path=routes_geo_path,
        stops_geo_path=stops_geo_path,
        speed_zones_path=speed_zones_path,
        source_crs=source_crs,
        routes_layer=routes_layer,
        stops_layer=stops_layer,
        speed_zones_layer=speed_zones_layer,
        routes_geo_column_map=routes_geo_column_map,
        stops_geo_column_map=stops_geo_column_map,
        speed_zones_column_map=speed_zones_column_map,
    )

    validate_excel_tables(
        agency_df=workbook["agency"],
        routes_df=workbook["routes"],
        shapes_gdf=geofiles["shapes"],
        stops_gdf=geofiles["stops"],
        holidays_df=workbook["holidays"],
    )

    tables = _build_protofeed_tables(
        agency_df=workbook["agency"],
        routes_df=workbook["routes"],
        shapes_gdf=geofiles["shapes"],
        stops_gdf=geofiles["stops"],
        speed_zones=geofiles["speed_zones"],
        holidays_df=workbook["holidays"],
        boundary=boundary,
    )

    return _make_protofeed(tables)


def read_geo_file(
    path: str | pl.Path,
    *,
    layer: LayerSelection = None,
    source_crs: int | str | None = None,
    target_crs: int | str | None = None,
    warn_multiple_layers: bool = True,
    source_layer_col: str | None = "source_file_layer",
    **read_file_kwargs: Any,
) -> gpd.GeoDataFrame:
    """Read one or more layers from a supported geospatial file.

    By default, ``layer=None`` follows GeoPandas/Pyogrio behavior and reads
    the default layer, usually the first one.

    Parameters
    ----------
    path
        Path to the geospatial file.
    layer
        Layer selection.

        - ``None``: read the default layer, usually the first.
        - ``str`` or ``int``: read one selected layer.
        - ``list[str | int]``: read selected layers and concatenate them.
        - ``"all"``: read and concatenate all spatial layers.

        If the file has a real layer named ``"all"``, pass ``layer=["all"]``.
    source_crs
        CRS to assign when the file has no CRS metadata. This does not
        transform coordinates; it only declares what CRS the coordinates are
        already in.
    target_crs
        CRS to reproject to after reading.
    warn_multiple_layers
        If True, warn when the file has multiple spatial layers and no explicit
        layer is selected.
    source_layer_col
        Column used to record the original source layer when several layers
        are concatenated. If None, no provenance column is added.
    **read_file_kwargs
        Extra keyword arguments passed to ``geopandas.read_file()``.
    """
    path = pl.Path(path)
    _validate_geo_extension(path)

    layers_to_concat = _layers_to_concat(path, layer)

    if layers_to_concat is None:
        if layer is None and warn_multiple_layers:
            _warn_if_multiple_spatial_layers(path)

        gdf = gpd.read_file(
            path,
            layer=layer,
            **read_file_kwargs,
        )
    else:
        gdf = _read_geo_layers(
            path,
            layers=layers_to_concat,
            source_layer_col=source_layer_col,
            **read_file_kwargs,
        )

    return _resolve_crs(
        gdf,
        path=path,
        source_crs=source_crs,
        target_crs=target_crs,
    )


# ── Feed Orchestration ───────────────────────────────────────────────────────


def _build_protofeed_tables(
    *,
    agency_df: pd.DataFrame,
    routes_df: pd.DataFrame,
    shapes_gdf: gpd.GeoDataFrame,
    stops_gdf: gpd.GeoDataFrame | None,
    speed_zones: gpd.GeoDataFrame | None,
    holidays_df: pd.DataFrame | None,
    boundary: gpd.GeoDataFrame | None,
) -> ProtoFeedTables:

    meta = agency_df[
        [
            "agency_name",
            "agency_url",
            "agency_timezone",
            "start_date",
            "end_date",
        ]
    ].copy()

    service_profiles = _excel_to_service_profiles(routes_df)
    frequencies = _excel_to_trip_blueprints(routes_df, service_profiles)

    shapes = _shape_table_from_gdf(shapes_gdf)
    stops = _stops_gdf_to_table(stops_gdf) if stops_gdf is not None else None

    return {
        "meta": meta,
        "service_profiles": service_profiles,
        "shapes": shapes,
        "frequencies": frequencies,
        "stops": stops,
        "speed_zones": speed_zones,
        "holidays": holidays_df,
        "boundary": boundary,
    }


def _make_protofeed(tables: ProtoFeedTables) -> ProtoFeed:
    validate_tables(_core_tables_for_validation(tables))
    return ProtoFeed(**tables)


def _core_tables_for_validation(tables: ProtoFeedTables) -> dict[str, object]:
    """Return the core table set expected by validate_tables()."""
    return {
        "meta": tables["meta"],
        "service_profiles": tables["service_profiles"],
        "shapes": tables["shapes"],
        "frequencies": tables["frequencies"],
        "stops": tables["stops"],
        "speed_zones": tables["speed_zones"],
    }


# ── Excel Data Extraction ────────────────────────────────────────────────────


def _read_excel_workbook(path: str | pl.Path) -> ExcelWorkbook:
    path = pl.Path(path)

    sheets = {
        name: _strip_object_columns(df)
        for name, df in pd.read_excel(path, sheet_name=None, dtype=str).items()
    }

    for sheet in (cs.EXCEL_SHEET_AGENCY, cs.EXCEL_SHEET_ROUTES):
        if sheet not in sheets:
            raise ValueError(f"Excel workbook must have a {sheet!r} sheet.")

    return {
        "agency": sheets[cs.EXCEL_SHEET_AGENCY],
        "routes": sheets[cs.EXCEL_SHEET_ROUTES],
        "holidays": sheets.get(cs.EXCEL_SHEET_HOLIDAYS),  # Returns None if missing
    }


def _prepare_routes_data(routes_df: pd.DataFrame) -> pd.DataFrame:
    """Prepare normalized route rows for ProtoFeed construction."""
    df = routes_df.copy()

    df = _infer_schedule_type(df)

    df["service_pattern"] = (
        df["service_pattern"]
        .astype("string")
        .str.upper()
    )

    df = make_shape_ids(df)

    df["service_profile_id"] = df.apply(
        _make_service_profile_id_from_row,
        axis="columns",
    )

    return df


def _make_service_profile_id_from_row(row: pd.Series) -> str:
    """Generate the deterministic service-profile ID for one route row."""
    return make_service_profile_id(
        schedule_type=row["schedule_type"],
        start_time=row["start_time"],
        end_time=row.get("end_time"),
        pattern=row["service_pattern"],
    )


def _excel_to_service_profiles(clean_routes: pd.DataFrame) -> pd.DataFrame:
    """Extract unique service profiles from prepared route rows."""
    profile_cols = [
        "service_profile_id",
        "schedule_type",
        "start_time",
        "end_time",
        "service_pattern",
    ]

    profiles = (
        clean_routes[profile_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    duplicated = profiles["service_profile_id"].duplicated(keep=False)
    if duplicated.any():
        collisions = profiles.loc[duplicated].sort_values(
            "service_profile_id"
        )
        raise ValueError(
            "service_profile_id collision across distinct profiles. "
            "Increase the hash length or inspect schedule fields:\n"
            f"{collisions.to_string(index=False)}"
        )

    parsed = profiles["service_pattern"].apply(parse_service_pattern)

    weekdays = pd.DataFrame(
        parsed.str[0].tolist(),
        columns=list(cs.WEEKDAYS),
    )

    profiles = pd.concat([profiles, weekdays], axis="columns")
    profiles["holiday"] = parsed.str[1].astype(int)

    return profiles[
        profile_cols
        + list(cs.WEEKDAYS)
        + ["holiday"]
    ]


def _excel_to_trip_blueprints(
    clean_routes: pd.DataFrame,
    service_profiles: pd.DataFrame,
) -> pd.DataFrame:
    """Convert prepared route rows into the master trip blueprint table."""
    out = clean_routes.copy()

    missing_profiles = (
        set(out["service_profile_id"])
        - set(service_profiles["service_profile_id"])
    )
    if missing_profiles:
        raise ValueError(
            "Could not match all routes to service profiles. "
            f"Missing profile IDs: {sorted(missing_profiles)}"
        )

    out["frequency"] = _calculate_trip_frequencies(out)
    out["route_id"] = out["route_short_name"].apply(make_route_id)

    if "route_type" in out.columns:
        out["route_type"] = pd.to_numeric(
            out["route_type"],
            errors="raise",
        ).astype(int)

    blueprint_cols = [
        "route_id",
        "route_short_name",
        "route_long_name",
        "route_type",
        "service_profile_id",
        "shape_id",
        "direction",
        "start_time",
        "end_time",
        "schedule_type",
        "frequency",
        "headway_mins",
        "travel_time_mins",
        "speed",
    ]

    return out[blueprint_cols]


def _calculate_trip_frequencies(df: pd.DataFrame) -> pd.Series:
    """
    Determine the exact number of trips generated by each schedule rule.

    - Fixed departures represent exactly 1 physical trip.
    - Headways generate (60 / headway_mins) trips per hour.
    """
    # Initialize all rules as single, fixed trips
    frequencies = pd.Series(1, index=df.index)

    # Overwrite the calculation specifically for headway schedules
    is_headway = df["schedule_type"].eq(cs.SCHEDULE_HEADWAY)
    if is_headway.any():
        headways = pd.to_numeric(df.loc[is_headway, "headway_mins"])
        frequencies.loc[is_headway] = (60.0 / headways).round()

    return frequencies.astype(int)


def _infer_schedule_type(df: pd.DataFrame) -> pd.DataFrame:
    """
    Infer schedule_type from timing fields.

    Rules:
    - headway: end_time and headway_mins are both present
    - fixed: headway_mins is missing
    - invalid: headway_mins is present but end_time is missing

    Fixed rows may have no end_time.
    """
    out = df.copy()

    if "end_time" not in out.columns:
        out["end_time"] = pd.NA
    if "headway_mins" not in out.columns:
        out["headway_mins"] = pd.NA

    has_end = out["end_time"].notna()
    has_headway = out["headway_mins"].notna()

    invalid = has_headway & ~has_end
    if invalid.any():
        raise ValueError(
            "Rows with 'headway_mins' must also have 'end_time'. "
            f"Invalid row indices: {invalid[invalid].index.tolist()}"
        )

    out["schedule_type"] = cs.SCHEDULE_FIXED
    out.loc[has_end & has_headway, "schedule_type"] = cs.SCHEDULE_HEADWAY

    return out


# ── Geospatial Data Extraction ───────────────────────────────────────────────


def _read_companion_geo_files(
    *,
    routes_geo_path: str | pl.Path,
    stops_geo_path: str | pl.Path | None,
    speed_zones_path: str | pl.Path | None,
    source_crs: int | str | None,
    routes_layer: LayerSelection,
    stops_layer: LayerSelection,
    speed_zones_layer: LayerSelection,
    routes_geo_column_map: ColumnMap | None,
    stops_geo_column_map: ColumnMap | None,
    speed_zones_column_map: ColumnMap | None,
) -> CompanionGeoFiles:

    shapes = _load_and_stage_geo(
        routes_geo_path,
        routes_layer,
        routes_geo_column_map,
        "Routes geofile",
        source_crs,
    )
    stops = _load_and_stage_geo(
        stops_geo_path,
        stops_layer,
        stops_geo_column_map,
        "Stops geofile",
        source_crs,
    )
    speed_zones = _load_and_stage_geo(
        speed_zones_path,
        speed_zones_layer,
        speed_zones_column_map,
        "Speed zones geofile",
        source_crs,
    )

    # Enforce structural requirement specific to shapes
    # (Note: shapes cannot be None because routes_geo_path is a required arg)
    if "direction" not in shapes.columns:
        raise ValueError(
            "Routes geofile must have a 'direction' column, either "
            "originally or after applying the column map."
        )

    return {
        "shapes": shapes,
        "stops": stops,
        "speed_zones": speed_zones,
    }


def _load_and_stage_geo(
    path: str | pl.Path | None,
    layer: LayerSelection,
    column_map: ColumnMap | None,
    table_name: str,
    source_crs: int | str | None,
) -> gpd.GeoDataFrame | None:
    """Read, map columns, and sanitize a geofile if the path is provided."""
    if path is None:
        return None

    gdf = read_geo_file(
        path,
        layer=layer,
        source_crs=source_crs,
        target_crs=cs.WGS84,
    )

    renamed_df = _apply_column_map(gdf, column_map, table_name=table_name)

    return _strip_object_columns(renamed_df)


def _read_geo_layers(
    path: pl.Path,
    *,
    layers: list[Layer],
    source_layer_col: str | None,
    **read_file_kwargs: Any,
) -> gpd.GeoDataFrame:
    frames: list[gpd.GeoDataFrame] = []

    for selected_layer in layers:
        gdf = gpd.read_file(
            path,
            layer=selected_layer,
            **read_file_kwargs,
        )

        if source_layer_col is not None:
            if source_layer_col in gdf.columns:
                raise ValueError(
                    f"Cannot add source layer column {source_layer_col!r} "
                    f"because it already exists in layer {selected_layer!r}. "
                    "Pass source_layer_col=None or choose another column name."
                )

            gdf = gdf.assign(**{source_layer_col: selected_layer})

        frames.append(gdf)

    return _concat_geo_layers(frames)


def _concat_geo_layers(frames: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    geometry_col = frames[0].geometry.name

    mismatched_geometry_cols = {
        frame.geometry.name for frame in frames if frame.geometry.name != geometry_col
    }

    if mismatched_geometry_cols:
        warnings.warn(
            "Selected layers use different geometry column names. "
            f"Renaming them to {geometry_col!r} before concatenation.",
            UserWarning,
            stacklevel=3,
        )

    normalised_frames = [
        (
            frame
            if frame.geometry.name == geometry_col
            else frame.rename_geometry(geometry_col)
        )
        for frame in frames
    ]

    return cast(
        gpd.GeoDataFrame,
        pd.concat(normalised_frames, ignore_index=True),
    )


def _resolve_crs(
    gdf: gpd.GeoDataFrame,
    *,
    path: pl.Path,
    source_crs: int | str | None,
    target_crs: int | str | None,
) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        if source_crs is None and target_crs is not None:
            raise ValueError(
                f"{path} has no CRS metadata, so it cannot be reprojected to "
                f"{target_crs!r}. Pass source_crs=... if you know the input CRS."
            )

        if source_crs is not None:
            warnings.warn(
                f"{path} has no CRS metadata. Assigning " f"source_crs={source_crs!r}.",
                UserWarning,
                stacklevel=3,
            )
            gdf = gdf.set_crs(source_crs)

    if target_crs is not None:
        gdf = gdf.to_crs(target_crs)

    return gdf


def _layers_to_concat(
    path: pl.Path,
    layer: LayerSelection,
) -> list[Layer] | None:
    """Return layers to concatenate, or None for single-layer reads."""
    if layer == "all":
        return _list_spatial_layers(path)

    if isinstance(layer, list):
        if not layer:
            raise ValueError("layer list cannot be empty.")
        return layer

    return None


def _list_spatial_layers(path: pl.Path) -> list[str]:
    layers = gpd.list_layers(path)

    if "geometry_type" in layers.columns:
        layers = layers[layers["geometry_type"].notna()]

    layer_names = layers["name"].astype(str).tolist()

    if not layer_names:
        raise ValueError(f"No spatial layers found in {path}.")

    return layer_names


def _warn_if_multiple_spatial_layers(path: pl.Path) -> None:
    try:
        layers = _list_spatial_layers(path)
    except Exception:
        return

    if len(layers) <= 1:
        return

    layer_names = "\n".join(f"  - {layer!r}" for layer in layers)

    warnings.warn(
        f"{path} has multiple spatial layers:\n"
        f"{layer_names}\n"
        "Reading the default layer, usually the first one.\n"
        "Pass layer=... to choose one layer, layer=[...] to read selected "
        "layers, or layer='all' to read all spatial layers.",
        UserWarning,
        stacklevel=3,
    )


def _validate_geo_extension(path: pl.Path) -> None:
    suffix = path.suffix.lower()

    if suffix not in cs.GEO_EXTENSIONS:
        supported = ", ".join(sorted(cs.GEO_EXTENSIONS))
        raise ValueError(
            f"Unsupported geospatial format {suffix!r}. "
            f"Supported extensions are: {supported}."
        )


# ── Formatting & Validation Utilities ────────────────────────────────────────


def _shape_table_from_gdf(shapes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Convert route geometries into final directional shape rows.

    A source geometry with direction='both' is expanded into:
    - direction 0: original geometry
    - direction 1: reversed geometry
    """
    rows: list[pd.Series] = []

    for _, row in shapes_gdf.iterrows():
        direction = Direction.from_label(row["direction"])

        if direction == Direction.BOTH:
            for expanded_direction, geometry in (
                (Direction.FORWARD, row.geometry),
                (Direction.REVERSE, _reverse_linestring(row.geometry)),
            ):
                out = row.copy()
                out["direction"] = int(expanded_direction)
                out["shape_id"] = make_shape_id(
                    row["route_short_name"],
                    int(expanded_direction),
                )
                out["geometry"] = geometry
                rows.append(out)
        else:
            out = row.copy()
            out["direction"] = int(direction)
            out["shape_id"] = make_shape_id(
                row["route_short_name"],
                int(direction),
            )
            rows.append(out)

    result = gpd.GeoDataFrame(rows, geometry="geometry", crs=shapes_gdf.crs)
    result = result[["shape_id", "geometry"]].reset_index(drop=True)

    duplicated = result["shape_id"].duplicated(keep=False)
    if duplicated.any():
        duplicates = result.loc[duplicated, "shape_id"].unique().tolist()
        raise ValueError(
            "Routes geo file produces duplicated shape_id values: "
            f"{duplicates[:10]}"
        )

    return result


def _reverse_linestring(line: sg.LineString) -> sg.LineString:
    """Return a reversed LineString."""
    if not isinstance(line, sg.LineString):
        raise TypeError(
            "Routes geo file must contain only LineString geometries."
        )

    return sg.LineString(list(line.coords)[::-1])


def _stops_gdf_to_table(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Convert a WGS84 stop GeoDataFrame into a GTFS-style stops table."""

    # TODO: COnsider STOP_COLUMNS as a canonical list of expected columns,
    # and put them on constants.py, as well as the other lists in here
    STOP_COLUMNS = [
        "stop_id",
        "stop_name",
        "stop_code",
        "stop_desc",
        "zone_id",
        "location_type",
        "parent_station",
        "stop_timezone",
        "wheelchair_boarding",
    ]
    existing_cols = [c for c in STOP_COLUMNS if c in gdf.columns]

    return gdf[existing_cols].assign(
        stop_lat=gdf.geometry.y,
        stop_lon=gdf.geometry.x,
    )


def _apply_column_map(
    df: pd.DataFrame,
    column_map: ColumnMap | None,
    *,
    table_name: str,
) -> pd.DataFrame:
    """Rename source columns to expected columns using an explicit map."""
    if not column_map:
        return df

    missing_sources = set(column_map).difference(df.columns)

    if missing_sources:
        missing = ", ".join(sorted(missing_sources))
        raise ValueError(
            f"{table_name} column map references missing source columns: " f"{missing}."
        )

    renamed = df.rename(columns=dict(column_map))

    if renamed.columns.duplicated().any():
        duplicated = renamed.columns[renamed.columns.duplicated()].tolist()
        duplicated_text = ", ".join(repr(col) for col in duplicated)
        raise ValueError(
            f"{table_name} has duplicated columns after applying column map: "
            f"{duplicated_text}."
        )

    return renamed


def _strip_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim whitespace and convert common empty-string markers to NA."""
    df = df.copy()

    object_cols = df.select_dtypes(include=["object", "string"]).columns

    if not object_cols.empty:
        na_markers = ["", "None", "none", "NaN", "nan", "<NA>"]
        for col in object_cols:
            df[col] = df[col].astype("string").str.strip()

        df[object_cols] = df[object_cols].replace(na_markers, pd.NA)

    return df