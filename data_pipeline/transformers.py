import polars as pl


def clean_and_transform_schedules(
    raw_df: pl.DataFrame, airports_df: pl.DataFrame, airlines_df: pl.DataFrame
) -> pl.DataFrame:
    """
    Cleans raw schedules data, deduplicates, and joins with airports and airlines dictionaries.
    """
    if raw_df.is_empty():
        return pl.DataFrame()

    df = raw_df.select(
        [
            pl.col("flight_iata").alias("flight_code"),
            pl.col("status").cast(pl.String),
            pl.col("dep_iata").alias("departure_airport"),
            pl.col("arr_iata").alias("arrival_airport"),
            pl.col("airline_iata").alias("airline_code"),
            pl.from_epoch(
                pl.col("dep_time_ts").cast(pl.Float64).cast(pl.Int64), time_unit="s"
            ).alias("scheduled_departure_utc"),
            pl.from_epoch(
                pl.col("arr_time_ts").cast(pl.Float64).cast(pl.Int64), time_unit="s"
            ).alias("scheduled_arrival_utc"),
            pl.col("dep_delayed")
            .cast(pl.Float64)
            .fill_null(0.0)
            .cast(pl.Int32)
            .alias("departure_delay_mins"),
            pl.col("arr_delayed")
            .cast(pl.Float64)
            .fill_null(0.0)
            .cast(pl.Int32)
            .alias("arrival_delay_mins"),
        ]
    )

    df = df.drop_nulls(subset=["flight_code", "departure_airport", "arrival_airport"])

    # Deduplicate: keep only the latest scheduled entry per flight code and departure time
    df = df.unique(subset=["flight_code", "scheduled_departure_utc"], keep="last")

    # 1. Join with Departure Airport Coordinates
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

    # 2. Join with Arrival Airport Coordinates
    if not airports_df.is_empty():
        arr_airports = airports_df.select(
            [
                pl.col("iata_code").alias("arrival_airport"),
                pl.col("name").alias("arr_airport_name"),
                pl.col("lat").alias("arr_lat"),
                pl.col("lng").alias("arr_lng"),
            ]
        )
        df = df.join(arr_airports, on="arrival_airport", how="left")

    # 3. Join with Airlines details
    if not airlines_df.is_empty():
        airlines = airlines_df.select(
            [
                pl.col("iata_code").alias("airline_code"),
                pl.col("name").alias("airline_name"),
            ]
        )
        df = df.join(airlines, on="airline_code", how="left")

    assert not df.is_empty(), "Data quality error: Cleaned schedules DataFrame is empty."
    assert df["flight_code"].null_count() == 0, "Data quality error: flight_code contains nulls."

    return df


def clean_and_transform_flights(raw_df: pl.DataFrame, fleets_df: pl.DataFrame) -> pl.DataFrame:
    """
    Cleans raw live departures/flights data, joins with fleets dictionary to enrich aircraft metadata.
    """
    if raw_df.is_empty():
        return pl.DataFrame()

    df = raw_df.select(
        [
            pl.col("flight_iata").alias("flight_code"),
            pl.col("reg_number"),
            pl.col("hex").alias("icao_24bit_hex"),
            pl.col("lat").cast(pl.Float64),
            pl.col("lng").cast(pl.Float64),
            pl.col("alt").cast(pl.Float64).cast(pl.Int64).alias("altitude_ft"),
            pl.col("speed").cast(pl.Float64).alias("ground_speed_knt"),
            pl.col("dir").cast(pl.Float64).alias("heading_deg"),
            pl.col("status").cast(pl.String),
            pl.col("airline_iata").alias("airline_code"),
            pl.from_epoch(pl.col("updated").cast(pl.Float64).cast(pl.Int64), time_unit="s").alias(
                "last_updated_utc"
            ),
        ]
    )

    df = df.drop_nulls(subset=["flight_code", "reg_number"])
    df = df.unique(subset=["flight_code", "last_updated_utc"], keep="last")

    # Join with Fleets dictionary to get detailed plane metadata
    if not fleets_df.is_empty():
        planes = fleets_df.select(
            [
                pl.col("reg_number"),
                pl.col("manufacturer").alias("aircraft_manufacturer"),
                pl.col("model").alias("aircraft_model"),
                pl.col("built").alias("aircraft_built_year"),
                pl.col("age").alias("aircraft_age_years"),
            ]
        ).unique(subset=["reg_number"])  # Ensure uniqueness for join

        df = df.join(planes, on="reg_number", how="left")

    assert not df.is_empty(), "Data quality error: Cleaned flights DataFrame is empty."
    return df


def clean_and_transform_weather(raw_df: pl.DataFrame) -> pl.DataFrame:
    """
    Cleans raw weather logs.
    """
    if raw_df.is_empty():
        return pl.DataFrame()

    # Extract weather parameters
    df = raw_df.select(
        [
            pl.col("name").alias("airport_city"),
            pl.from_epoch(pl.col("dt").cast(pl.Float64).cast(pl.Int64), time_unit="s").alias(
                "observation_time_utc"
            ),
            pl.col("main.temp").cast(pl.Float64).alias("temp_celsius"),
            pl.col("main.feels_like").cast(pl.Float64).alias("feels_like_celsius"),
            pl.col("main.pressure").cast(pl.Float64).cast(pl.Int32).alias("pressure_hpa"),
            pl.col("main.humidity").cast(pl.Float64).cast(pl.Int32).alias("humidity_percent"),
            pl.col("wind.speed").cast(pl.Float64).alias("wind_speed_mps"),
            pl.col("clouds.all").cast(pl.Float64).cast(pl.Int32).alias("cloudiness_percent"),
            pl.col("visibility").cast(pl.Float64).cast(pl.Int32).alias("visibility_meters"),
        ]
    )

    # Deduplicate weather readings on observation time
    df = df.unique(subset=["airport_city", "observation_time_utc"], keep="last")

    assert not df.is_empty(), "Data quality error: Cleaned weather DataFrame is empty."
    return df
