"""gtfsweaver — build GTFS feeds from basic route information.

Example
-------
    from gtfsweaver import build_feed, read_protofeed

    pfeed = read_protofeed(
        xlsx_path="operacional.xlsx",
        routes_geo_path="itinerarios.gpkg",
        stops_geo_path="paradas.gpkg",
    )
    feed = build_feed(pfeed, speed_mode="proportional")
    feed.write("gtfs.zip")
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .builders import build_feed
from .models import (
    Direction,
    ProtoFeed,
    holiday_action_from_pattern,
    make_route_id,
    parse_service_pattern,
)
from .qa import build_quality_report
from .readers import read_geo_file, read_protofeed
from .time_utils import duration_seconds, format_gtfs_time, parse_gtfs_time

try:
    __version__ = _pkg_version("gtfsweaver")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "Direction",
    "ProtoFeed",
    "build_feed",
    "build_quality_report",
    "duration_seconds",
    "format_gtfs_time",
    "holiday_action_from_pattern",
    "make_route_id",
    "parse_gtfs_time",
    "parse_service_pattern",
    "read_geo_file",
    "read_protofeed",
]
