import logging

import polars as pl

FLIGHT_SCHEDULE_MATCH_TOLERANCE = "12h"
WEATHER_MATCH_TOLERANCE = "30m"


def _optional_column(df: pl.DataFrame, column_name: str, dtype: pl.DataType) -> pl.Expr:
    if column_name in df.columns:
        return pl.col(column_name)
    return pl.lit(None, dtype=dtype).alias(column_name)


def build_schedule_flight_facts(
    schedules_df: pl.DataFrame,
    flights_df: pl.DataFrame,
    flight_match_tolerance: str = FLIGHT_SCHEDULE_MATCH_TOLERANCE,
) -> pl.DataFrame:
    """
    Matches each scheduled departure with the nearest live flight update for the same flight code.
    """
    if schedules_df.is_empty() or flights_df.is_empty():
        return pl.DataFrame()

    schedules = schedules_df.sort(["flight_code", "scheduled_departure_utc"])
    flights = flights_df.select(
        [
            "flight_code",
            "last_updated_utc",
            _optional_column(flights_df, "reg_number", pl.String),
            _optional_column(flights_df, "icao_24bit_hex", pl.String),
            (
                pl.col("lat").alias("live_lat")
                if "lat" in flights_df.columns
                else pl.lit(None, dtype=pl.Float64).alias("live_lat")
            ),
            (
                pl.col("lng").alias("live_lng")
                if "lng" in flights_df.columns
                else pl.lit(None, dtype=pl.Float64).alias("live_lng")
            ),
            _optional_column(flights_df, "altitude_ft", pl.Int64),
            _optional_column(flights_df, "ground_speed_knt", pl.Float64),
            _optional_column(flights_df, "heading_deg", pl.Float64),
            (
                pl.col("status").alias("live_status")
                if "status" in flights_df.columns
                else pl.lit(None, dtype=pl.String).alias("live_status")
            ),
            _optional_column(flights_df, "aircraft_manufacturer", pl.String),
            _optional_column(flights_df, "aircraft_model", pl.String),
            _optional_column(flights_df, "aircraft_built_year", pl.Int64),
            _optional_column(flights_df, "aircraft_age_years", pl.Int64),
        ]
    ).sort(["flight_code", "last_updated_utc"])

    # 12h tolerance keeps the daily recurring flight code aligned
    # with the proper departure instance.
    return schedules.join_asof(
        flights,
        left_on="scheduled_departure_utc",
        right_on="last_updated_utc",
        by="flight_code",
        strategy="nearest",
        tolerance=flight_match_tolerance,
    )


def build_weather_correlation(
    schedule_flight_facts_df: pl.DataFrame,
    weather_df: pl.DataFrame,
    weather_match_tolerance: str = WEATHER_MATCH_TOLERANCE,
) -> pl.DataFrame:
    """
    Correlates matched flight departures with the closest weather observation in KRK.
    """
    if schedule_flight_facts_df.is_empty() or weather_df.is_empty():
        return pl.DataFrame()

    flights = schedule_flight_facts_df.filter(pl.col("last_updated_utc").is_not_null()).sort(
        "last_updated_utc"
    )
    weather = weather_df.sort("observation_time_utc")

    if flights.is_empty():
        return pl.DataFrame()

    correlated = flights.join_asof(
        weather,
        left_on="last_updated_utc",
        right_on="observation_time_utc",
        strategy="nearest",
        tolerance=weather_match_tolerance,
    )

    return correlated.with_columns(
        pl.col("observation_time_utc").is_not_null().alias("has_weather_match")
    )


def build_airline_performance(
    schedule_flight_facts_df: pl.DataFrame, airlines_df: pl.DataFrame
) -> pl.DataFrame:
    """
    Calculates average departure delay and total observed flights per airline.
    """
    if schedule_flight_facts_df.is_empty():
        return pl.DataFrame()

    base = schedule_flight_facts_df.filter(
        pl.col("last_updated_utc").is_not_null() & pl.col("airline_code").is_not_null()
    )

    if base.is_empty():
        return pl.DataFrame()

    if not airlines_df.is_empty():
        airline_names = airlines_df.select(
            [
                pl.col("iata_code").alias("airline_code"),
                pl.col("name").alias("airline_name"),
            ]
        ).unique(subset=["airline_code"], keep="last")
        if "airline_name" in base.columns:
            base = base.drop("airline_name")
        base = base.join(airline_names, on="airline_code", how="left")

    return (
        base.group_by(["airline_code", "airline_name"])
        .agg(
            [
                pl.col("departure_delay_mins")
                .mean()
                .round(2)
                .alias("avg_departure_delay_mins"),
                pl.len().alias("total_flights"),
            ]
        )
        .sort(["total_flights", "airline_code"], descending=[True, False])
    )


def build_weather_delay_impact(weather_correlation_df: pl.DataFrame) -> pl.DataFrame:
    """
    Aggregates average delay by temperature, wind speed, and visibility brackets.
    """
    if weather_correlation_df.is_empty():
        return pl.DataFrame()

    base = weather_correlation_df.filter(
        pl.col("has_weather_match") & pl.col("departure_delay_mins").is_not_null()
    )

    if base.is_empty():
        return pl.DataFrame()

    temp_base = base.with_columns(
        ((pl.col("temp_celsius") / 5).floor() * 5).alias("bucket_start")
    ).with_columns(
        [
            pl.lit("temperature_celsius").alias("metric"),
            (pl.col("bucket_start") + 5).alias("bucket_end"),
            pl.format(
                "{}-{} C",
                pl.col("bucket_start").cast(pl.Int64),
                (pl.col("bucket_start") + 5).cast(pl.Int64),
            ).alias("bucket_label"),
        ]
    )

    wind_base = base.with_columns(
        ((pl.col("wind_speed_mps") / 2).floor() * 2).alias("bucket_start")
    ).with_columns(
        [
            pl.lit("wind_speed_mps").alias("metric"),
            (pl.col("bucket_start") + 2).alias("bucket_end"),
            pl.format(
                "{}-{} m/s",
                pl.col("bucket_start").cast(pl.Int64),
                (pl.col("bucket_start") + 2).cast(pl.Int64),
            ).alias("bucket_label"),
        ]
    )

    visibility_base = base.with_columns(
        [
            pl.lit("visibility_level").alias("metric"),
            pl.lit(None, dtype=pl.Float64).alias("bucket_start"),
            pl.lit(None, dtype=pl.Float64).alias("bucket_end"),
            pl.when(pl.col("visibility_meters") < 3_000)
            .then(pl.lit("low"))
            .when(pl.col("visibility_meters") < 8_000)
            .then(pl.lit("medium"))
            .otherwise(pl.lit("high"))
            .alias("bucket_label"),
        ]
    )

    def aggregate(df: pl.DataFrame) -> pl.DataFrame:
        return (
            df.group_by(["metric", "bucket_label", "bucket_start", "bucket_end"])
            .agg(
                [
                    pl.col("departure_delay_mins")
                    .mean()
                    .round(2)
                    .alias("avg_departure_delay_mins"),
                    pl.len().alias("flight_count"),
                ]
            )
            .sort(["metric", "bucket_start", "bucket_label"])
        )

    return pl.concat(
        [aggregate(temp_base), aggregate(wind_base), aggregate(visibility_base)],
        how="vertical",
    )


def build_hourly_traffic_patterns(schedules_df: pl.DataFrame) -> pl.DataFrame:
    """
    Calculates hourly departure volumes to highlight congestion windows.
    """
    if schedules_df.is_empty():
        return pl.DataFrame()

    return (
        schedules_df.with_columns(
            pl.col("scheduled_departure_utc").dt.hour().alias("departure_hour_utc")
        )
        .group_by("departure_hour_utc")
        .agg(pl.len().alias("flight_count"))
        .sort("departure_hour_utc")
    )


def build_active_routes(schedules_df: pl.DataFrame, airports_df: pl.DataFrame) -> pl.DataFrame:
    """
    Builds unique KRK departure routes enriched with origin and destination coordinates.
    """
    if schedules_df.is_empty() or airports_df.is_empty():
        return pl.DataFrame()

    dep_airports = airports_df.select(
        [
            pl.col("iata_code").alias("departure_airport"),
            pl.col("lat").cast(pl.Float64).alias("dep_lat"),
            pl.col("lng").cast(pl.Float64).alias("dep_lng"),
        ]
    ).unique(subset=["departure_airport"], keep="last")

    arr_airports = airports_df.select(
        [
            pl.col("iata_code").alias("arrival_airport"),
            pl.col("name").alias("arr_airport_name"),
            pl.col("lat").cast(pl.Float64).alias("arr_lat"),
            pl.col("lng").cast(pl.Float64).alias("arr_lng"),
        ]
    ).unique(subset=["arrival_airport"], keep="last")

    routes = (
        schedules_df.filter(
            (pl.col("departure_airport") == "KRK") & pl.col("arrival_airport").is_not_null()
        )
        .select(
            [
                "departure_airport",
                "arrival_airport",
                "scheduled_departure_utc",
            ]
        )
        .join(dep_airports, on="departure_airport", how="left")
        .join(arr_airports, on="arrival_airport", how="left")
        .drop_nulls(["dep_lat", "dep_lng", "arr_lat", "arr_lng"])
    )

    return (
        routes.group_by(
            [
                "departure_airport",
                "arrival_airport",
                "dep_lat",
                "dep_lng",
                "arr_airport_name",
                "arr_lat",
                "arr_lng",
            ]
        )
        .agg(
            [
                pl.len().alias("flight_count"),
                pl.col("scheduled_departure_utc").max().alias("last_scheduled_departure_utc"),
            ]
        )
        .sort(["flight_count", "arrival_airport"], descending=[True, False])
    )


def build_gold_datasets(
    schedules_df: pl.DataFrame,
    flights_df: pl.DataFrame,
    weather_df: pl.DataFrame,
    airlines_df: pl.DataFrame,
    airports_df: pl.DataFrame,
) -> dict[str, pl.DataFrame]:
    """
    Builds all Gold layer datasets required by the analytical frontend.
    """
    schedule_flight_facts_df = build_schedule_flight_facts(schedules_df, flights_df)
    weather_correlation_df = build_weather_correlation(schedule_flight_facts_df, weather_df)

    return {
        "weather_correlation": weather_correlation_df,
        "airline_performance": build_airline_performance(schedule_flight_facts_df, airlines_df),
        "weather_impact": build_weather_delay_impact(weather_correlation_df),
        "hourly_traffic": build_hourly_traffic_patterns(schedules_df),
        "active_routes": build_active_routes(schedules_df, airports_df),
    }


def process_gold_layer():
    """
    Reads Silver datasets from Azure Blob Storage, builds Gold tables, and uploads them back.
    """
    from core.config import Config
    from data_pipeline.azure_io import (
        get_blob_service_client,
        read_parquet_blobs_to_dataframe,
        upload_dataframe_to_blob,
    )

    logging.info("Initializing AeroLake Gold Layer pipeline...")
    client = get_blob_service_client()

    logging.info("Loading Silver layer datasets from Azure Blob Storage...")
    schedules_df = read_parquet_blobs_to_dataframe(client, Config.CLEAN_CONTAINER, "schedules/")
    flights_df = read_parquet_blobs_to_dataframe(client, Config.CLEAN_CONTAINER, "flights/")
    weather_df = read_parquet_blobs_to_dataframe(client, Config.CLEAN_CONTAINER, "weather/")
    airlines_df = read_parquet_blobs_to_dataframe(
        client, Config.CLEAN_CONTAINER, "dictionaries/airlines.parquet"
    )
    airports_df = read_parquet_blobs_to_dataframe(
        client, Config.CLEAN_CONTAINER, "dictionaries/airports.parquet"
    )

    gold_datasets = build_gold_datasets(
        schedules_df=schedules_df,
        flights_df=flights_df,
        weather_df=weather_df,
        airlines_df=airlines_df,
        airports_df=airports_df,
    )

    container_client = client.get_container_client(Config.CLEAN_CONTAINER)
    for dataset_name, dataset_df in gold_datasets.items():
        upload_dataframe_to_blob(container_client, dataset_df, f"gold/{dataset_name}.parquet")

    logging.info("AeroLake Gold Layer pipeline completed successfully!")


if __name__ == "__main__":
    process_gold_layer()
