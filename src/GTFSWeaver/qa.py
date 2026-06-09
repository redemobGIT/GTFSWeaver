# qa.py
from __future__ import annotations

import pandas as pd
import gtfs_kit as gk


def build_quality_report(feed: gk.Feed) -> dict[str, pd.DataFrame]:
    report: dict[str, pd.DataFrame] = {}

    st = feed.stop_times.copy()
    trips = feed.trips.copy()

    report["trips_without_stop_times"] = trips.loc[
        ~trips["trip_id"].isin(st["trip_id"].unique())
    ].copy()

    report["non_monotone_times"] = (
        st.sort_values(["trip_id", "stop_sequence"])
        .groupby("trip_id")
        .filter(
            lambda df: (
                (df["arrival_time"].shift(-1) < df["arrival_time"]).fillna(False).any()
                or (df["departure_time"].shift(-1) < df["departure_time"]).fillna(False).any()
            )
        )
    )

    report["non_monotone_shape_dist"] = (
        st.sort_values(["trip_id", "stop_sequence"])
        .groupby("trip_id")
        .filter(
            lambda df: (df["shape_dist_traveled"].diff().fillna(0) < 0).any()
        )
    )

    report["duplicate_trip_stop_sequence"] = st.loc[
        st.duplicated(["trip_id", "stop_sequence"], keep=False)
    ].copy()

    return report


def has_blocking_issues(report: dict[str, pd.DataFrame]) -> bool:
    blocking = [
        "trips_without_stop_times",
        "non_monotone_times",
        "non_monotone_shape_dist",
        "duplicate_trip_stop_sequence",
    ]
    return any(not report[k].empty for k in blocking if k in report)