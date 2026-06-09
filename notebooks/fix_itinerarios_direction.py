"""
fix_itinerarios_direction.py
============================
Fix direction anomalies in the Salineira route itineraries GeoPackage.

Problem
-------
The GeoPackage ``itinerarios_linhas_clean.gpkg`` contains 62 entries for
23 routes.  Each route should have exactly **2 entries** (1 ida + 1 volta),
but only 9 routes are clean.  The 14 anomalous routes exhibit three
types of issues:

  1. **"não identificado" labels** — always the missing ida (the entry
     starts far from the route's terminal).
  2. **Duplicate voltas** — one is a full trace from the terminal, the
     other a partial trace from an intermediate point.
  3. **Duplicate idas** — two full traces from the same origin
     (different digitisation passes).

Anomaly patterns observed::

    Pattern               Routes                    Fix
    ────────────────────  ────────────────────────  ──────────────────────────
    0i + 1v + 1 NI        310, 311, 354             NI → ida
    0i + 2v + 1 NI        303                       NI → ida; drop partial volta
    1i + 1v + 1 NI        348                       NI → ida (duplicate); drop it
    1i + 2v + 0 NI        302,309,316,329,332,338   drop partial volta
    2i + 2v + 0 NI        327, 352                  drop duplicate ida + volta
    2i + 3v + 1 NI        321                       NI → ida; dedup ida + volta

Root cause
----------
The GeoPackage was digitised with two volta traces per route — one
starting from the main terminal, the other from an intermediate point.
The second is a partial duplicate.  Entries labelled "não identificado"
are always the missing ida direction.

Additionally, the GeoPackage uses the OPPOSITE direction convention
from the schedule: GeoPackage 'volta' starts at the terminal (outbound),
while the schedule 'ida' departs from the terminal (outbound).  After
deduplication, the script swaps ida ↔ volta so that the output matches
the schedule (and standard Brazilian transit) convention.

Terminal identification
-----------------------
The Salineira system in Cabo Frio has two main terminals:

- **São Cristóvão** (~-42.0415, -22.893): used by routes named
  "São Cristóvão / ..." and most other routes.
- **Av. do Contorno** (~-42.008, -22.881): used by routes named
  "Avenida do Contorno / ...".

From Moovit and the clean routes, the digitisation convention is:
**volta starts at the terminal** (trace goes terminal → bairro),
**ida starts at the bairro** (trace goes bairro → terminal).

Usage
-----
CLI::

    python fix_itinerarios_direction.py itinerarios_linhas_clean.gpkg
    python fix_itinerarios_direction.py input.gpkg -o output_fixed.gpkg

Notebook::

    from fix_itinerarios_direction import fix_directions
    gdf_fixed, audit_df = fix_directions(gdf)
"""

from __future__ import annotations

import argparse
import pathlib
from math import sqrt

import geopandas as gpd
import pandas as pd


# ── Terminal coordinates ─────────────────────────────────────────────
# Identified from the clustering of volta start points in clean routes.

TERMINALS = {
    "São Cristóvão": (-42.0415, -22.893),
    "Contorno":      (-42.008,  -22.881),
}

# Threshold in degrees (~0.5 km) for "near terminal"
NEAR_THRESHOLD = 0.005


def _get_terminal(route_long_name: str) -> tuple[float, float]:
    """Determine the route's terminal from the route_long_name."""
    if "Contorno" in route_long_name:
        return TERMINALS["Contorno"]
    return TERMINALS["São Cristóvão"]


def _start_point(geom) -> tuple[float, float] | None:
    """First coordinate of a LineString geometry."""
    if geom is None or geom.is_empty:
        return None
    coords = list(geom.coords)
    return (coords[0][0], coords[0][1]) if coords else None


def _n_vertices(geom) -> int:
    """Number of vertices in the geometry."""
    if geom is None or geom.is_empty:
        return 0
    return len(list(geom.coords))


def _dist(p1, p2) -> float:
    """Euclidean distance in degrees (sufficient for ranking)."""
    return sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _near_terminal(start, terminal) -> bool:
    """True if start point is within NEAR_THRESHOLD of the terminal."""
    if start is None:
        return False
    return _dist(start, terminal) < NEAR_THRESHOLD


# ── Main fix function ────────────────────────────────────────────────

def fix_directions(
    gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """
    Fix direction anomalies and deduplicate route entries.

    Parameters
    ----------
    gdf : GeoDataFrame
        Must have columns: route_short_name, route_long_name,
        route_type, direction, geometry.

    Returns
    -------
    tuple[GeoDataFrame, DataFrame]
        - GeoDataFrame with exactly 1 ida + 1 volta per route_short_name.
        - Audit DataFrame describing relabel, drop, keep, and swap actions.
    """
    gdf = gdf.copy()
    audit_rows: list[dict[str, object]] = []

    def fid_value(idx: int) -> object:
        return gdf.at[idx, "fid"] if "fid" in gdf.columns else idx

    def add_audit(
        *,
        step: str,
        rsn: str,
        rln: str,
        idx: int,
        action: str,
        reason: str,
        direction_original: str,
        direction_final: str,
        kept: bool,
        dropped: bool,
    ) -> None:
        dist_deg = gdf.at[idx, "_dist_to_term"]
        audit_rows.append(
            {
                "route_short_name": rsn,
                "route_long_name": rln,
                "fid_original": fid_value(idx),
                "row_index": idx,
                "step": step,
                "action": action,
                "reason": reason,
                "direction_original": direction_original,
                "direction_final": direction_final,
                "dist_to_term_deg": dist_deg,
                "dist_to_term_km": dist_deg * 111,
                "near_terminal": gdf.at[idx, "_near_term"],
                "n_vertices": gdf.at[idx, "_npts"],
                "kept": kept,
                "dropped": dropped,
            }
        )

    # ── Helper columns ───────────────────────────────────────────────
    gdf["_start"] = gdf.geometry.apply(_start_point)
    gdf["_npts"] = gdf.geometry.apply(_n_vertices)
    gdf["_terminal"] = gdf["route_long_name"].apply(_get_terminal)
    gdf["_dist_to_term"] = gdf.apply(
        lambda row: (
            _dist(row["_start"], row["_terminal"])
            if row["_start"] is not None
            else 999
        ),
        axis=1,
    )
    gdf["_near_term"] = gdf["_dist_to_term"] < NEAR_THRESHOLD

    original_direction = gdf["direction"].copy()

    # ── Step 1: Rename "não identificado" ───────────────────────────
    print("Step 1 — Relabelling 'não identificado' entries\n")

    for rsn, group in gdf.groupby("route_short_name"):
        rln = group["route_long_name"].iloc[0]

        ni_mask = (gdf["route_short_name"] == rsn) & (
            gdf["direction"] == "não identificado"
        )
        if not ni_mask.any():
            continue

        has_ida = (group["direction"] == "ida").any()
        has_volta = (group["direction"] == "volta").any()

        ni_indices = gdf.loc[ni_mask].sort_values(
            "_dist_to_term",
            ascending=False,
        ).index

        for idx in ni_indices:
            old_dir = gdf.at[idx, "direction"]

            if not has_ida:
                new_dir = "ida"
                reason = "missing ida"
            elif not has_volta:
                new_dir = "volta"
                reason = "missing volta"
            else:
                if gdf.at[idx, "_near_term"]:
                    new_dir = "volta"
                    reason = "starts near terminal"
                else:
                    new_dir = "ida"
                    reason = "starts far from terminal"

            gdf.at[idx, "direction"] = new_dir
            d_km = gdf.at[idx, "_dist_to_term"] * 111

            print(
                f"  {rsn}: fid={fid_value(idx)} "
                f"→ '{new_dir}' ({reason}, {d_km:.1f} km from terminal)"
            )

            add_audit(
                step="relabel_ni",
                rsn=rsn,
                rln=rln,
                idx=idx,
                action="relabel",
                reason=reason,
                direction_original=old_dir,
                direction_final=new_dir,
                kept=True,
                dropped=False,
            )

            if new_dir == "ida":
                has_ida = True
            else:
                has_volta = True

    # ── Step 2: Deduplicate ──────────────────────────────────────────
    print("\nStep 2 — Deduplicating\n")

    keep_indices: list[int] = []
    n_dropped = 0

    for rsn, group in gdf.groupby("route_short_name"):
        rln = group["route_long_name"].iloc[0]

        for direction in ("ida", "volta"):
            candidates = group.loc[group["direction"] == direction]

            if candidates.empty:
                print(f"  ⚠ {rsn}: no '{direction}' entry!")
                continue

            if len(candidates) == 1:
                idx = candidates.index[0]
                keep_indices.append(idx)

                add_audit(
                    step="deduplicate",
                    rsn=rsn,
                    rln=rln,
                    idx=idx,
                    action="keep_unique",
                    reason="only candidate for direction",
                    direction_original=original_direction.at[idx],
                    direction_final=gdf.at[idx, "direction"],
                    kept=True,
                    dropped=False,
                )
                continue

            if direction == "volta":
                best_idx = candidates.sort_values(
                    ["_dist_to_term", "_npts"],
                    ascending=[True, False],
                ).index[0]
                keep_reason = (
                    "best volta candidate: closest to terminal, "
                    "tie-broken by vertices"
                )
            else:
                best_idx = candidates.sort_values(
                    "_npts",
                    ascending=False,
                ).index[0]
                keep_reason = "best ida candidate: highest vertex count"

            keep_indices.append(best_idx)

            add_audit(
                step="deduplicate",
                rsn=rsn,
                rln=rln,
                idx=best_idx,
                action="keep_best",
                reason=keep_reason,
                direction_original=original_direction.at[best_idx],
                direction_final=gdf.at[best_idx, "direction"],
                kept=True,
                dropped=False,
            )

            for idx in candidates.index:
                if idx == best_idx:
                    continue

                n_dropped += 1
                npts = gdf.at[idx, "_npts"]
                d_km = gdf.at[idx, "_dist_to_term"] * 111

                print(
                    f"  {rsn} {direction}: DROP fid={fid_value(idx)} "
                    f"({npts} pts, {d_km:.1f} km from terminal)"
                )

                add_audit(
                    step="deduplicate",
                    rsn=rsn,
                    rln=rln,
                    idx=idx,
                    action="drop_duplicate",
                    reason=f"worse {direction} candidate",
                    direction_original=original_direction.at[idx],
                    direction_final=gdf.at[idx, "direction"],
                    kept=False,
                    dropped=True,
                )

    # ── Step 3: Swap ida ↔ volta to match schedule convention ───────
    CONSISTENT_ROUTES = {"313", "346", "355"}

    print("\nStep 3 — Swapping ida ↔ volta to match schedule convention\n")

    swap_map = {"ida": "volta", "volta": "ida"}

    result = gdf.loc[keep_indices].copy()

    swap_mask = ~result["route_short_name"].isin(CONSISTENT_ROUTES)
    result.loc[swap_mask, "direction"] = (
        result.loc[swap_mask, "direction"].map(swap_map)
    )

    n_swapped = int(swap_mask.sum())
    print(
        f"  Swapped: {n_swapped} entries "
        f"(skipped {len(CONSISTENT_ROUTES)} already-consistent routes: "
        f"{sorted(CONSISTENT_ROUTES)})"
    )

    result_idx = set(result.index)
    swapped_idx = set(result.loc[swap_mask].index)

    for idx in keep_indices:
        if idx not in result_idx:
            continue

        rsn = gdf.at[idx, "route_short_name"]
        rln = gdf.at[idx, "route_long_name"]
        before_swap = gdf.at[idx, "direction"]
        after_swap = result.at[idx, "direction"]

        if idx in swapped_idx:
            add_audit(
                step="swap",
                rsn=rsn,
                rln=rln,
                idx=idx,
                action="swap_direction",
                reason="matches schedule convention",
                direction_original=before_swap,
                direction_final=after_swap,
                kept=True,
                dropped=False,
            )
        else:
            add_audit(
                step="swap",
                rsn=rsn,
                rln=rln,
                idx=idx,
                action="keep_direction",
                reason="route marked as already consistent",
                direction_original=before_swap,
                direction_final=after_swap,
                kept=True,
                dropped=False,
            )

    # ── Step 4: Validation ───────────────────────────────────────────
    print("\nStep 4 — Validation\n")

    ok = True
    for rsn, group in result.groupby("route_short_name"):
        dirs = sorted(group["direction"].tolist())
        if dirs != ["ida", "volta"]:
            print(f"  ⚠ {rsn}: {dirs} — expected ['ida', 'volta']")
            ok = False

    n_routes = result["route_short_name"].nunique()
    print(f"  Routes: {n_routes}")
    print(f"  Entries: {len(result)} (was {len(gdf)}, dropped {n_dropped})")
    print(f"  Status: {'✓ all clean' if ok else '⚠ some routes still anomalous'}")

    result = result.drop(
        columns=[
            "_start",
            "_npts",
            "_terminal",
            "_dist_to_term",
            "_near_term",
        ],
        errors="ignore",
    ).reset_index(drop=True)

    audit_df = pd.DataFrame(audit_rows).sort_values(
        ["route_short_name", "fid_original", "step", "action"],
        ignore_index=True,
    )

    return result, audit_df


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fix direction anomalies in route itineraries GeoPackage.",
    )
    parser.add_argument("input", help="Input .gpkg file")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output .gpkg file (default: <input>_fixed.gpkg)",
    )
    args = parser.parse_args()

    inpath = pathlib.Path(args.input)
    outpath = pathlib.Path(args.output) if args.output else inpath.with_stem(
        inpath.stem + "_fixed"
    )
    audit_path = outpath.with_name(outpath.stem + "_audit.csv")

    gdf = gpd.read_file(inpath)
    print(f"Input: {inpath}")
    print(f"  {len(gdf)} entries, {gdf['route_short_name'].nunique()} routes\n")

    gdf_fixed, audit_df = fix_directions(gdf)

    gdf_fixed.to_file(outpath, driver="GPKG")
    audit_df.to_csv(audit_path, index=False)

    print(f"\nSaved: {outpath}")
    print(f"Saved audit: {audit_path}")
