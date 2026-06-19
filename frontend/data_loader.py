from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl
import streamlit as st

from core.config import Config
from data_pipeline.azure_io import (
    get_blob_service_client,
    read_parquet_blobs_to_dataframe,
    read_raw_blobs_to_dataframe,
)
from data_pipeline.polars_helpers import align_dataframe_schemas

DATA_START_DATE = date(2026, 5, 31)
LOCAL_TZ = ZoneInfo("Europe/Warsaw")
KRK_LAT = 50.0777
KRK_LNG = 19.7848
CACHE_TTL_SECONDS = 3600
MAX_DOWNLOAD_WORKERS = 8

GOLD_DATASETS = (
    "active_routes",
    "airline_performance",
    "weather_impact",
)


@st.cache_resource
def get_cached_blob_client():
    Config.validate_storage()
    return get_blob_service_client()


def iter_dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def partition_load_range(start_date: date, end_date: date) -> tuple[date, date]:
    """Expands the requested range by one day to cover timezone boundaries."""
    return start_date - timedelta(days=1), end_date + timedelta(days=1)


def hive_partition_prefix(base_prefix: str, day: date) -> str:
    return f"{base_prefix}/year={day.year}/month={day.month:02d}/day={day.day:02d}/"


def raw_arrivals_day_prefix(day: date) -> str:
    return f"flights/arrivals/KRK/{day.year}/{day.month:02d}/{day.day:02d}/"


def _concat_frames(dfs: list[pl.DataFrame]) -> pl.DataFrame:
    if not dfs:
        return pl.DataFrame()
    if len(dfs) == 1:
        return dfs[0]
    return pl.concat(align_dataframe_schemas(dfs), how="vertical")


def _load_frames_in_parallel(load_day_fn, days: list[date]) -> list[pl.DataFrame]:
    if not days:
        return []

    frames: list[pl.DataFrame] = []
    if len(days) == 1:
        day_frame = load_day_fn(days[0])
        if not day_frame.is_empty():
            frames.append(day_frame)
        return frames

    with ThreadPoolExecutor(max_workers=min(MAX_DOWNLOAD_WORKERS, len(days))) as executor:
        futures = [executor.submit(load_day_fn, day) for day in days]
        for future in as_completed(futures):
            day_frame = future.result()
            if not day_frame.is_empty():
                frames.append(day_frame)
    return frames


def _read_partition_range(start_date: date, end_date: date, load_day) -> pl.DataFrame:
    load_start, load_end = partition_load_range(start_date, end_date)
    days = list(iter_dates(load_start, load_end))
    return _concat_frames(_load_frames_in_parallel(load_day, days))


def read_silver_partition_range(
    client,
    base_prefix: str,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    def load_day(day: date) -> pl.DataFrame:
        return read_parquet_blobs_to_dataframe(
            client, Config.CLEAN_CONTAINER, hive_partition_prefix(base_prefix, day)
        )

    return _read_partition_range(start_date, end_date, load_day)


def read_raw_arrivals_partition_range(
    client,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    def load_day(day: date) -> pl.DataFrame:
        return read_raw_blobs_to_dataframe(
            client, Config.RAW_CONTAINER, raw_arrivals_day_prefix(day)
        )

    return _read_partition_range(start_date, end_date, load_day)


def transform_raw_arrivals(raw_df: pl.DataFrame, airports_df: pl.DataFrame) -> pl.DataFrame:
    """Cleans raw arrival snapshots for the frontend arrivals table."""
    if raw_df.is_empty():
        return pl.DataFrame()

    updated_col = "updated" if "updated" in raw_df.columns else None
    if updated_col is None:
        return pl.DataFrame()

    df = raw_df.select(
        [
            pl.col("flight_iata").alias("flight_code"),
            pl.col("dep_iata").alias("departure_airport"),
            pl.col("arr_iata").alias("arrival_airport"),
            pl.col("status").cast(pl.String),
            pl.col("airline_iata").alias("airline_code"),
            pl.col("lat").cast(pl.Float64),
            pl.col("lng").cast(pl.Float64),
            pl.from_epoch(pl.col(updated_col).cast(pl.Float64).cast(pl.Int64), time_unit="s").alias(
                "last_updated_utc"
            ),
        ]
    )

    df = df.drop_nulls(subset=["flight_code", "last_updated_utc"])

    if not airports_df.is_empty():
        dep_airports = airports_df.select(
            [
                pl.col("iata_code").alias("departure_airport"),
                pl.col("name").alias("dep_airport_name"),
                pl.col("lat").alias("dep_lat"),
                pl.col("lng").alias("dep_lng"),
            ]
        )
        df = df.join(dep_airports, on="departure_airport", how="left")

    if "airline_name" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("airline_name"))

    return df.unique(subset=["flight_code", "last_updated_utc"], keep="last")


def _local_date_expr(date_column: str, tz: str) -> pl.Expr:
    return (
        pl.col(date_column)
        .cast(pl.Datetime(time_unit="us", time_zone="UTC"))
        .dt.convert_time_zone(tz)
        .dt.date()
    )


def filter_dataframe_by_date(
    df: pl.DataFrame,
    date_column: str,
    start_date: date,
    end_date: date,
    tz: str = "Europe/Warsaw",
) -> pl.DataFrame:
    """Filters a DataFrame by inclusive local calendar date range."""
    if df is None or df.is_empty() or date_column not in df.columns:
        return df if df is not None else pl.DataFrame()

    if start_date > end_date:
        return df.head(0)

    return df.filter(_local_date_expr(date_column, tz).is_between(start_date, end_date))


def filter_dataframe_by_today(
    df: pl.DataFrame,
    date_column: str,
    on_date: date | None = None,
    tz: str = "Europe/Warsaw",
) -> pl.DataFrame:
    """Filters a DataFrame to a single local calendar day."""
    target_date = on_date or datetime.now(ZoneInfo(tz)).date()
    return filter_dataframe_by_date(df, date_column, target_date, target_date, tz)


def compute_delays_by_hour(schedules_df: pl.DataFrame) -> pl.DataFrame:
    """Aggregates average departure delay by scheduled hour (UTC)."""
    if schedules_df.is_empty():
        return pl.DataFrame()

    return (
        schedules_df.with_columns(
            pl.col("scheduled_departure_utc").dt.hour().alias("departure_hour_utc")
        )
        .group_by("departure_hour_utc")
        .agg(
            [
                pl.col("departure_delay_mins").mean().round(2).alias("avg_delay_mins"),
                pl.len().alias("flight_count"),
            ]
        )
        .sort("departure_hour_utc")
    )


def _weekday_stats_from_timestamp(
    df: pl.DataFrame, timestamp_col: str, direction: str
) -> pl.DataFrame:
    if df.is_empty() or timestamp_col not in df.columns:
        return pl.DataFrame()

    daily = (
        df.with_columns(
            pl.col(timestamp_col).dt.weekday().alias("weekday"),
            pl.col(timestamp_col).dt.date().alias("flight_date"),
        )
        .group_by("weekday", "flight_date")
        .agg(pl.len().alias("daily_count"))
    )

    return (
        daily.group_by("weekday")
        .agg(
            pl.col("daily_count").sum().alias("flight_count"),
            pl.col("daily_count").mean().alias("avg_flights_per_day"),
        )
        .with_columns(pl.lit(direction).alias("direction"))
    )


def compute_weekday_patterns(schedules_df: pl.DataFrame, arrivals_df: pl.DataFrame) -> pl.DataFrame:
    """Flight counts by weekday: total sum and average per calendar day."""
    weekday_labels = {
        1: "Poniedziałek",
        2: "Wtorek",
        3: "Środa",
        4: "Czwartek",
        5: "Piątek",
        6: "Sobota",
        7: "Niedziela",
    }

    frames = [
        _weekday_stats_from_timestamp(schedules_df, "scheduled_departure_utc", "Z Krakowa"),
        _weekday_stats_from_timestamp(arrivals_df, "last_updated_utc", "Do Krakowa"),
    ]
    frames = [frame for frame in frames if not frame.is_empty()]

    if not frames:
        return pl.DataFrame()

    result = pl.concat(frames, how="vertical")
    return result.with_columns(
        pl.col("weekday").replace_strict(weekday_labels, default="Nieznany").alias("weekday_label")
    ).sort(["direction", "weekday"])


def bucket_flight_positions(flights_df: pl.DataFrame, bucket_minutes: int = 15) -> pl.DataFrame:
    """Buckets live flight positions; one row per flight and time window (latest snapshot)."""
    if flights_df.is_empty():
        return pl.DataFrame()

    bucketed = (
        flights_df.drop_nulls(subset=["lat", "lng", "last_updated_utc"])
        .with_columns(
            pl.col("last_updated_utc").dt.truncate(f"{bucket_minutes}m").alias("time_bucket")
        )
        .sort(["flight_code", "time_bucket", "last_updated_utc"])
    )

    group_cols = ["flight_code", "time_bucket"]
    agg_exprs = [
        pl.col("lat").last(),
        pl.col("lng").last(),
        pl.col("last_updated_utc").last(),
    ]
    if "flight_type" in bucketed.columns:
        agg_exprs.append(pl.col("flight_type").last())
    if "heading_deg" in bucketed.columns:
        agg_exprs.append(pl.col("heading_deg").last())

    return (
        bucketed.group_by(group_cols)
        .agg(agg_exprs)
        .with_columns(pl.col("time_bucket").dt.strftime("%Y-%m-%d %H:%M").alias("time_label"))
        .sort("time_bucket")
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1_r, lon1_r, lat2_r, lon2_r = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def _filter_position_jumps(df: pl.DataFrame, max_km: float) -> pl.DataFrame:
    if df.is_empty() or df.height <= 1:
        return df

    sorted_df = df.sort("last_updated_utc")
    rows = sorted_df.select(["lat", "lng"]).to_dicts()
    keep_indices = [0]
    for index in range(1, len(rows)):
        prev = rows[keep_indices[-1]]
        cur = rows[index]
        distance = _haversine_km(prev["lat"], prev["lng"], cur["lat"], cur["lng"])
        if distance <= max_km:
            keep_indices.append(index)
    return sorted_df.with_row_index("_row").filter(pl.col("_row").is_in(keep_indices)).drop("_row")


def _latest_flight_segment(df: pl.DataFrame, max_gap_hours: float) -> pl.DataFrame:
    if df.is_empty():
        return df

    segmented = (
        df.sort("last_updated_utc")
        .with_columns(pl.col("last_updated_utc").diff().dt.total_hours().alias("gap_hours"))
        .with_columns(pl.col("gap_hours").fill_null(0.0).alias("gap_hours"))
        .with_columns(
            (pl.col("gap_hours") > max_gap_hours).cast(pl.Int64).cum_sum().alias("segment_id")
        )
    )
    latest_segment = segmented["segment_id"].max()
    return segmented.filter(pl.col("segment_id") == latest_segment).drop(
        ["gap_hours", "segment_id"]
    )


def build_flight_track(
    positions_df: pl.DataFrame,
    flight_code: str,
    flight_type: str,
    *,
    bucket_minutes: int = 15,
    max_gap_hours: float = 15.0,
    max_leg_jump_km: float = 400.0,
) -> pl.DataFrame:
    """
    Builds a single chronological track for the most recent flight leg of a flight code.
    Prevents mixing positions from different days/instances of the same flight number.
    """
    if positions_df.is_empty() or "flight_code" not in positions_df.columns:
        return pl.DataFrame()

    normalized_code = flight_code.strip().upper()
    track = (
        positions_df.filter(pl.col("flight_code").str.to_uppercase() == normalized_code)
        .drop_nulls(subset=["lat", "lng", "last_updated_utc"])
        .filter((pl.col("lat").abs() > 0.01) | (pl.col("lng").abs() > 0.01))
        .filter(pl.col("lat").is_between(-90, 90) & pl.col("lng").is_between(-180, 180))
        .unique(subset=["flight_code", "last_updated_utc", "lat", "lng"], keep="last")
    )
    if track.is_empty():
        return pl.DataFrame()

    track = _latest_flight_segment(track, max_gap_hours=max_gap_hours)
    track = _filter_position_jumps(track, max_km=max_leg_jump_km)
    if track.is_empty():
        return pl.DataFrame()

    track = track.with_columns(pl.lit(flight_type).alias("flight_type"))
    columns = ["flight_code", "lat", "lng", "last_updated_utc", "flight_type"]
    if "heading_deg" in track.columns:
        columns.append("heading_deg")
    return bucket_flight_positions(track.select(columns), bucket_minutes=bucket_minutes)


def pick_flight_track(
    flights_df: pl.DataFrame,
    arrivals_df: pl.DataFrame,
    flight_code: str,
    *,
    bucket_minutes: int = 15,
) -> pl.DataFrame:
    """Prefers the dataset with the most recent completed leg for the flight code."""
    candidates: list[pl.DataFrame] = []
    for df, label in ((flights_df, "Odlot z KRK"), (arrivals_df, "Przylot do KRK")):
        track = build_flight_track(df, flight_code, label, bucket_minutes=bucket_minutes)
        if not track.is_empty():
            candidates.append(track)

    if not candidates:
        return pl.DataFrame()
    if len(candidates) == 1:
        return candidates[0]

    return max(
        candidates,
        key=lambda frame: frame.select(pl.col("last_updated_utc").max()).item(),
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _load_airports_dictionary() -> pl.DataFrame:
    client = get_cached_blob_client()
    return read_parquet_blobs_to_dataframe(
        client, Config.CLEAN_CONTAINER, "dictionaries/airports.parquet"
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _load_airlines_dictionary() -> pl.DataFrame:
    client = get_cached_blob_client()
    return read_parquet_blobs_to_dataframe(
        client, Config.CLEAN_CONTAINER, "dictionaries/airlines.parquet"
    )


def _enrich_arrivals_with_airlines(arrivals_df: pl.DataFrame) -> pl.DataFrame:
    airlines_df = _load_airlines_dictionary()
    if arrivals_df.is_empty() or airlines_df.is_empty():
        return arrivals_df

    airline_names = airlines_df.select(
        [
            pl.col("iata_code").alias("airline_code"),
            pl.col("name").alias("airline_name"),
        ]
    ).unique(subset=["airline_code"], keep="last")

    if "airline_name" in arrivals_df.columns:
        arrivals_df = arrivals_df.drop("airline_name")
    return arrivals_df.join(airline_names, on="airline_code", how="left")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Wczytywanie rozkladu lotów...")
def load_schedules():
    client = get_cached_blob_client()
    today = datetime.now(LOCAL_TZ).date()
    return read_silver_partition_range(client, "schedules", DATA_START_DATE, today)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Wczytywanie pozycji live...")
def load_live_flights():
    client = get_cached_blob_client()
    today = datetime.now(LOCAL_TZ).date()
    return read_silver_partition_range(client, "flights", DATA_START_DATE, today)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Wczytywanie przylotów...")
def load_arrivals():
    client = get_cached_blob_client()
    today = datetime.now(LOCAL_TZ).date()
    raw_df = read_raw_arrivals_partition_range(client, DATA_START_DATE, today)
    arrivals_df = transform_raw_arrivals(raw_df, _load_airports_dictionary())
    return _enrich_arrivals_with_airlines(arrivals_df)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_gold(dataset_name: str):
    if dataset_name not in GOLD_DATASETS:
        raise ValueError(f"Unknown gold dataset: {dataset_name}")

    client = get_cached_blob_client()
    return read_parquet_blobs_to_dataframe(
        client, Config.CLEAN_CONTAINER, f"gold/{dataset_name}.parquet"
    )


def clear_cache():
    """Clears Streamlit caches so the dashboard can fetch fresh data."""
    st.cache_data.clear()
    st.cache_resource.clear()
