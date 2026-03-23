"""
Command-line interface.

    make_gtfs from-directory ./input/ output.zip
    make_gtfs from-excel routes.xlsx itinerarios.gpkg output.zip \\
        --stops-geo-path paradas.shp
"""

from __future__ import annotations

import click

from . import constants as cs
from .readers import read_protofeed, read_protofeed_from_excel
from .builders import build_feed


@click.group()
def cli():
    """make_gtfs — Build GTFS feeds from basic route information."""


@cli.command()
@click.argument("source_path", type=click.Path(exists=True))
@click.argument("target_path", type=click.Path())
@click.option("-bf", "--buffer", default=cs.BUFFER, type=float, show_default=True)
@click.option("-so", "--stop-offset", default=cs.STOP_OFFSET, type=float, show_default=True)
@click.option("-ns", "--num-stops", default=2, type=int, show_default=True)
@click.option("-ss", "--stop-spacing", default=None, type=float)
@click.option("-sm", "--speed-mode", default="zones",
              type=click.Choice(["zones", "proportional"]), show_default=True)
@click.option("--cluster-h3/--no-cluster-h3", default=False, show_default=True)
@click.option("--use-frequencies/--no-frequencies", default=False, show_default=True)
@click.option("-nd", "--num-digits", default=6, type=int, show_default=True)
def from_directory(source_path, target_path, buffer, stop_offset, num_stops,
                   stop_spacing, speed_mode, cluster_h3, use_frequencies, num_digits):
    """Build GTFS from a directory of CSV/GeoJSON files."""
    pfeed = read_protofeed(source_path)
    feed = build_feed(
        pfeed, buffer=buffer, stop_offset=stop_offset,
        num_stops_per_shape=num_stops, stop_spacing=stop_spacing,
        speed_mode=speed_mode, cluster_h3=cluster_h3,
        use_frequencies=use_frequencies,
    )
    feed.write(target_path, ndigits=num_digits)
    click.echo(f"GTFS written to {target_path}")


@cli.command()
@click.argument("xlsx_path", type=click.Path(exists=True))
@click.argument("routes_geo_path", type=click.Path(exists=True))
@click.argument("target_path", type=click.Path())
@click.option("-sp", "--stops-geo-path", default=None, type=click.Path(exists=True),
              help="Stops geo file (GeoJSON/GPKG/SHP/KML).")
@click.option("-bf", "--buffer", default=cs.BUFFER, type=float, show_default=True)
@click.option("-so", "--stop-offset", default=cs.STOP_OFFSET, type=float, show_default=True)
@click.option("-ss", "--stop-spacing", default=cs.DEFAULT_STOP_SPACING, type=float, show_default=True)
@click.option("-sm", "--speed-mode", default="proportional",
              type=click.Choice(["zones", "proportional"]), show_default=True)
@click.option("--cluster-h3/--no-cluster-h3", default=True, show_default=True)
@click.option("--use-frequencies/--no-frequencies", default=False, show_default=True)
@click.option("-nd", "--num-digits", default=6, type=int, show_default=True)
def from_excel(xlsx_path, routes_geo_path, target_path, stops_geo_path,
               buffer, stop_offset, stop_spacing, speed_mode,
               cluster_h3, use_frequencies, num_digits):
    """Build GTFS from Excel workbook + geo files."""
    pfeed = read_protofeed_from_excel(
        xlsx_path, routes_geo_path, stops_geo_path=stops_geo_path,
    )
    feed = build_feed(
        pfeed, buffer=buffer, stop_offset=stop_offset,
        stop_spacing=stop_spacing, speed_mode=speed_mode,
        cluster_h3=cluster_h3, use_frequencies=use_frequencies,
    )
    feed.write(target_path, ndigits=num_digits)
    click.echo(f"GTFS written to {target_path}")