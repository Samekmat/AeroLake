import logging

from core.config import Config
from data_pipeline.azure_io import (
    get_blob_service_client,
    load_all_dictionaries,
    read_raw_blobs_to_dataframe,
    upload_partitioned_dataframe,
)
from data_pipeline.transformers import (
    clean_and_transform_flights,
    clean_and_transform_schedules,
    clean_and_transform_weather,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


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
