from datetime import datetime

import polars as pl
import pytest

from frontend.components.flights import _apply_airport_filter, _apply_text_filter
from frontend.components.map_view import _bearing_deg, _path_points, _track_map_bounds
from frontend.data_loader import KRK_LAT, KRK_LNG


@pytest.fixture
def sample_flights() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_code": ["FR123", "LO3905", "W6123"],
            "airline_name": ["Ryanair", "LOT", "Wizz Air"],
            "arrival_airport": ["STN", "WAW", "CDG"],
            "arr_airport_name": ["London Stansted", "Warsaw Chopin", "Paris CDG"],
        }
    )


def test_apply_text_filter_matches_case_insensitive(sample_flights):
    filtered = _apply_text_filter(sample_flights, "airline_name", "lot")

    assert filtered.height == 1
    assert filtered["flight_code"][0] == "LO3905"


def test_apply_text_filter_returns_original_when_value_empty(sample_flights):
    assert _apply_text_filter(sample_flights, "airline_name", "").equals(sample_flights)


def test_apply_airport_filter_matches_code_or_name(sample_flights):
    by_code = _apply_airport_filter(sample_flights, "arrival_airport", "arr_airport_name", "waw")
    by_name = _apply_airport_filter(
        sample_flights, "arrival_airport", "arr_airport_name", "stansted"
    )

    assert by_code["flight_code"].to_list() == ["LO3905"]
    assert by_name["flight_code"].to_list() == ["FR123"]


def test_apply_airport_filter_uses_code_only_when_name_column_missing(sample_flights):
    df = sample_flights.drop("arr_airport_name")

    filtered = _apply_airport_filter(df, "arrival_airport", "arr_airport_name", "cdg")

    assert filtered["flight_code"].to_list() == ["W6123"]


def test_track_map_bounds_includes_krk_and_padding():
    path_df = pl.DataFrame(
        {
            "lat": [50.2, 50.4],
            "lng": [19.7, 19.9],
        }
    )

    bounds = _track_map_bounds(path_df)

    assert bounds[0][0] < min(path_df["lat"].min(), KRK_LAT)
    assert bounds[1][0] > max(path_df["lat"].max(), KRK_LAT)
    assert bounds[0][1] < min(path_df["lng"].min(), KRK_LNG)
    assert bounds[1][1] > max(path_df["lng"].max(), KRK_LNG)


def test_bearing_deg_points_north_for_pure_latitude_move():
    bearing = _bearing_deg(50.0, 19.0, 51.0, 19.0)

    assert bearing == pytest.approx(0.0, abs=0.1)


def test_path_points_includes_labels_and_bearings():
    path_df = pl.DataFrame(
        {
            "lat": [50.0, 50.1],
            "lng": [19.0, 19.1],
            "time_bucket": [
                datetime(2025, 6, 1, 10, 0),
                datetime(2025, 6, 1, 10, 15),
            ],
            "time_label": ["2025-06-01 10:00", "2025-06-01 10:15"],
        }
    )

    points = _path_points(path_df)

    assert len(points) == 2
    assert points[0]["label"] == "2025-06-01 10:00"
    assert 0.0 <= points[0]["bearing"] <= 360.0
    assert points[1]["bearing"] > 0.0
