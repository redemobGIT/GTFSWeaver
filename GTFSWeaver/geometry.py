"""
Spatial utilities: stop generation, H3 clustering, speed-zone intersection.
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


# ── Stop point generation ────────────────────────────────────────────

def make_stop_points(
    lines: gpd.GeoDataFrame,
    id_col: str,
    offset: float,
    side: str,
    num_stops: int = 2,
    spacing: float | None = None,
) -> gpd.GeoDataFrame:
    """
    Generate equally-spaced points along each line, offset to one side.

    Returns GeoDataFrame with ``point_id``, ``{id_col}``,
    ``shape_dist_traveled``, and ``geometry``.
    """
    num_stops = max(int(num_stops), 2)
    frames: list[gpd.GeoDataFrame] = []

    for row in lines.itertuples(index=False):
        geom = row.geometry
        length = geom.length
        step = spacing if spacing is not None else length / (num_stops - 1)
        dists = _sample_distances(length, step)
        points = _interpolate_with_offset(geom, dists, offset, side)

        line_id = getattr(row, id_col)
        suffixes = gk.make_ids(len(points), prefix="")
        frame = gpd.GeoDataFrame(
            {
                "point_id": [f"{line_id}{cs.SEP}{s}" for s in suffixes],
                id_col: line_id,
                "shape_dist_traveled": dists,
            },
            geometry=gpd.points_from_xy(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                crs=lines.crs,
            ),
        ).drop_duplicates("geometry")
        frames.append(frame)

    return pd.concat(frames) if frames else gpd.GeoDataFrame()


def _sample_distances(length: float, step: float) -> list[float]:
    """Equally-spaced distances along a line, always including the endpoint."""
    return [step * i for i in range(math.floor(length / step))] + [length]


def _interpolate_with_offset(
    line: sg.LineString,
    dists: list[float],
    offset: float,
    side: str,
) -> list[tuple[float, float]]:
    """Interpolate points along ``line``, then offset perpendicular."""
    if offset <= 0:
        return [line.interpolate(d).coords[0] for d in dists]

    points_on = [line.interpolate(d) for d in dists]
    guide = line.parallel_offset(0.1, side)
    points_guide = [guide.interpolate(guide.project(p)) for p in points_on]

    result = []
    for p_on, p_guide in zip(points_on, points_guide):
        vec = np.array(p_guide.coords[0]) - np.array(p_on.coords[0])
        norm = np.linalg.norm(vec)
        if norm > 0:
            result.append(tuple(np.array(p_on.coords[0]) + offset * (vec / norm)))
        else:
            result.append(p_on.coords[0])
    return result


# ── H3 hex clustering ────────────────────────────────────────────────

def cluster_stops_h3(
    stops: gpd.GeoDataFrame,
    resolution: int = cs.DEFAULT_H3_RESOLUTION,
) -> gpd.GeoDataFrame:
    """
    Deduplicate nearby stops using H3 hexagonal grid clustering.

    Within each cell, the stop closest to the cell centroid is kept.

    Parameters
    ----------
    stops
        Point GeoDataFrame with at least ``stop_id`` and ``geometry``.
    resolution
        H3 resolution (default 9 ≈ ~175 m edge length).
    """
    try:
        import h3
    except ImportError:
        raise ImportError(
            "H3 clustering requires the 'h3' package: pip install h3"
        ) from None

    original_crs = stops.crs
    gdf = stops.to_crs(cs.WGS84).copy()
    gdf["_h3"] = gdf.geometry.apply(
        lambda pt: h3.latlng_to_cell(pt.y, pt.x, resolution)
    )

    def _pick(group: gpd.GeoDataFrame) -> pd.Series:
        if len(group) == 1:
            return group.iloc[0]
        cell = group["_h3"].iloc[0]
        lat, lng = h3.cell_to_latlng(cell)
        centroid = sg.Point(lng, lat)
        return group.loc[group.geometry.distance(centroid).idxmin()]

    result = (
        gdf.groupby("_h3", group_keys=False).apply(_pick)
        .drop(columns="_h3")
        .reset_index(drop=True)
    )
    return gpd.GeoDataFrame(result, crs=cs.WGS84).to_crs(original_crs)


# ── Buffer & stop matching ───────────────────────────────────────────

def buffer_side(
    linestring: sg.LineString,
    side: str,
    buffer: float,
) -> sg.Polygon:
    """Buffer a LineString on one side only."""
    full_buffer = linestring.buffer(buffer, cap_style=2)
    if side not in ("left", "right") or buffer <= 0:
        return full_buffer
    eps = min(buffer / 2, 0.001)
    splitter = linestring.buffer(eps, cap_style=3)
    diff = full_buffer.difference(splitter)
    halves = list(so.polygonize(diff))
    half = halves[0] if side == "left" else halves[-1]
    return half.buffer(1.1 * eps)


def get_stops_nearby(
    stops: gpd.GeoDataFrame,
    linestring: sg.LineString,
    side: str,
    buffer: float = cs.BUFFER,
) -> gpd.GeoDataFrame:
    """Return stops within ``buffer`` distance on ``side`` of the line."""
    area = buffer_side(linestring, side, buffer)
    return stops.loc[stops.intersects(area)].copy()


# ── Speed zone intersection ──────────────────────────────────────────

def compute_shape_point_speeds(
    shapes: pd.DataFrame,
    speed_zones: gpd.GeoDataFrame,
    route_type: int,
    *,
    use_utm: bool = False,
) -> gpd.GeoDataFrame:
    """Assign speeds to shape points by intersecting with speed zones."""
    zones = speed_zones.loc[speed_zones.route_type == route_type]
    if zones.empty:
        return gpd.GeoDataFrame()

    utm_crs = zones.estimate_utm_crs()
    zones = zones.to_crs(utm_crs)
    shape_points = _build_shape_points(shapes, utm_crs)
    boundary_points = _find_boundary_points(shapes, zones, utm_crs)

    combined = (
        pd.concat([shape_points, boundary_points.assign(shape_pt_sequence=-1)])
        .sjoin(zones).drop(columns="index_right")
        .sort_values(
            ["shape_id", "shape_dist_traveled", "shape_pt_sequence"],
            ignore_index=True,
        )
    )

    if not use_utm:
        combined = combined.to_crs(cs.WGS84)

    return combined.filter([
        "shape_id", "shape_pt_sequence", "shape_dist_traveled",
        "geometry", "route_type", "speed_zone_id", "speed",
    ])


def _build_shape_points(shapes: pd.DataFrame, crs: str) -> gpd.GeoDataFrame:
    def _add_distances(group: pd.DataFrame) -> pd.DataFrame:
        shifted = group.geometry.shift(1)
        group["shape_dist_traveled"] = (
            group.geometry.distance(shifted, align=False).fillna(0).cumsum()
        )
        return group

    return (
        gpd.GeoDataFrame(
            shapes,
            geometry=gpd.points_from_xy(shapes.shape_pt_lon, shapes.shape_pt_lat),
            crs=cs.WGS84,
        )
        .to_crs(crs)
        .sort_values(["shape_id", "shape_pt_sequence"])
        .groupby("shape_id")
        .apply(_add_distances, include_groups=False)
        .drop(columns=["shape_pt_lat", "shape_pt_lon"], errors="ignore")
    )


def _find_boundary_points(
    shapes: pd.DataFrame,
    zones: gpd.GeoDataFrame,
    crs: str,
) -> gpd.GeoDataFrame:
    shapes_g = gk.geometrize_shapes(shapes).to_crs(crs)
    shapes_g["boundary_points"] = shapes_g.intersection(
        zones.boundary, align=True,
    )

    rows = []
    for shape_id, group in shapes_g.groupby("shape_id"):
        boundary = group["boundary_points"].iat[0]
        if boundary is None or boundary.is_empty:
            continue
        pts = [boundary] if isinstance(boundary, sg.Point) else boundary.geoms
        for pt in pts:
            dist = group.geometry.iat[0].project(pt)
            rows.append([shape_id, dist, pt])

    return gpd.GeoDataFrame(
        rows, columns=["shape_id", "shape_dist_traveled", "geometry"], crs=crs,
    )