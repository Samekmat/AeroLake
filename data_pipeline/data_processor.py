import logging
from pathlib import Path

import polars as pl

from data_pipeline.api_client import fetch_airlabs_schedules

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def transform_schedules(raw_data: list[dict]) -> pl.DataFrame:
    """
    Transforms raw API dictionaries into a cleaned Polars DataFrame.

    Applies schema enforcement, timestamp parsing, and drops missing critical data.
    """
    if not raw_data:
        logging.warning("No raw data provided to Polars.")
        return pl.DataFrame()

    df = pl.DataFrame(raw_data)

    df_clean = df.select(
        [
            pl.col("flight_iata").alias("flight_code"),
            pl.col("status").cast(pl.Utf8),
            pl.col("dep_iata").alias("departure_airport"),
            pl.col("arr_iata").alias("arrival_airport"),
            pl.from_epoch(pl.col("dep_time_ts"), time_unit="s").alias("scheduled_departure_utc"),
            pl.from_epoch(pl.col("arr_time_ts"), time_unit="s").alias("scheduled_arrival_utc"),
            pl.col("dep_delayed").cast(pl.Int32).fill_null(0).alias("departure_delay_mins"),
            pl.col("arr_delayed").cast(pl.Int32).fill_null(0).alias("arrival_delay_mins"),
        ]
    )

    df_clean = df_clean.drop_nulls(subset=["departure_airport", "arrival_airport"])

    return df_clean


def save_to_parquet(df: pl.DataFrame, output_path: Path) -> None:
    """
    Saves a Polars DataFrame to a Parquet file (highly compressed binary format).
    """
    if df.is_empty():
        logging.warning("DataFrame is empty. Skipping Parquet save.")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    df.write_parquet(output_path)
    logging.info(f"Parquet file saved to: {output_path}")


if __name__ == "__main__":
    try:
        logging.info("Starting extraction...")
        raw_schedules = fetch_airlabs_schedules(limit=100, dep_iata="KRK")

        logging.info("Starting Polars transformation...")
        processed_df = transform_schedules(raw_schedules)

        print("Transformated table: ", processed_df.head(5))

        output_file = Path("data") / "schedules_raw.parquet"
        save_to_parquet(processed_df, output_file)

    except Exception as e:
        logging.error(f"Pipeline execution failed: {e}")
