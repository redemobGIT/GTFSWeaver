"""Utilities for GTFS time parsing and formatting."""

from __future__ import annotations

import re

_GTFS_TIME = re.compile(
    r"^(?P<h>\d{1,3}):(?P<m>[0-5]\d):(?P<s>[0-5]\d)$"
)


def parse_gtfs_time(value: str) -> int:
    """Parse a GTFS time string into seconds from service-day start.

    GTFS allows hours beyond 23 for after-midnight service.
    """
    match = _GTFS_TIME.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(
            f"Invalid GTFS time {value!r}. Expected HH:MM:SS with HH >= 0."
        )

    hours = int(match["h"])
    minutes = int(match["m"])
    seconds = int(match["s"])
    return hours * 3600 + minutes * 60 + seconds


def format_gtfs_time(value: int | float) -> str:
    """Format seconds from service-day start as a GTFS time string."""
    total_seconds = int(round(value))
    if total_seconds < 0:
        raise ValueError(
            f"GTFS times cannot be negative: {value!r}"
        )

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def duration_seconds(
    start: str,
    end: str,
    *,
    allow_wrap: bool = True,
) -> int:
    """Return the duration between two GTFS times in seconds.

    When ``allow_wrap`` is true and ``end < start``, the function assumes
    ``end`` refers to the following service day.
    """
    start_seconds = parse_gtfs_time(start)
    end_seconds = parse_gtfs_time(end)

    if allow_wrap and end_seconds < start_seconds:
        end_seconds += 24 * 3600

    return end_seconds - start_seconds