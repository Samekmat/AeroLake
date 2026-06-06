import polars as pl

from data_pipeline.transformers import (
    clean_and_transform_flights,
    clean_and_transform_schedules,
    clean_and_transform_weather,
)


def test_clean_and_transform_schedules():
    # Mock raw schedules DataFrame
    raw_schedules = pl.DataFrame(
        {
            "flight_iata": ["LO3905", "LO3905", "FR123"],
            "status": ["active", "active", "scheduled"],
            "dep_iata": ["KRK", "KRK", "WAW"],
            "arr_iata": ["WAW", "WAW", "STN"],
            "airline_iata": ["LO", "LO", "FR"],
            "dep_time_ts": [1780233000, 1780233000, 1780233600],
            "arr_time_ts": [1780236600, 1780236600, 1780240000],
            "dep_delayed": [15.0, 15.0, None],
            "arr_delayed": [10.0, 10.0, None],
        }
    )

    # Mock airports dictionary
    airports = pl.DataFrame(
        {
            "iata_code": ["KRK", "WAW", "STN"],
            "name": ["Krakow Airport", "Warsaw Chopin Airport", "London Stansted Airport"],
            "lat": [50.076, 52.167, 51.885],
            "lng": [19.792, 20.967, 0.235],
            "country_code": ["PL", "PL", "GB"],
        }
    )

    # Mock airlines dictionary
    airlines = pl.DataFrame(
        {
            "iata_code": ["LO", "FR"],
            "name": ["LOT Polish Airlines", "Ryanair"],
        }
    )

    # Perform cleaning
    cleaned = clean_and_transform_schedules(raw_schedules, airports, airlines)

    assert not cleaned.is_empty()
    # Deduplication check
    assert cleaned.shape[0] == 2  # 3 rows, but one duplicate LO3905
    assert "dep_airport_name" in cleaned.columns
    assert "airline_name" in cleaned.columns
    # Delay check
    assert cleaned.filter(pl.col("flight_code") == "FR123")["departure_delay_mins"][0] == 0


def test_clean_and_transform_flights():
    raw_flights = pl.DataFrame(
        {
            "flight_iata": ["FR123", "LO3905"],
            "reg_number": ["SP-RZM", "SP-LND"],
            "hex": ["48C2AC", "48C300"],
            "lat": [50.0, 52.0],
            "lng": [19.0, 20.0],
            "alt": [30000, 10000],
            "speed": [450.0, 250.0],
            "dir": [90.0, 180.0],
            "status": ["en-route", "en-route"],
            "airline_iata": ["FR", "LO"],
            "updated": [1780251296, 1780251300],
        }
    )

    fleets = pl.DataFrame(
        {
            "reg_number": ["SP-RZM"],
            "manufacturer": ["BOEING"],
            "model": ["Boeing 737 MAX 8"],
            "built": [2018],
            "age": [8],
        }
    )

    cleaned = clean_and_transform_flights(raw_flights, fleets)

    assert not cleaned.is_empty()
    assert cleaned.shape[0] == 2
    assert "aircraft_model" in cleaned.columns
    assert (
        cleaned.filter(pl.col("flight_code") == "FR123")["aircraft_model"][0] == "Boeing 737 MAX 8"
    )
    assert cleaned.filter(pl.col("flight_code") == "LO3905")["aircraft_model"][0] is None


def test_clean_and_transform_weather():
    raw_weather = pl.DataFrame(
        {
            "name": ["Krakow", "Krakow"],
            "dt": [1780436502, 1780436502],
            "main.temp": [16.56, 16.56],
            "main.feels_like": [15.37, 15.37],
            "main.pressure": [1010, 1010],
            "main.humidity": [42, 42],
            "wind.speed": [1.69, 1.69],
            "clouds.all": [99, 99],
            "visibility": [10000, 10000],
        }
    )

    cleaned = clean_and_transform_weather(raw_weather)

    assert not cleaned.is_empty()
    assert cleaned.shape[0] == 1
    assert cleaned["temp_celsius"][0] == 16.56
