import io
import json
import logging

import polars as pl
from azure.storage.blob import BlobServiceClient

from core.config import Config
from data_pipeline.api_client import (
    fetch_airlabs_airlines,
    fetch_airlabs_airports,
    fetch_airlabs_fleets,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def get_blob_service_client() -> BlobServiceClient:
    """Initializes and returns the Azure BlobServiceClient."""
    conn_str = Config.AZURE_STORAGE_CONNECTION_STRING
    if not conn_str:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING is missing.")
    return BlobServiceClient.from_connection_string(conn_str)


def ensure_dictionary_exists(
    blob_service_client: BlobServiceClient,
    dictionary_name: str,
    fetch_func,
    *fetch_args,
    **fetch_kwargs,
) -> pl.DataFrame:
    """
    Ensures that a static dictionary exists in the clean-data container.
    If it doesn't exist, fetches it from the API, saves it to Azure as Parquet (Zero Disk I/O),
    and returns the DataFrame.
    """
    container_client = blob_service_client.get_container_client(Config.CLEAN_CONTAINER)
    blob_path = f"dictionaries/{dictionary_name}.parquet"
    blob_client = container_client.get_blob_client(blob_path)

    # 1. Try to read from Azure Blob Storage
    try:
        if blob_client.exists():
            logging.info(f"Loading dictionary '{dictionary_name}' from Azure Blob Storage cache...")
            buffer = io.BytesIO()
            blob_client.download_blob().readinto(buffer)
            buffer.seek(0)
            return pl.read_parquet(buffer)
    except Exception as e:
        logging.warning(f"Failed to read cached dictionary '{dictionary_name}': {e}. Re-fetching.")

    # 2. Fetch from API if not found or failed
    logging.info(f"Dictionary '{dictionary_name}' not cached. Fetching from AirLabs API...")
    raw_data = fetch_func(*fetch_args, **fetch_kwargs)

    if not raw_data:
        logging.warning(f"No data returned for dictionary '{dictionary_name}' from API.")
        return pl.DataFrame()

    df = pl.DataFrame(raw_data)

    # 3. Upload to Azure Blob Storage (Zero Disk I/O)
    try:
        buffer = io.BytesIO()
        df.write_parquet(buffer)
        buffer.seek(0)
        blob_client.upload_blob(buffer.getvalue(), overwrite=True)
        logging.info(f"Uploaded dictionary '{dictionary_name}' to Azure Blob Storage.")
    except Exception as e:
        logging.error(f"Failed to upload dictionary '{dictionary_name}' to storage: {e}")

    return df


def load_all_dictionaries(blob_service_client: BlobServiceClient):
    """Loads and caches all dictionaries from API to Azure Storage."""
    airports_df = ensure_dictionary_exists(blob_service_client, "airports", fetch_airlabs_airports)

    airlines_df = ensure_dictionary_exists(blob_service_client, "airlines", fetch_airlabs_airlines)

    # 3. Fleets Dictionary (aircraft information for Ryanair, Wizz Air, LOT, Lufthansa, etc.)
    # We fetch for common carriers at KRK to keep within API limits
    def fetch_combined_fleets():
        common_carriers = ["LO", "FR", "W6", "LH", "U2", "KL", "OS", "DY"]
        all_fleets = []
        for carrier in common_carriers:
            try:
                carrier_fleets = fetch_airlabs_fleets(airline_iata=carrier)
                if carrier_fleets:
                    all_fleets.extend(carrier_fleets)
            except Exception as e:
                logging.warning(f"Could not fetch fleet for carrier {carrier}: {e}")
        return all_fleets

    fleets_df = ensure_dictionary_exists(blob_service_client, "fleets", fetch_combined_fleets)

    return airports_df, airlines_df, fleets_df


def flatten_dict(d, parent_key="", sep="."):
    """Recursively flattens a nested dictionary by joining keys with `sep`."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def parse_parquet_blob(blob_name: str, buffer: io.BytesIO) -> pl.DataFrame | None:
    """Parses a raw Parquet blob and validates it doesn't contain API errors."""
    df_raw = pl.read_parquet(buffer)
    if any(col in df_raw.columns for col in ["error.message", "error.code", "error"]):
        logging.warning(f"Skipping file {blob_name} because it contains an API error message.")
        return None
    return df_raw


def parse_json_blob(blob_name: str, container_name: str, buffer: io.BytesIO) -> pl.DataFrame | None:
    """Parses a raw JSON blob and handles weather flattening vs flight extraction."""
    raw_bytes = buffer.getvalue()
    json_data = json.loads(raw_bytes.decode("utf-8"))

    if isinstance(json_data, dict):
        if (
            any(k in json_data for k in ["error", "error.message"])
            or json_data.get("cod") == "month_limit_exceeded"
        ):
            logging.warning(f"Skipping file {blob_name} because it contains an API error.")
            return None

    if "weather" in container_name or "weather" in blob_name:
        if isinstance(json_data, dict) and json_data.get("cod") not in [200, None]:
            logging.warning(
                f"Skipping weather file {blob_name} due to API error code: {json_data.get('cod')}"
            )
            return None
        return pl.DataFrame([flatten_dict(json_data)])

    payload = (
        json_data.get("response")
        if isinstance(json_data, dict) and "response" in json_data
        else json_data
    )
    if not payload:
        return None

    return pl.DataFrame(payload) if isinstance(payload, list) else pl.DataFrame([payload])


def parse_raw_blob(blob_name: str, container_name: str, buffer: io.BytesIO) -> pl.DataFrame | None:
    """Delegates parsing based on file format."""
    if blob_name.endswith(".parquet"):
        return parse_parquet_blob(blob_name, buffer)
    if blob_name.endswith(".json"):
        return parse_json_blob(blob_name, container_name, buffer)
    return None


def resolve_type_conflict(type1, type2):
    """Resolves type conflict between two Polars data types, returning the more general type."""
    if type1 == type2:
        return type1
    if type1 == pl.Int64 and type2 == pl.Float64:
        return pl.Float64
    if type1 == pl.Float64 and type2 == pl.Int64:
        return pl.Float64
    if type1 == pl.String or type2 == pl.String:
        return pl.String
    if type1.is_numeric() and type2.is_numeric():
        return pl.Float64
    return pl.String


def align_dataframe_schemas(dfs: list[pl.DataFrame]) -> list[pl.DataFrame]:
    """Aligns the columns and types of a list of Polars DataFrames so they can be concatenated."""
    if not dfs:
        return []

    unified_schema = {}
    for df in dfs:
        for col, dtype in df.schema.items():
            if col not in unified_schema:
                unified_schema[col] = dtype
            else:
                unified_schema[col] = resolve_type_conflict(unified_schema[col], dtype)

    standardized_dfs = []
    for df in dfs:
        select_exprs = []
        for col, target_dtype in unified_schema.items():
            if col in df.columns:
                select_exprs.append(pl.col(col).cast(target_dtype))
            else:
                select_exprs.append(pl.lit(None).cast(target_dtype).alias(col))
        standardized_dfs.append(df.select(select_exprs))

    return standardized_dfs


def read_raw_blobs_to_dataframe(
    blob_service_client: BlobServiceClient, container_name: str, prefix: str
) -> pl.DataFrame:
    """
    Downloads and combines raw blobs from a container under a prefix into a Polars DataFrame.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blobs = container_client.list_blobs(name_starts_with=prefix)

    dfs = []
    for b in blobs:
        if b.size == 0:
            continue

        try:
            blob_client = container_client.get_blob_client(b.name)
            buffer = io.BytesIO()
            blob_client.download_blob().readinto(buffer)
            buffer.seek(0)

            df_raw = parse_raw_blob(b.name, container_name, buffer)
            if df_raw is not None and not df_raw.is_empty():
                dfs.append(df_raw)
        except Exception as e:
            logging.error(f"Error reading blob {b.name}: {e}")

    if not dfs:
        logging.warning(f"No valid data frames loaded for prefix {prefix}")
        return pl.DataFrame()

    standardized_dfs = align_dataframe_schemas(dfs)
    return pl.concat(standardized_dfs)


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


def upload_partitioned_dataframe(
    container_client, df: pl.DataFrame, date_col: str, base_prefix: str
):
    """
    Partitions a DataFrame by year, month, and day derived from `date_col`,
    and uploads each partition to Azure Blob Storage in Hive format:
    base_prefix/year=YYYY/month=MM/day=DD/clean_data.parquet
    """
    if df.is_empty():
        logging.warning(f"DataFrame for prefix {base_prefix} is empty. Skipping partition upload.")
        return

    # Add year, month, day columns
    df_partitioned = df.with_columns(
        [
            pl.col(date_col).dt.year().alias("year"),
            pl.col(date_col).dt.month().alias("month"),
            pl.col(date_col).dt.day().alias("day"),
        ]
    )

    partitions = df_partitioned.partition_by(["year", "month", "day"])
    logging.info(f"Uploading {len(partitions)} partitions for base prefix '{base_prefix}'...")

    for part_df in partitions:
        if part_df.is_empty():
            continue

        row = part_df.head(1)
        year = row["year"][0]
        month = row["month"][0]
        day = row["day"][0]

        clean_part_df = part_df.drop(["year", "month", "day"])

        blob_path = (
            f"{base_prefix}/year={year}/month={month:02d}/day={day:02d}/clean_{base_prefix}.parquet"
        )

        buffer = io.BytesIO()
        clean_part_df.write_parquet(buffer)
        buffer.seek(0)

        blob_client = container_client.get_blob_client(blob_path)
        blob_client.upload_blob(buffer.getvalue(), overwrite=True)
        logging.info(
            f"Successfully uploaded partition: {blob_path} ({clean_part_df.shape[0]} rows)"
        )


def process_silver_layer():
    """Main function that handles the full Silver Layer processing pipeline."""
    logging.info("Initializing AeroLake Silver Layer pipeline...")
    client = get_blob_service_client()

    # 1. Load and cache dictionaries
    logging.info("Step 1: Loading dictionaries...")
    airports_df, airlines_df, fleets_df = load_all_dictionaries(client)

    # 2. Process schedules
    logging.info("Step 2: Processing raw flight schedules...")
    raw_schedules_df = read_raw_blobs_to_dataframe(client, Config.RAW_CONTAINER, "schedules/KRK/")
    clean_schedules_df = clean_and_transform_schedules(raw_schedules_df, airports_df, airlines_df)

    # 3. Process live departures/flights
    logging.info("Step 3: Processing live flight departures...")
    raw_flights_df = read_raw_blobs_to_dataframe(
        client, Config.RAW_CONTAINER, "flights/departures/KRK/"
    )
    clean_flights_df = clean_and_transform_flights(raw_flights_df, fleets_df)

    # 4. Process weather
    logging.info("Step 4: Processing weather observations...")
    raw_weather_df = read_raw_blobs_to_dataframe(client, Config.WEATHER_CONTAINER, "weather/KRK/")
    clean_weather_df = clean_and_transform_weather(raw_weather_df)

    # 5. Upload Silver Data (Zero Disk I/O, Hive Partitioned)
    logging.info("Step 5: Uploading cleaned partitioned parquet files to clean-data container...")
    container_client = client.get_container_client(Config.CLEAN_CONTAINER)

    upload_partitioned_dataframe(
        container_client,
        clean_schedules_df,
        date_col="scheduled_departure_utc",
        base_prefix="schedules",
    )

    upload_partitioned_dataframe(
        container_client, clean_flights_df, date_col="last_updated_utc", base_prefix="flights"
    )

    upload_partitioned_dataframe(
        container_client, clean_weather_df, date_col="observation_time_utc", base_prefix="weather"
    )

    logging.info("AeroLake Silver Layer pipeline completed successfully!")


if __name__ == "__main__":
    process_silver_layer()
