import json
import logging
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import polars as pl
import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

from core.config import Config
from data_pipeline.polars_helpers import flatten_dict

TIMEZONE = "Europe/Warsaw"
OUTPUT_FORMAT = "parquet"
CONTAINER_NAME = Config.RAW_CONTAINER
AIRPORTS = {"KRK": "Krakow"}


def save_json(container, blob_name: str, data: dict):
    container.upload_blob(name=blob_name, data=json.dumps(data, ensure_ascii=False), overwrite=True)


def save_parquet(container, blob_name: str, data: dict):
    records = data.get("response", data)
    if not records:
        records = []
    flat_records = [flatten_dict(r) for r in records] if records else []
    df = pl.DataFrame(flat_records)
    buffer = BytesIO()
    df.write_parquet(buffer)
    buffer.seek(0)
    container.upload_blob(name=blob_name, data=buffer.getvalue(), overwrite=True)


def get_container():
    if not Config.AZURE_STORAGE_CONNECTION_STRING:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING is missing.")
    blob_service = BlobServiceClient.from_connection_string(Config.AZURE_STORAGE_CONNECTION_STRING)
    container = blob_service.get_container_client(CONTAINER_NAME)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    return container


def fetch_and_save_schedules(iata: str):
    try:
        iata = iata.strip().upper()
        url = (
            f"https://airlabs.co/api/v9/schedules?dep_iata={iata}&api_key={Config.AIRLABS_API_KEY}"
        )
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        container = get_container()
        now = datetime.now(ZoneInfo(TIMEZONE))
        base_path = f"schedules/{iata}/{now:%Y/%m/%d/%H%M%S}"

        if OUTPUT_FORMAT == "parquet":
            blob_name = base_path + ".parquet"
            save_parquet(container, blob_name, data)
            logging.info(f"Saved PARQUET: {blob_name}")
        else:
            blob_name = base_path + ".json"
            save_json(container, blob_name, data)
            logging.info(f"Saved JSON: {blob_name}")
    except Exception as e:
        logging.error(f"Schedules error ({iata}): {e}")


def fetch_and_save_flights(iata: str, direction: str):
    try:
        iata = iata.strip().upper()
        if direction == "arrivals":
            url = (
                f"https://airlabs.co/api/v9/flights"
                f"?api_key={Config.AIRLABS_API_KEY}"
                f"&arr_iata={iata}"
            )
        else:
            url = (
                f"https://airlabs.co/api/v9/flights"
                f"?api_key={Config.AIRLABS_API_KEY}"
                f"&dep_iata={iata}"
            )
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        container = get_container()
        now = datetime.now(ZoneInfo(TIMEZONE))
        base_path = f"flights/{direction}/{iata}/{now:%Y/%m/%d/%H%M%S}"

        if OUTPUT_FORMAT == "parquet":
            blob_name = base_path + ".parquet"
            save_parquet(container, blob_name, data)
            logging.info(f"Saved PARQUET: {direction} {iata}: {blob_name}")
        else:
            blob_name = base_path + ".json"
            save_json(container, blob_name, data)
            logging.info(f"Saved JSON: {direction} {iata}: {blob_name}")
    except Exception as e:
        logging.error(f"Flights error ({iata}, {direction}): {e}")


def run_schedules_ingestion():
    logging.info(f"Starting schedules ingestion for: {AIRPORTS}")
    for iata in AIRPORTS.keys():
        fetch_and_save_schedules(iata)


def run_flights_ingestion():
    logging.info(f"Starting flights ingestion for: {AIRPORTS}")
    for iata in AIRPORTS.keys():
        fetch_and_save_flights(iata, "arrivals")
        fetch_and_save_flights(iata, "departures")
