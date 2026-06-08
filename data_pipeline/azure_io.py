import io
import json
import logging

import polars as pl
from azure.storage.blob import BlobServiceClient

from api_client import (fetch_airlabs_airlines, fetch_airlabs_airports,
                        fetch_airlabs_fleets)
from config import Config
from polars_helpers import align_dataframe_schemas, flatten_dict


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

    # Fleets Dictionary
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


def upload_partitioned_dataframe(
    container_client, df: pl.DataFrame, date_col: str, base_prefix: str
):
    """
    Partitions a DataFrame by year, month, and day derived from `date_col`,
    and uploads each partition to Azure Blob Storage in Hive format.
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
