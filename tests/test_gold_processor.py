from datetime import datetime

import polars as pl

from data_pipeline.gold_processor import (
    build_active_routes,
    build_airline_performance,
    build_gold_datasets,
    build_hourly_traffic_patterns,
    build_schedule_flight_facts,
    build_weather_correlation,
    build_weather_delay_impact,
)


def make_schedules_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_code": ["LO3905", "FR123", "LO3905", "W6123"],
            "status": ["scheduled", "active", "scheduled", "scheduled"],
            "departure_airport": ["KRK", "KRK", "KRK", "KRK"],
            "arrival_airport": ["WAW", "STN", "WAW", "CDG"],
            "airline_code": ["LO", "FR", "LO", "W6"],
            "airline_name": ["LOT", "Ryanair", "LOT", "Wizz Air"],
            "scheduled_departure_utc": [
                datetime(2026, 6, 14, 10, 0),
                datetime(2026, 6, 14, 10, 30),
                datetime(2026, 6, 15, 10, 0),
                datetime(2026, 6, 14, 11, 0),
            ],
            "scheduled_arrival_utc": [
                datetime(2026, 6, 14, 11, 0),
                datetime(2026, 6, 14, 12, 45),
                datetime(2026, 6, 15, 11, 0),
                datetime(2026, 6, 14, 13, 0),
            ],
            "departure_delay_mins": [15, 20, 5, 0],
            "arrival_delay_mins": [10, 15, 0, 0],
        }
    )


def make_flights_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "flight_code": ["LO3905", "FR123", "LO3905", "W6123"],
            "reg_number": ["SP-LND", "SP-RZM", "SP-LND", "HA-LXA"],
            "icao_24bit_hex": ["48C300", "48C2AC", "48C300", "471F36"],
            "lat": [50.08, 50.05, 50.07, 50.03],
            "lng": [19.79, 19.80, 19.78, 19.81],
            "altitude_ft": [5000, 7000, 5200, 4500],
            "ground_speed_knt": [180.0, 220.0, 175.0, 200.0],
            "heading_deg": [270.0, 310.0, 269.0, 240.0],
            "status": ["departed", "departed", "departed", "departed"],
            "last_updated_utc": [
                datetime(2026, 6, 14, 10, 5),
                datetime(2026, 6, 14, 10, 40),
                datetime(2026, 6, 15, 10, 7),
                datetime(2026, 6, 14, 12, 15),
            ],
            "aircraft_manufacturer": ["Boeing", "Boeing", "Boeing", "Airbus"],
            "aircraft_model": ["737-800", "737 MAX 8", "737-800", "A321neo"],
            "aircraft_built_year": [2012, 2018, 2012, 2021],
            "aircraft_age_years": [14, 8, 14, 5],
        }
    )


def make_weather_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "airport_city": ["Krakow", "Krakow", "Krakow"],
            "observation_time_utc": [
                datetime(2026, 6, 14, 10, 0),
                datetime(2026, 6, 14, 10, 35),
                datetime(2026, 6, 15, 10, 10),
            ],
            "temp_celsius": [12.0, 18.0, 7.0],
            "feels_like_celsius": [11.0, 17.0, 6.0],
            "pressure_hpa": [1012, 1011, 1013],
            "humidity_percent": [55, 60, 70],
            "wind_speed_mps": [3.0, 5.0, 1.0],
            "cloudiness_percent": [20, 65, 90],
            "visibility_meters": [10000, 5000, 2000],
        }
    )


def make_airlines_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "iata_code": ["LO", "FR", "W6"],
            "name": ["LOT Polish Airlines", "Ryanair", "Wizz Air"],
        }
    )


def make_airports_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "iata_code": ["KRK", "WAW", "STN", "CDG"],
            "name": [
                "John Paul II Krakow-Balice",
                "Warsaw Chopin Airport",
                "London Stansted Airport",
                "Charles de Gaulle Airport",
            ],
            "lat": [50.0777, 52.1657, 51.8850, 49.0097],
            "lng": [19.7848, 20.9671, 0.2350, 2.5479],
        }
    )


def test_build_schedule_flight_facts_matches_repeated_flight_codes_by_nearest_departure():
    facts = build_schedule_flight_facts(make_schedules_df(), make_flights_df())

    assert facts.shape[0] == 4

    day_one_match = facts.filter(
        (pl.col("flight_code") == "LO3905")
        & (pl.col("scheduled_departure_utc") == datetime(2026, 6, 14, 10, 0))
    )
    day_two_match = facts.filter(
        (pl.col("flight_code") == "LO3905")
        & (pl.col("scheduled_departure_utc") == datetime(2026, 6, 15, 10, 0))
    )

    assert day_one_match["last_updated_utc"][0] == datetime(2026, 6, 14, 10, 5)
    assert day_two_match["last_updated_utc"][0] == datetime(2026, 6, 15, 10, 7)


def test_gold_aggregations_build_expected_outputs():
    schedules = make_schedules_df()
    flights = make_flights_df()
    weather = make_weather_df()
    airlines = make_airlines_df()
    airports = make_airports_df()

    facts = build_schedule_flight_facts(schedules, flights)
    weather_correlation = build_weather_correlation(facts, weather)
    airline_performance = build_airline_performance(facts, airlines)
    weather_impact = build_weather_delay_impact(weather_correlation)
    hourly_traffic = build_hourly_traffic_patterns(schedules)
    active_routes = build_active_routes(schedules, airports)
    gold_datasets = build_gold_datasets(schedules, flights, weather, airlines, airports)

    unmatched_weather = weather_correlation.filter(pl.col("flight_code") == "W6123")
    assert unmatched_weather["has_weather_match"][0] is False
    assert unmatched_weather["temp_celsius"][0] is None

    lot_row = airline_performance.filter(pl.col("airline_code") == "LO")
    assert lot_row["avg_departure_delay_mins"][0] == 10.0
    assert lot_row["total_flights"][0] == 2
    assert lot_row["airline_name"][0] == "LOT Polish Airlines"

    temp_bucket = weather_impact.filter(
        (pl.col("metric") == "temperature_celsius") & (pl.col("bucket_label") == "10-15 C")
    )
    visibility_bucket = weather_impact.filter(
        (pl.col("metric") == "visibility_level") & (pl.col("bucket_label") == "medium")
    )
    assert temp_bucket["avg_departure_delay_mins"][0] == 15.0
    assert temp_bucket["flight_count"][0] == 1
    assert visibility_bucket["avg_departure_delay_mins"][0] == 20.0

    ten_oclock = hourly_traffic.filter(pl.col("departure_hour_utc") == 10)
    assert ten_oclock["flight_count"][0] == 3

    waw_route = active_routes.filter(pl.col("arrival_airport") == "WAW")
    assert waw_route["flight_count"][0] == 2
    assert waw_route["arr_airport_name"][0] == "Warsaw Chopin Airport"

    assert set(gold_datasets) == {
        "active_routes",
        "airline_performance",
        "hourly_traffic",
        "weather_correlation",
        "weather_impact",
    }
