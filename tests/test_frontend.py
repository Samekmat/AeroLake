from datetime import UTC, date, datetime

import polars as pl
import pytest

from frontend.data_loader import (
    bucket_flight_positions,
    build_flight_track,
    compute_delays_by_hour,
    compute_weekday_patterns,
    filter_dataframe_by_date,
    filter_dataframe_by_today,
    hive_partition_prefix,
    iter_dates,
    partition_load_range,
    pick_flight_track,
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

    assert result.height == 2
    assert set(result.columns) >= {
        "flight_code",
        "departure_airport",
        "arrival_airport",
        "dep_airport_name",
        "last_updated_utc",
        "airline_name",
    }
    ba123 = result.filter(pl.col("flight_code") == "BA123").row(0, named=True)
    assert ba123["dep_airport_name"] == "Heathrow"
    assert ba123["departure_airport"] == "LHR"
    assert ba123["last_updated_utc"] == datetime(2025, 6, 1, 12, 20)


def test_transform_raw_arrivals_returns_empty_for_missing_updated(sample_airports):
    raw_df = pl.DataFrame({"flight_iata": ["BA123"], "dep_iata": ["LHR"]})

    assert transform_raw_arrivals(raw_df, sample_airports).is_empty()


def test_transform_raw_arrivals_returns_empty_for_empty_input(sample_airports):
    assert transform_raw_arrivals(pl.DataFrame(), sample_airports).is_empty()


def test_transform_raw_arrivals_keeps_latest_snapshot(sample_airports):
    raw_df = pl.DataFrame(
        {
            "flight_iata": ["BA123", "BA123"],
            "dep_iata": ["LHR", "LHR"],
            "arr_iata": ["KRK", "KRK"],
            "status": ["en-route", "landed"],
            "airline_iata": ["BA", "BA"],
            "lat": [51.0, 51.5],
            "lng": [10.0, 10.5],
            "updated": [1748780400, 1748780400],
        }
    )

    result = transform_raw_arrivals(raw_df, sample_airports)

    assert result.height == 1
    assert result["lat"][0] == 51.5
    assert result["status"][0] == "landed"


@pytest.mark.parametrize(
    ("start_date", "end_date", "expected_codes"),
    [
        (date(2025, 6, 2), date(2025, 6, 30), {"W6123"}),
        (date(2025, 6, 1), date(2025, 6, 1), {"FR123", "LO3905"}),
        (date(2025, 6, 3), date(2025, 6, 30), set()),
    ],
    ids=["from_second_day", "single_day", "after_last_flight"],
)
def test_filter_dataframe_by_date(sample_schedules, start_date, end_date, expected_codes):
    filtered = filter_dataframe_by_date(
        sample_schedules, "scheduled_departure_utc", start_date, end_date
    )

    assert set(filtered["flight_code"].to_list()) == expected_codes


def test_filter_dataframe_by_date_timezone_aware_utc():
    df = pl.DataFrame(
        {
            "flight_code": ["A", "B", "C"],
            "scheduled_departure_utc": [
                datetime(2025, 6, 1, 8, 0, tzinfo=UTC),
                datetime(2025, 6, 10, 12, 0, tzinfo=UTC),
                datetime(2025, 6, 15, 18, 0, tzinfo=UTC),
            ],
        }
    )

    filtered = filter_dataframe_by_date(
        df, "scheduled_departure_utc", date(2025, 6, 10), date(2025, 6, 12)
    )

    assert filtered.height == 1
    assert filtered["flight_code"][0] == "B"


@pytest.mark.parametrize(
    ("utc_time", "local_date", "included"),
    [
        (datetime(2025, 6, 9, 22, 30, tzinfo=UTC), date(2025, 6, 10), True),
        (datetime(2025, 6, 10, 21, 30, tzinfo=UTC), date(2025, 6, 10), True),
        (datetime(2025, 6, 10, 22, 30, tzinfo=UTC), date(2025, 6, 10), False),
    ],
    ids=["utc_evening_becomes_next_local_day", "late_local_same_day", "utc_late_crosses_midnight"],
)
def test_filter_dataframe_by_date_warsaw_timezone_boundary(utc_time, local_date, included):
    df = pl.DataFrame(
        {
            "flight_code": ["EDGE"],
            "scheduled_departure_utc": [utc_time],
        }
    )

    filtered = filter_dataframe_by_date(
        df, "scheduled_departure_utc", local_date, local_date, tz="Europe/Warsaw"
    )

    assert not filtered.is_empty() if included else filtered.is_empty()


def test_filter_dataframe_by_date_invalid_range_returns_empty(sample_schedules):
    filtered = filter_dataframe_by_date(
        sample_schedules, "scheduled_departure_utc", date(2025, 6, 5), date(2025, 6, 1)
    )

    assert filtered.is_empty()


def test_filter_dataframe_by_date_empty_input():
    assert filter_dataframe_by_date(
        pl.DataFrame(), "scheduled_departure_utc", date(2025, 6, 1), date(2025, 6, 2)
    ).is_empty()


def test_filter_dataframe_by_today(sample_schedules):
    filtered = filter_dataframe_by_today(
        sample_schedules, "scheduled_departure_utc", on_date=date(2025, 6, 1)
    )

    assert set(filtered["flight_code"].to_list()) == {"FR123", "LO3905"}


@pytest.mark.parametrize(
    ("start", "end", "expected_load_start", "expected_load_end", "expected_days"),
    [
        (
            date(2025, 6, 1),
            date(2025, 6, 3),
            date(2025, 5, 31),
            date(2025, 6, 4),
            [date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)],
        ),
    ],
)
def test_partition_helpers(start, end, expected_load_start, expected_load_end, expected_days):
    load_start, load_end = partition_load_range(start, end)
    assert load_start == expected_load_start
    assert load_end == expected_load_end
    assert list(iter_dates(start, end)) == expected_days
    assert hive_partition_prefix("schedules", start) == "schedules/year=2025/month=06/day=01/"


def test_compute_delays_by_hour(sample_schedules):
    result = compute_delays_by_hour(sample_schedules)

    assert result.height == 3
    assert result["departure_hour_utc"].to_list() == [8, 14, 20]

    hour_8 = result.filter(pl.col("departure_hour_utc") == 8).row(0, named=True)
    assert hour_8["avg_delay_mins"] == 10.0
    assert hour_8["flight_count"] == 1


def test_compute_delays_by_hour_empty_input():
    assert compute_delays_by_hour(pl.DataFrame()).is_empty()


def test_compute_weekday_patterns(sample_schedules, sample_arrivals_raw, sample_airports):
    arrivals = transform_raw_arrivals(sample_arrivals_raw, sample_airports)
    result = compute_weekday_patterns(sample_schedules, arrivals)

    assert "Z Krakowa" in result["direction"].to_list()
    assert "Do Krakowa" in result["direction"].to_list()
    assert "avg_flights_per_day" in result.columns

    departures = result.filter(pl.col("direction") == "Z Krakowa")
    sunday = departures.filter(pl.col("weekday") == 7).row(0, named=True)
    assert sunday["flight_count"] == 2
    assert sunday["avg_flights_per_day"] == 2.0

    monday = departures.filter(pl.col("weekday") == 1).row(0, named=True)
    assert monday["flight_count"] == 1
    assert monday["avg_flights_per_day"] == 1.0


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

    assert {"time_bucket", "time_label"}.issubset(result.columns)
    fr1 = result.filter(pl.col("flight_code") == "FR1").row(0, named=True)
    assert fr1["lat"] == 50.2
    assert fr1["time_label"] == "2025-06-01 10:00"


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
    assert track["lat"].to_list() == [52.0, 52.1]
    assert track["flight_type"].unique().to_list() == ["Odlot z KRK"]


def test_build_flight_track_matches_case_insensitive_flight_code():
    positions = pl.DataFrame(
        {
            "flight_code": ["fr1"],
            "lat": [50.1],
            "lng": [19.8],
            "last_updated_utc": [datetime(2025, 6, 1, 10, 0)],
        }
    )

    track = build_flight_track(positions, "FR1", "Odlot z KRK")

    assert track.height == 1


def test_pick_flight_track_prefers_most_recent_leg():
    flights_df = pl.DataFrame(
        {
            "flight_code": ["FR1"],
            "lat": [50.0],
            "lng": [19.0],
            "last_updated_utc": [datetime(2025, 6, 1, 8, 0)],
        }
    )
    arrivals_df = pl.DataFrame(
        {
            "flight_code": ["FR1"],
            "lat": [51.0],
            "lng": [20.0],
            "last_updated_utc": [datetime(2025, 6, 10, 9, 0)],
        }
    )

    track = pick_flight_track(flights_df, arrivals_df, "FR1")

    assert track.height == 1
    assert track["flight_type"][0] == "Przylot do KRK"
    assert track["lat"][0] == 51.0


def test_pick_flight_track_returns_empty_when_no_positions():
    assert pick_flight_track(pl.DataFrame(), pl.DataFrame(), "FR1").is_empty()
