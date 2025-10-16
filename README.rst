GTFSWeaver
***********
.. image:: https://github.com/your-user/GTFSWeaver/actions/workflows/test.yml/badge.svg

A Python 3.10+ library to **weave GTFS feeds** from minimal, human-friendly route inputs.  
Inspired by `make_gtfs <https://github.com/mrcagney/make_gtfs>`_ and Conveyal’s
`geom2gtfs <https://github.com/conveyal/geom2gtfs>`_,  
GTFSWeaver simplifies feed construction by consolidating inputs into a single intuitive
table and providing transparent defaults for speeds, frequencies and directions.

Contributors
============
- José B. (maintainer), 2025–
- Based on original work by Alex Raichev (2014–2024)

Installation
=============
GTFSWeaver supports modern dependency managers such as `uv` and Poetry.

To install as a dependency in your own project::

    uv add gtfs-weaver

To develop the project locally::

    git clone https://github.com/your-user/GTFSWeaver.git
    cd GTFSWeaver
    uv sync

(If using Poetry, replace the `uv` commands accordingly.)

Usage
=====
GTFSWeaver can be used both as a **library** and from the **command line**.

To view available options::

    uv run gtfs-weaver --help

The library constructs a valid GTFS feed from a small, self-contained set of files.

Required and optional inputs
============================

- ``meta.csv`` (required) — metadata for the agency and feed validity period.

  Columns:
  - ``agency_name`` (required): name of the transport agency  
  - ``agency_url`` (required): fully qualified URL  
  - ``agency_timezone`` (required): Olson timezone name (e.g. ``America/Sao_Paulo``)  
  - ``start_date`` / ``end_date`` (required): YYYYMMDD validity interval

- ``lines.geojson`` (required) — a GeoJSON FeatureCollection of ``LineString`` features.  
  Each feature represents one representative trip of a route, with property ``shape_id``.
  Shapes should not traverse the same segment repeatedly unless the route truly does so.

- ``timetable.csv`` (required) — a **single consolidated table** describing all routes,
  timebands and headways.

  Columns:

  - ``route_id`` (required): unique identifier  
  - ``route_short_name`` (required): short name, e.g. “51X”  
  - ``route_long_name`` (optional): full descriptive name  
  - ``route_type`` (required): GTFS route type integer  
  - ``shape_id`` (required): matches a feature in ``lines.geojson``  
  - ``direction`` (optional): 0 = reverse, 1 = forward, 2 = both (default 2)  
  - ``dow`` (required): day mask such as ``MTWTFSS``, ``MTWTF``, or ``SS``  
  - ``start_time`` (required): start of service window (HH:MM:SS)  
  - ``end_time`` (required): end of service window (HH:MM:SS)  
  - ``headway_min`` (required): headway in minutes  
  - ``speed_kph`` (optional): average speed (km/h); defaults from internal dictionary

  Each row defines one operational timeband for a route pattern.
  Multiple rows may be supplied for different day masks or periods.

- ``stops.csv`` (optional) — a CSV conforming to GTFS `stops.txt`_.
  If omitted, GTFSWeaver automatically creates endpoints (and avoids duplicates on loops).

- ``speed_zones.geojson`` (optional) — Polygon features defining local speed overrides.

  Properties:
  - ``speed_zone_id`` (required): unique polygon id  
  - ``route_type`` (required): GTFS route type  
  - ``speed`` (required): mean speed (km/h) overriding ``timetable.csv`` inside the polygon

  Missing speeds in ``timetable.csv`` are filled using defaults in ``SPEED_BY_RTYPE``.

.. _stops.txt: https://developers.google.com/transit/gtfs/reference/#stopstxt

Algorithm
=========
Conceptually:

- ``agency.txt`` ← ``meta.csv``  
- ``routes.txt`` ← unique routes in ``timetable.csv``  
- ``calendar.txt`` ← single all-week service using ``dow`` masks per route  
- ``shapes.txt`` ← ``lines.geojson``  
  - Fixes broken route shape geometries
- ``stops.txt`` ← ``stops.csv`` or estimated from regular spacings or from GPS data  
- ``trips.txt`` and ``stop_times.txt`` ← for each row in ``timetable.csv``:
  - derive frequency (veh/h = 60 / ``headway_min``);
  - compute trip times from length and speed;
  - if ``direction = 2``, create reverse trips as well;
  - assign stops within the traffic-side buffer.
- The resulting feed is validated and written via::

      feed.write("gtfsfile.zip")

Example files
=============
See ``examples/minimal`` and ``examples/full`` for ready-to-run templates.
A sample Jupyter notebook is available in ``notebooks/examples.ipynb``.

Documentation
=============
Project documentation and API reference are hosted on GitHub Pages:  
`https://your-user.github.io/gtfsweaver-docs <https://your-user.github.io/gtfsweaver-docs>`_.

Notes
=====
- Project status: **Alpha / MVP** — rapid iteration expected.
- Semantic versioning is followed.
- Derived from `make_gtfs` (MIT License, © Alex Raichev) with a couple structural changes and new features.
- Developed under the RedeMob / Mob 4.0 initiative.

Change log
===========

0.1.0, 2025-02-??
-----------------
- Introduced consolidated input model (`timetable.csv`).
- Updated validation and feed-building pipeline.
- Added automatic defaults for speeds and directions.
- Renamed project to **GTFSWeaver**.

