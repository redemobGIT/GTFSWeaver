"""
make_gtfs — Build GTFS feeds from basic route information.

    from make_gtfs import read_protofeed_from_excel, build_feed

    pfeed = read_protofeed_from_excel(
        "operacional.xlsx",         # agency + routes + holidays sheets
        "itinerarios.gpkg",         # route LineStrings with route_short_name + direction
        stops_geo_path="paradas.shp",
    )
    feed = build_feed(pfeed, speed_mode="proportional")
    feed.write("gtfs.zip")
"""

from .models import (
    Direction, ProtoFeed, TripKey,
    make_shape_id, make_route_id, parse_service_pattern,
)
from .readers import read_protofeed, read_protofeed_from_excel, read_geo_file
from .builders import build_feed

__version__ = "1.0.0"

__all__ = [
    "Direction", "ProtoFeed", "TripKey",
    "make_shape_id", "make_route_id", "parse_service_pattern",
    "read_protofeed", "read_protofeed_from_excel", "read_geo_file",
    "build_feed",
]