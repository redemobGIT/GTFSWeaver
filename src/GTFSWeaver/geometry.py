"""Spatial utilities for GTFS generation.

This module covers four tasks:

1. Stop point synthesis along shapes.
2. Optional H3-based stop clustering.
3. Stop selection and projection onto shapes.
4. Speed-zone intersection for zone-based stop_times.
"""

from __future__ import annotations

import math

import geopandas as gpd
import gtfs_kit as gk
import numpy as np
import pandas as pd
import shapely.geometry as sg
import shapely.ops as so

from . import constants as cs


def make_stop_points(
    lines: gpd.GeoDataFrame,
    id_col: str,
    offset: float,
    side: str,
    num_stops: int = 2,
    spacing: float | None = None,
) -> gpd.GeoDataFrame:
    """Generate equally spaced stop points along each line.

    Parameters
    ----------
    lines
        Line GeoDataFrame in a projected CRS.
    id_col
        Column used to identify the parent line.
    offset
        Lateral stop offset in line CRS units.
    side
        Offset side, usually ``"left"`` or ``"right"``.
    num_stops
        Minimum number of stops per line when ``spacing`` is not used.
    spacing
        Optional spacing between stops in line CRS units.

    Returns
    -------
    GeoDataFrame
        With columns ``point_id``, ``id_col``, ``shape_dist_traveled``,
        and ``geometry``.
    """
    if lines.empty:
        return gpd.GeoDataFrame(
            columns=["point_id", id_col, "shape_dist_traveled", "geometry"],
            geometry="geometry",
            crs=lines.crs,
        )

    num_stops = max(int(num_stops), 2)
    frames: list[gpd.GeoDataFrame] = []

    for row in lines.itertuples(index=False):
        line = row.geometry
        line_id = getattr(row, id_col)

        if line is None or line.is_empty:
            continue

        distances = _sample_distances(
            length=line.length,
            num_stops=num_stops,
            spacing=spacing,
        )
        points = _interpolate_with_offset(
            line=line,
            distances=distances,
            offset=offset,
            side=side,
        )

        suffixes = gk.make_ids(len(points), prefix="")
        frame = gpd.GeoDataFrame(
            {
                "point_id": [f"{line_id}_{suffix}" for suffix in suffixes],
                id_col: line_id,
                "shape_dist_traveled": distances,
            },
            geometry=gpd.points_from_xy(
                x=[point[0] for point in points],
                y=[point[1] for point in points],
                crs=lines.crs,
            ),
            crs=lines.crs,
        ).drop_duplicates("geometry")

        frames.append(frame)

    if not frames:
        return gpd.GeoDataFrame(
            columns=["point_id", id_col, "shape_dist_traveled", "geometry"],
            geometry="geometry",
            crs=lines.crs,
        )

    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=lines.crs,
    )


def _sample_distances(
    length: float,
    num_stops: int,
    spacing: float | None,
) -> list[float]:
    """Return monotone distances along a line.

    The endpoint is always included.
    """
    if length <= 0:
        return [0.0]

    if spacing is not None:
        if spacing <= 0:
            raise ValueError("'spacing' must be positive when provided")
        step = float(spacing)
    else:
        step = length / (num_stops - 1)

    distances = [step * i for i in range(math.floor(length / step))]
    if not distances or distances[-1] != length:
        distances.append(length)

    return distances


def _interpolate_with_offset(
    line: sg.LineString,
    distances: list[float],
    offset: float,
    side: str,
) -> list[tuple[float, float]]:
    """Interpolate points on a line and offset them laterally."""
    points_on_line = [line.interpolate(distance) for distance in distances]

    if offset <= 0 or side not in {"left", "right"}:
        return [point.coords[0] for point in points_on_line]

    guide = _offset_guide(line, side=side)
    if guide is None:
        return [point.coords[0] for point in points_on_line]

    result: list[tuple[float, float]] = []

    for point_on in points_on_line:
        point_guide = guide.interpolate(guide.project(point_on))
        vector = np.array(point_guide.coords[0]) - np.array(point_on.coords[0])
        norm = np.linalg.norm(vector)

        if norm == 0:
            result.append(point_on.coords[0])
            continue

        shifted = np.array(point_on.coords[0]) + offset * (vector / norm)
        result.append((float(shifted[0]), float(shifted[1])))

    return result


def _offset_guide(
    line: sg.LineString,
    side: str,
) -> sg.LineString | None:
    """Return a small parallel guide line for local offset direction."""
    try:
        guide = line.parallel_offset(0.1, side, join_style=2)
    except Exception:
        return None

    if guide is None or guide.is_empty:
        return None

    return _coerce_linestring(guide)


def _coerce_linestring(
    geometry: sg.base.BaseGeometry,
) -> sg.LineString | None:
    """Return a usable LineString from line-like geometries."""
    if isinstance(geometry, sg.LineString):
        return geometry

    if isinstance(geometry, sg.MultiLineString):
        merged = so.linemerge(geometry)

        if isinstance(merged, sg.LineString):
            return merged

        if isinstance(merged, sg.MultiLineString) and len(merged.geoms) > 0:
            return max(merged.geoms, key=lambda geom: geom.length)

    return None


def cluster_stops_h3(
    stops: gpd.GeoDataFrame,
    resolution: int = cs.DEFAULT_H3_RESOLUTION,
) -> gpd.GeoDataFrame:
    """Deduplicate nearby stops using H3 clustering.

    Within each cell, the stop closest to the H3 cell centroid is kept.
    """
    if stops.empty:
        return stops.copy()

    try:
        import h3
    except ImportError as exc:
        raise ImportError(
            "H3 clustering requires the 'h3' package. "
            "Install it with: pip install h3"
        ) from exc

    original_crs = stops.crs
    stops_wgs84 = stops.to_crs(cs.WGS84).copy()
    stops_wgs84["_h3"] = stops_wgs84.geometry.apply(
        lambda point: h3.latlng_to_cell(point.y, point.x, resolution)
    )

    def pick_stop(group: gpd.GeoDataFrame) -> pd.Series:
        if len(group) == 1:
            return group.iloc[0]

        cell = group["_h3"].iat[0]
        lat, lon = h3.cell_to_latlng(cell)
        centroid = sg.Point(lon, lat)

        distances = group.geometry.distance(centroid)
        return group.loc[distances.idxmin()]

    clustered = (
        stops_wgs84.groupby("_h3", group_keys=False)
        .apply(pick_stop)
        .drop(columns="_h3")
        .reset_index(drop=True)
    )

    result = gpd.GeoDataFrame(clustered, geometry="geometry", crs=cs.WGS84)

    if original_crs is not None:
        return result.to_crs(original_crs)

    return result


def buffer_side(
    linestring: sg.LineString,
    side: str,
    buffer: float,
) -> sg.Polygon:
    """Return a full or one-sided buffer around a line."""
    if buffer <= 0:
        return linestring.buffer(0)

    if side not in {"left", "right"}:
        return linestring.buffer(buffer, cap_style=2)

    signed = buffer if side == "left" else -buffer

    try:
        area = linestring.buffer(
            signed,
            single_sided=True,
            cap_style=2,
        )
    except TypeError:
        area = linestring.buffer(signed, single_sided=True)

    if area.is_empty:
        return linestring.buffer(buffer, cap_style=2)

    return area


def get_stops_nearby(
    stops: gpd.GeoDataFrame,
    linestring: sg.LineString,
    side: str,
    buffer: float = cs.BUFFER,
) -> gpd.GeoDataFrame:
    """Return stops within a buffered area around a line."""
    if stops.empty:
        return stops.copy()

    area = buffer_side(linestring, side, buffer)
    return stops.loc[stops.intersects(area)].copy()


def _coalesce_projected_stops(
    projected: gpd.GeoDataFrame,
    distance_tolerance: float = 5.0,  # TODO: consider this
) -> gpd.GeoDataFrame:
    """
    Collapse stops whose projected positions along the shape are effectively
    the same.

    Parameters
    ----------
    projected
        GeoDataFrame already containing:
        - stop_id
        - geometry
        - shape_dist_traveled
        - _dist_to_line
    distance_tolerance
        Maximum allowed separation, in the projected CRS units, for two
        projected stops to be treated as the same position along the shape.

    Notes
    -----
    This is intentionally conservative. It does not alter the shape. It only
    picks one representative stop for near-identical projections.
    """
    if projected.empty or distance_tolerance <= 0:
        return projected.copy()

    proj = projected.sort_values(
        ["shape_dist_traveled", "_dist_to_line", "stop_id"],
        ignore_index=True,
    ).copy()

    keep_idx: list[int] = []
    cluster_start = 0
    cluster_members = [0]

    def _flush_cluster(member_idx: list[int]) -> None:
        cluster = proj.iloc[member_idx]
        best_idx = cluster.sort_values(
            ["_dist_to_line", "shape_dist_traveled", "stop_id"],
            ignore_index=False,
        ).index[0]
        keep_idx.append(best_idx)

    for i in range(1, len(proj)):
        d0 = float(proj.iloc[cluster_start]["shape_dist_traveled"])
        di = float(proj.iloc[i]["shape_dist_traveled"])

        if (di - d0) <= distance_tolerance:
            cluster_members.append(i)
        else:
            _flush_cluster(cluster_members)
            cluster_start = i
            cluster_members = [i]

    _flush_cluster(cluster_members)

    out = proj.loc[keep_idx].sort_values(
        "shape_dist_traveled",
        ignore_index=True,
    )

    return out


def project_stops_to_shape(
    stops: gpd.GeoDataFrame,
    line: sg.LineString,
    *,
    buffer: float = cs.BUFFER,
    side: str = "both",
    distance_tolerance: float = 5.0,
) -> gpd.GeoDataFrame:
    """
    Select nearby stops and project them onto a shape.

    For supplied stops, ``side="both"`` is usually safer than enforcing a
    traffic-side filter too early.

    Parameters
    ----------
    stops
        Stops in a projected CRS.
    line
        Shape geometry in the same projected CRS.
    buffer
        Search buffer around the line.
    side
        "left", "right", or "both".
    distance_tolerance
        Tolerance, in projected CRS units, used to collapse near-identical
        projected positions along the shape.
    """
    nearby = get_stops_nearby(
        stops=stops,
        linestring=line,
        side=side,
        buffer=buffer,
    )
    if nearby.empty:
        return nearby.assign(shape_dist_traveled=pd.Series(dtype=float))

    projected = (
        nearby.assign(
            shape_dist_traveled=lambda df: df.geometry.apply(line.project),
            _dist_to_line=lambda df: df.geometry.distance(line),
        )
        .sort_values(
            ["shape_dist_traveled", "_dist_to_line", "stop_id"],
            ignore_index=True,
        )
        .drop_duplicates("stop_id")
    )

    projected = _coalesce_projected_stops(
        projected,
        distance_tolerance=distance_tolerance,
    )

    return projected.drop(columns="_dist_to_line", errors="ignore")


def compute_shape_point_speeds(
    shapes: pd.DataFrame,
    speed_zones: gpd.GeoDataFrame,
    route_type: int,
    *,
    use_utm: bool = False,
) -> gpd.GeoDataFrame:
    """Assign speeds to shape points by intersecting with speed zones."""
    columns = [
        "shape_id",
        "shape_pt_sequence",
        "shape_dist_traveled",
        "geometry",
        "route_type",
        "speed_zone_id",
        "speed",
    ]

    if shapes.empty or speed_zones.empty:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry")

    zones = speed_zones.loc[speed_zones["route_type"] == route_type].copy()
    if zones.empty:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry")

    utm_crs = zones.estimate_utm_crs()
    zones = zones.to_crs(utm_crs)

    shape_points = _build_shape_points(shapes, utm_crs)
    boundary_points = _find_boundary_points(shapes, zones, utm_crs)

    parts = [shape_points]
    if not boundary_points.empty:
        parts.append(boundary_points.assign(shape_pt_sequence=-1))

    combined = pd.concat(parts, ignore_index=True)
    combined = (
        gpd.GeoDataFrame(combined, geometry="geometry", crs=utm_crs)
        .sjoin(zones, how="inner")
        .drop(columns="index_right")
        .sort_values(
            ["shape_id", "shape_dist_traveled", "shape_pt_sequence"],
            ignore_index=True,
        )
    )

    if not use_utm:
        combined = combined.to_crs(cs.WGS84)

    return combined.filter(columns)


def _build_shape_points(
    shapes: pd.DataFrame,
    crs: str,
) -> gpd.GeoDataFrame:
    """Return shape points with cumulative distance along each shape."""
    points = (
        gpd.GeoDataFrame(
            shapes.copy(),
            geometry=gpd.points_from_xy(
                shapes["shape_pt_lon"],
                shapes["shape_pt_lat"],
            ),
            crs=cs.WGS84,
        )
        .to_crs(crs)
        .sort_values(["shape_id", "shape_pt_sequence"])
        .reset_index(drop=True)
    )

    def add_shape_distances(group: pd.DataFrame) -> pd.DataFrame:
        step = group.geometry.distance(group.geometry.shift(1))
        out = group.copy()
        out["shape_dist_traveled"] = step.fillna(0).cumsum()
        return out

    result = (
        points.groupby("shape_id", group_keys=False)
        .apply(add_shape_distances)
        .reset_index(drop=True)
    )

    return result.drop(columns=["shape_pt_lat", "shape_pt_lon"], errors="ignore")


def _find_boundary_points(
    shapes: pd.DataFrame,
    zones: gpd.GeoDataFrame,
    crs: str,
) -> gpd.GeoDataFrame:
    """Return points where shapes cross speed-zone boundaries."""
    shapes_gdf = gk.geometrize_shapes(shapes).to_crs(crs)
    zone_boundary = zones.boundary.union_all()

    rows: list[list[object]] = []

    for row in shapes_gdf.itertuples(index=False):
        line = row.geometry
        intersections = line.intersection(zone_boundary)

        for point in _extract_points(intersections):
            rows.append(
                [
                    row.shape_id,
                    float(line.project(point)),
                    point,
                ]
            )

    if not rows:
        return gpd.GeoDataFrame(
            columns=["shape_id", "shape_dist_traveled", "geometry"],
            geometry="geometry",
            crs=crs,
        )

    return gpd.GeoDataFrame(
        rows,
        columns=["shape_id", "shape_dist_traveled", "geometry"],
        geometry="geometry",
        crs=crs,
    )


def _extract_points(
    geometry: sg.base.BaseGeometry,
) -> list[sg.Point]:
    """Extract Point geometries from arbitrary Shapely intersections."""
    if geometry is None or geometry.is_empty:
        return []

    if isinstance(geometry, sg.Point):
        return [geometry]

    if isinstance(geometry, sg.MultiPoint):
        return list(geometry.geoms)

    if hasattr(geometry, "geoms"):
        points: list[sg.Point] = []
        for part in geometry.geoms:
            points.extend(_extract_points(part))
        return points

    return []
