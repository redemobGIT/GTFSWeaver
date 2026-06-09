
from __future__ import annotations

import math
from dataclasses import dataclass

import geopandas as gpd
import gtfs_kit as gk
import numpy as np
import pandas as pd
import shapely.geometry as sg


def parse_gtfs_time_to_seconds(value) -> float:
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return np.nan
    parts = text.split(":")
    if len(parts) != 3:
        return np.nan
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + int(s)


def seconds_to_hhmmss(value: float) -> str | None:
    if pd.isna(value):
        return None
    value = int(value)
    h = value // 3600
    m = (value % 3600) // 60
    s = value % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def hhmmss_to_hours(value: str) -> float:
    parts = str(value).split(":")
    return int(parts[0]) + int(parts[1]) / 60 + int(parts[2]) / 3600


def assign_time_window(hour: float, time_windows: list[tuple[str, str, str]]) -> str:
    if pd.isna(hour):
        return "Sem horário"
    for label, start, end in time_windows:
        if hhmmss_to_hours(start) <= hour < hhmmss_to_hours(end):
            return label
    return "Fora da janela"


def choose_busiest_date(feed) -> str:
    dates = sorted(feed.get_dates())
    if not dates:
        raise ValueError("O feed não possui datas válidas de serviço.")
    activity = feed.compute_trip_activity(dates)
    totals = activity[dates].sum(axis=0)
    return totals.idxmax()


def infer_local_crs(feed) -> int:
    if feed.shapes is not None and not feed.shapes.empty:
        shapes_gdf = gk.geometrize_shapes(feed.shapes)
        crs = shapes_gdf.estimate_utm_crs()
        if crs is not None:
            return crs.to_epsg()
    stops = feed.stops.dropna(subset=["stop_lon", "stop_lat"]).copy()
    stops_gdf = gpd.GeoDataFrame(
        stops,
        geometry=gpd.points_from_xy(stops.stop_lon, stops.stop_lat),
        crs=4326,
    )
    crs = stops_gdf.estimate_utm_crs()
    if crs is None:
        raise ValueError("Não foi possível inferir um CRS local.")
    return crs.to_epsg()


def ensure_shape_dist_traveled(feed):
    if feed.stop_times is None or feed.stop_times.empty:
        raise ValueError("O feed não possui stop_times.")
    has_field = "shape_dist_traveled" in feed.stop_times.columns
    if (not has_field) or feed.stop_times["shape_dist_traveled"].isna().all():
        return feed.append_dist_to_stop_times()
    return feed


def compute_active_trip_ids(feed, analysis_date: str) -> set[str]:
    activity = feed.compute_trip_activity([analysis_date])
    return set(activity.loc[activity[analysis_date] > 0, "trip_id"])


def build_trip_metrics_raw(feed, active_trip_ids: set[str]) -> pd.DataFrame:
    st = feed.stop_times.loc[feed.stop_times["trip_id"].isin(active_trip_ids)].copy()
    if st.empty:
        raise ValueError("Nenhuma viagem ativa encontrada para a data de análise.")

    st["arrival_secs"] = st["arrival_time"].apply(parse_gtfs_time_to_seconds)
    st["departure_secs"] = st["departure_time"].apply(parse_gtfs_time_to_seconds)
    st = st.sort_values(["trip_id", "stop_sequence"])

    trip = (
        st.groupby("trip_id")
        .agg(
            start_secs=("departure_secs", "first"),
            end_secs=("arrival_secs", "last"),
            start_shape=("shape_dist_traveled", "first"),
            end_shape=("shape_dist_traveled", "last"),
            n_stops=("stop_id", "nunique"),
        )
        .reset_index()
    )

    trip["duration_h"] = (trip["end_secs"] - trip["start_secs"]) / 3600
    trip["distance_raw"] = trip["end_shape"] - trip["start_shape"]
    trip["start_time"] = trip["start_secs"].apply(seconds_to_hhmmss)
    trip["end_time"] = trip["end_secs"].apply(seconds_to_hhmmss)
    trip["start_hour"] = trip["start_secs"] / 3600

    trip = trip.merge(
        feed.trips[["trip_id", "route_id", "direction_id", "shape_id", "service_id"]],
        on="trip_id",
        how="left",
    )
    trip = trip.merge(
        feed.routes[
            [c for c in ["route_id", "route_short_name", "route_long_name", "route_type"] if c in feed.routes.columns]
        ],
        on="route_id",
        how="left",
    )
    return trip


def infer_distance_scale(trip_metrics_raw: pd.DataFrame) -> dict:
    sample = trip_metrics_raw.copy()
    sample = sample.loc[
        sample["distance_raw"].notna()
        & sample["duration_h"].notna()
        & (sample["distance_raw"] > 0)
        & (sample["duration_h"] > 0)
    ].copy()
    if sample.empty:
        return {
            "scale_factor": 1.0,
            "unit_guess": "desconhecida",
            "status": "insufficient",
            "message": "Não foi possível inferir a escala de distância.",
        }

    candidates = []
    for factor, label in [(1.0, "km"), (0.001, "m→km")]:
        speed = sample["distance_raw"] * factor / sample["duration_h"]
        median_speed = float(speed.median())
        p95_speed = float(speed.quantile(0.95))
        median_dist = float((sample["distance_raw"] * factor).median())
        extreme_share = float((speed > 80).mean())

        score = 0.0
        score += abs(median_speed - 18)
        score += extreme_share * 100
        if median_dist > 80:
            score += 25
        if median_dist < 0.5:
            score += 25
        if p95_speed > 100:
            score += 25

        candidates.append(
            {
                "scale_factor": factor,
                "unit_guess": label,
                "median_speed": median_speed,
                "p95_speed": p95_speed,
                "median_dist": median_dist,
                "extreme_share": extreme_share,
                "score": score,
            }
        )

    best = min(candidates, key=lambda x: x["score"])
    alt = max(candidates, key=lambda x: x["score"])

    status = "ok"
    message = "Escala de distância plausível."
    if best["median_speed"] > 60 or best["median_dist"] > 80:
        status = "suspect"
        message = "Mesmo a melhor hipótese ainda gera métricas pouco plausíveis."
    elif best["unit_guess"] == "m→km":
        message = "Os valores parecem estar em metros; o notebook converteu para km."

    best["status"] = status
    best["message"] = message
    best["alternatives"] = candidates
    best["difference_vs_alt"] = alt["score"] - best["score"]
    return best


def finalize_trip_metrics(trip_metrics_raw: pd.DataFrame, distance_scale: dict) -> pd.DataFrame:
    trip = trip_metrics_raw.copy()
    factor = distance_scale["scale_factor"]
    trip["distance_km"] = trip["distance_raw"] * factor
    trip["speed_kmh"] = trip["distance_km"] / trip["duration_h"]
    trip.loc[~np.isfinite(trip["speed_kmh"]), "speed_kmh"] = np.nan
    trip["distance_unit_assumption"] = distance_scale["unit_guess"]
    return trip


def build_overview(feed, analysis_date: str, local_crs: int, active_trip_ids: set[str]) -> pd.DataFrame:
    agency = feed.agency.iloc[0].to_dict() if feed.agency is not None and not feed.agency.empty else {}
    overview = {
        "Operadora": agency.get("agency_name", "—"),
        "Timezone": agency.get("agency_timezone", "—"),
        "Data de análise": analysis_date,
        "Rotas no feed": len(feed.routes) if feed.routes is not None else 0,
        "Paradas no feed": len(feed.stops) if feed.stops is not None else 0,
        "Viagens no feed": len(feed.trips) if feed.trips is not None else 0,
        "Viagens ativas no dia": len(active_trip_ids),
        "Shapes": feed.shapes["shape_id"].nunique() if feed.shapes is not None else 0,
        "Datas no feed": len(feed.get_dates()),
        "CRS local": f"EPSG:{local_crs}",
    }
    return pd.DataFrame(list(overview.items()), columns=["Métrica", "Valor"])


def build_network_time_series(feed, analysis_date: str) -> pd.DataFrame:
    rts = feed.compute_route_time_series(
        [analysis_date],
        freq="10min",
        split_directions=False,
    )
    nts = (
        rts.groupby("datetime")
        .agg(
            num_trips=("num_trips", "sum"),
            num_trip_starts=("num_trip_starts", "sum"),
        )
        .reset_index()
    )
    ts = pd.to_datetime(nts["datetime"])
    nts["hour"] = ts.dt.hour + ts.dt.minute / 60
    return nts


def build_stop_stats_gdf(feed, analysis_date: str, headway_start: str, headway_end: str, split_directions: bool = False):
    stop_stats = feed.compute_stop_stats(
        [analysis_date],
        headway_start_time=headway_start,
        headway_end_time=headway_end,
        split_directions=split_directions,
    )
    stops_gdf = (
        feed.stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]]
        .merge(stop_stats, on="stop_id", how="inner")
        .pipe(
            lambda df: gpd.GeoDataFrame(
                df,
                geometry=gpd.points_from_xy(df.stop_lon, df.stop_lat),
                crs=4326,
            )
        )
    )
    stops_gdf["frequency_per_hour"] = np.where(
        stops_gdf["mean_headway"] > 0,
        60 / stops_gdf["mean_headway"],
        np.nan,
    )
    return stops_gdf


def build_trip_window_summary(trip_metrics: pd.DataFrame, time_windows: list[tuple[str, str, str]]) -> pd.DataFrame:
    df = trip_metrics.copy()
    df["window"] = df["start_hour"].apply(lambda h: assign_time_window(h, time_windows))
    summary = (
        df.groupby("window")
        .agg(
            trips=("trip_id", "count"),
            km_total=("distance_km", "sum"),
            duracao_h=("duration_h", "sum"),
        )
        .reindex([w[0] for w in time_windows] + ["Sem horário", "Fora da janela"])
    )
    summary = summary.dropna(how="all")
    return summary


def build_route_summary(trip_metrics: pd.DataFrame) -> pd.DataFrame:
    df = trip_metrics.copy()
    summary = (
        df.groupby(["route_id", "route_short_name", "route_long_name"], dropna=False)
        .agg(
            viagens=("trip_id", "count"),
            km_total=("distance_km", "sum"),
            km_medio=("distance_km", "mean"),
            duracao_media_min=("duration_h", lambda x: x.mean() * 60),
            vel_media_kmh=("speed_kmh", "mean"),
        )
        .sort_values(["km_total", "viagens"], ascending=[False, False])
        .reset_index()
    )
    return summary


def build_interstop_proxy(feed, active_trip_ids: set[str]) -> gpd.GeoDataFrame:
    st = feed.stop_times.loc[feed.stop_times["trip_id"].isin(active_trip_ids)].copy()
    st = st.merge(feed.trips[["trip_id", "route_id", "direction_id"]], on="trip_id", how="left")
    st = st.sort_values(["trip_id", "stop_sequence"])
    st["next_stop_id"] = st.groupby("trip_id")["stop_id"].shift(-1)
    seg = st.dropna(subset=["next_stop_id"]).copy()
    seg["seg_key"] = seg.apply(lambda r: tuple(sorted([r["stop_id"], r["next_stop_id"]])), axis=1)

    seg = (
        seg.groupby("seg_key")
        .agg(
            n_routes=("route_id", "nunique"),
            n_trips=("trip_id", "nunique"),
            stop_a=("stop_id", "first"),
            stop_b=("next_stop_id", "first"),
        )
        .reset_index()
    )

    stop_pts = dict(
        zip(
            feed.stops["stop_id"],
            gpd.points_from_xy(feed.stops.stop_lon, feed.stops.stop_lat),
        )
    )

    seg["geometry"] = seg.apply(
        lambda r: sg.LineString([
            stop_pts.get(r["stop_a"], sg.Point(0, 0)),
            stop_pts.get(r["stop_b"], sg.Point(0, 0)),
        ]),
        axis=1,
    )
    seg_gdf = gpd.GeoDataFrame(seg, geometry="geometry", crs=4326)
    seg_gdf = seg_gdf.loc[~seg_gdf.geometry.is_empty & seg_gdf.geometry.is_valid].copy()
    seg_gdf["proxy_note"] = "proxy interparadas"
    return seg_gdf


def build_stop_spacing(feed, active_trip_ids: set[str], distance_scale: dict) -> pd.DataFrame:
    st = feed.stop_times.loc[feed.stop_times["trip_id"].isin(active_trip_ids)].copy()
    st = st.sort_values(["trip_id", "stop_sequence"])
    st["next_dist"] = st.groupby("trip_id")["shape_dist_traveled"].shift(-1)
    st["segment_raw"] = st["next_dist"] - st["shape_dist_traveled"]
    st["segment_km"] = st["segment_raw"] * distance_scale["scale_factor"]
    st = st.dropna(subset=["segment_km"])
    st = st.loc[st["segment_km"] > 0].copy()
    st["segment_m"] = st["segment_km"] * 1000
    return st


def build_feed_checks(feed) -> pd.DataFrame:
    checks = []

    orphan_trips = set(feed.trips["trip_id"]) - set(feed.stop_times["trip_id"])
    checks.append(("Viagens sem stop_times", len(orphan_trips), "ok" if not orphan_trips else "alerta"))

    visited = set(feed.stop_times["stop_id"])
    all_stops = set(feed.stops["stop_id"])
    unused = all_stops - visited
    checks.append(("Paradas sem visitas", len(unused), "ok" if not unused else "alerta"))

    routes_with_trips = set(feed.trips["route_id"])
    all_routes = set(feed.routes["route_id"])
    unused_routes = all_routes - routes_with_trips
    checks.append(("Rotas sem viagens", len(unused_routes), "ok" if not unused_routes else "alerta"))

    if feed.shapes is not None:
        shapes_used = set(feed.trips["shape_id"].dropna())
        shapes_defined = set(feed.shapes["shape_id"])
        missing_shapes = shapes_used - shapes_defined
        checks.append(("Shapes faltando", len(missing_shapes), "ok" if not missing_shapes else "alerta"))

    null_arr = int(feed.stop_times["arrival_time"].isna().sum())
    checks.append(("stop_times sem arrival_time", null_arr, "ok" if null_arr == 0 else "alerta"))

    null_dist = int(feed.stop_times["shape_dist_traveled"].isna().sum()) if "shape_dist_traveled" in feed.stop_times.columns else len(feed.stop_times)
    checks.append(("shape_dist_traveled nulo", null_dist, "ok" if null_dist == 0 else "alerta"))

    return pd.DataFrame(checks, columns=["Verificação", "Contagem", "Status"])
