from datetime import date, datetime

import polars as pl
import pytest

from frontend.data_loader import (
    bucket_flight_positions,
    build_flight_track,
    compute_delays_by_hour,
    compute_weekday_patterns,
    filter_pandas_dataframe_by_date,
    hive_partition_prefix,
    iter_dates,
    partition_load_range,
    transform_raw_arrivals,
)


@pytest.fixture
def sample_schedules() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_code": ["FR123", "LO3905", "W6123"],
            "scheduled_departure_utc": [
                datetime(2025, 6, 1, 8, 0),
                datetime(2025, 6, 1, 14, 30),
                datetime(2025, 6, 2, 20, 15),
            ],
            "departure_delay_mins": [10, 25, 0],
        }
    )


@pytest.fixture
def sample_arrivals_raw() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_iata": ["BA123", "LH456"],
            "dep_iata": ["LHR", "FRA"],
            "arr_iata": ["KRK", "KRK"],
            "status": ["en-route", "landed"],
            "airline_iata": ["BA", "LH"],
            "lat": [51.5, 50.5],
            "lng": [10.0, 15.0],
            "updated": [1748780400, 1748784000],
        }
    )


@pytest.fixture
def sample_airports() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "iata_code": ["LHR", "FRA", "KRK"],
            "name": ["Heathrow", "Frankfurt", "Krakow"],
            "lat": [51.47, 50.03, 50.08],
            "lng": [-0.45, 8.57, 19.78],
        }
    )


def test_transform_raw_arrivals(sample_arrivals_raw, sample_airports):
    result = transform_raw_arrivals(sample_arrivals_raw, sample_airports)

    assert result.shape[0] == 2
    assert "flight_code" in result.columns
    assert "dep_airport_name" in result.columns
    assert result.filter(pl.col("flight_code") == "BA123")["dep_airport_name"][0] == "Heathrow"


def test_filter_pandas_dataframe_by_date(sample_schedules):
    pdf = sample_schedules.to_pandas()
    filtered = filter_pandas_dataframe_by_date(
        pdf, "scheduled_departure_utc", date(2025, 6, 2), date(2025, 6, 30)
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]["flight_code"] == "W6123"


def test_filter_pandas_excludes_before_start_date(sample_schedules):
    pdf = sample_schedules.to_pandas()
    filtered = filter_pandas_dataframe_by_date(
        pdf, "scheduled_departure_utc", date(2025, 6, 2), date(2025, 6, 30)
    )

    assert "FR123" not in filtered["flight_code"].tolist()
    assert "LO3905" not in filtered["flight_code"].tolist()


def test_filter_pandas_both_date_bounds(sample_schedules):
    pdf = sample_schedules.to_pandas()
    filtered = filter_pandas_dataframe_by_date(
        pdf, "scheduled_departure_utc", date(2025, 6, 1), date(2025, 6, 1)
    )

    assert len(filtered) == 2
    assert set(filtered["flight_code"].tolist()) == {"FR123", "LO3905"}


def test_filter_pandas_datetime64_utc():
    import pandas as pd

    pdf = pd.DataFrame(
        {
            "flight_code": ["A", "B", "C"],
            "scheduled_departure_utc": pd.to_datetime(
                [
                    "2025-06-01 08:00:00",
                    "2025-06-10 12:00:00",
                    "2025-06-15 18:00:00",
                ],
                utc=True,
            ),
        }
    )

    filtered = filter_pandas_dataframe_by_date(
        pdf, "scheduled_departure_utc", date(2025, 6, 10), date(2025, 6, 12)
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]["flight_code"] == "B"


def test_partition_helpers():
    start = date(2025, 6, 1)
    end = date(2025, 6, 3)
    load_start, load_end = partition_load_range(start, end)
    assert load_start == date(2025, 5, 31)
    assert load_end == date(2025, 6, 4)
    assert list(iter_dates(start, end)) == [start, date(2025, 6, 2), end]
    assert hive_partition_prefix("schedules", start) == "schedules/year=2025/month=06/day=01/"


def test_compute_delays_by_hour(sample_schedules):
    result = compute_delays_by_hour(sample_schedules)

    assert result.shape[0] == 3
    hour_8 = result.filter(pl.col("departure_hour_utc") == 8)
    assert hour_8["avg_delay_mins"][0] == 10.0


def test_compute_weekday_patterns(sample_schedules, sample_arrivals_raw, sample_airports):
    arrivals = transform_raw_arrivals(sample_arrivals_raw, sample_airports)
    result = compute_weekday_patterns(sample_schedules, arrivals)

    assert "Z Krakowa" in result["direction"].to_list()
    assert "Do Krakowa" in result["direction"].to_list()
    assert result.filter(pl.col("direction") == "Z Krakowa").shape[0] >= 1
    assert "avg_flights_per_day" in result.columns

    departures = result.filter(pl.col("direction") == "Z Krakowa")
    sunday = departures.filter(pl.col("weekday") == 7)
    assert sunday["flight_count"][0] == 2
    assert sunday["avg_flights_per_day"][0] == 2.0

    monday = departures.filter(pl.col("weekday") == 1)
    assert monday["flight_count"][0] == 1
    assert monday["avg_flights_per_day"][0] == 1.0


def test_bucket_flight_positions():
    flights = pl.DataFrame(
        {
            "flight_code": ["FR1", "FR1", "LO2"],
            "lat": [50.1, 50.2, 51.0],
            "lng": [19.8, 19.9, 20.0],
            "last_updated_utc": [
                datetime(2025, 6, 1, 10, 7),
                datetime(2025, 6, 1, 10, 12),
                datetime(2025, 6, 1, 10, 40),
            ],
        }
    )

    result = bucket_flight_positions(flights, bucket_minutes=15)

    assert "time_bucket" in result.columns
    assert "time_label" in result.columns
    fr1 = result.filter(pl.col("flight_code") == "FR1")
    assert fr1.shape[0] == 1
    assert fr1["lat"][0] == 50.2


def test_build_flight_track_keeps_latest_leg_only():
    positions = pl.DataFrame(
        {
            "flight_code": ["FR1", "FR1", "FR1", "FR1"],
            "lat": [50.0, 50.1, 52.0, 52.1],
            "lng": [19.0, 19.1, 21.0, 21.1],
            "last_updated_utc": [
                datetime(2025, 6, 1, 8, 0),
                datetime(2025, 6, 1, 8, 30),
                datetime(2025, 6, 10, 9, 0),
                datetime(2025, 6, 10, 9, 30),
            ],
        }
    )

    track = build_flight_track(positions, "FR1", "Odlot z KRK")

    assert track.height == 2
    assert track["lat"].min() >= 52.0
