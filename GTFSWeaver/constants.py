"""
Package-wide constants.
"""

from __future__ import annotations

import pytz
import pycountry

WGS84 = "EPSG:4326"
SEP = "-"

# ── Geometry defaults ────────────────────────────────────────────────

BUFFER = 10
STOP_OFFSET = 5
DEFAULT_STOP_SPACING = 350
DEFAULT_H3_RESOLUTION = 9

# ── Speeds (km/h by GTFS route_type) ────────────────────────────────

SPEED_BY_ROUTE_TYPE: dict[int, float] = {
    0: 11, 1: 30, 2: 45, 3: 22, 4: 22,
    5: 13, 6: 20, 7: 18, 11: 22, 12: 65,
}

# ── Calendar ─────────────────────────────────────────────────────────

WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)
VALID_ROUTE_TYPES = list(range(8)) + [11, 12]

# ── Service patterns ─────────────────────────────────────────────────
#
# Predefined patterns: (weekday_bits, holiday_flag).
# "DOM" = domingos E feriados (Brazilian convention).

SERVICE_PATTERNS: dict[str, tuple[tuple[int, ...], bool]] = {
    "DU":      ((1, 1, 1, 1, 1, 0, 0), False),
    "SAB":     ((0, 0, 0, 0, 0, 1, 0), False),
    "DOM":     ((0, 0, 0, 0, 0, 0, 1), True),
    "FER":     ((0, 0, 0, 0, 0, 0, 0), True),
    "DU_SAB":  ((1, 1, 1, 1, 1, 1, 0), False),
    "TODOS":   ((1, 1, 1, 1, 1, 1, 1), False),
}

# ── Schedule types ───────────────────────────────────────────────────

SCHEDULE_HEADWAY = "headway"
SCHEDULE_FIXED = "fixed"

# ── File formats ─────────────────────────────────────────────────────

GEO_EXTENSIONS = frozenset({
    ".geojson", ".json", ".gpkg", ".shp", ".kml", ".kmz",
})

EXCEL_SHEET_AGENCY = "agency"
EXCEL_SHEET_ROUTES = "routes"
EXCEL_SHEET_HOLIDAYS = "holidays"

# ── Traffic side ─────────────────────────────────────────────────────

_COUNTRY_BY_ALPHA2 = {c.alpha_2: c.name for c in pycountry.countries}

ALPHA2_BY_TIMEZONE: dict[str, str] = {
    tz: a2
    for a2, tzs in pytz.country_timezones.items()
    for tz in tzs
}

COUNTRY_BY_TIMEZONE: dict[str, str] = {
    tz: _COUNTRY_BY_ALPHA2[a2] for tz, a2 in ALPHA2_BY_TIMEZONE.items()
}

LHT_COUNTRIES: frozenset[str] = frozenset({
    "Antigua and Barbuda", "Australia", "Bahamas", "Bangladesh",
    "Barbados", "Bhutan", "Botswana", "Brunei", "Cyprus", "Dominica",
    "East Timor", "Fiji", "Grenada", "Guyana", "Hong Kong", "India",
    "Indonesia", "Ireland", "Jamaica", "Japan", "Kenya", "Kiribati",
    "Lesotho", "Macau", "Malawi", "Malaysia", "Maldives", "Malta",
    "Mauritius", "Mozambique", "Namibia", "Nauru", "Nepal",
    "New Zealand", "Niue", "Northern Cyprus", "Pakistan",
    "Papua New Guinea", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "Seychelles",
    "Singapore", "Solomon Islands", "South Africa", "Sri Lanka",
    "Suriname", "Swaziland", "Tanzania", "Thailand", "Tonga",
    "Trinidad and Tobago", "Tuvalu", "Uganda", "United Kingdom",
    "Zambia", "Zimbabwe",
})

TRAFFIC_SIDE_BY_TIMEZONE: dict[str, str] = {
    tz: "left" if country in LHT_COUNTRIES else "right"
    for tz, country in COUNTRY_BY_TIMEZONE.items()
}