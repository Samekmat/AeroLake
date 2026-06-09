import json
import logging
import os
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

from core.config import Config

TIMEZONE = "Europe/Warsaw"
OUTPUT_FORMAT = "parquet"
CONTAINER_NAME = Config.WEATHER_CONTAINER
CITIES = {"KRK": "Krakow,PL"}

API_KEY = os.getenv("OPENWEATHER_API_KEY_R") or os.getenv("OPENWEATHER_API_KEY")


def save_json(container, blob_name: str, data: dict):
    container.upload_blob(name=blob_name, data=json.dumps(data, ensure_ascii=False), overwrite=True)


def save_parquet(container, blob_name: str, data: dict):
    df = pd.json_normalize(data)
    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    buffer.seek(0)
    container.upload_blob(name=blob_name, data=buffer.getvalue(), overwrite=True)


def fetch_and_save_weather(airport_code: str, city_name: str):
    if not API_KEY:
        logging.error("Missing OPENWEATHER_API_KEY!")
        return

    try:
        weather_url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={city_name}"
            f"&appid={API_KEY}"
            f"&units=metric"
        )
        response = requests.get(weather_url, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not Config.AZURE_STORAGE_CONNECTION_STRING:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING is missing.")

        blob_service = BlobServiceClient.from_connection_string(
            Config.AZURE_STORAGE_CONNECTION_STRING
        )
        container = blob_service.get_container_client(CONTAINER_NAME)
        try:
            container.create_container()
        except ResourceExistsError:
            pass

        now = datetime.now(ZoneInfo(TIMEZONE))
        base_path = f"weather/{airport_code}/{now:%Y/%m/%d/%H%M%S}"

        if OUTPUT_FORMAT == "parquet":
            blob_name = base_path + ".parquet"
            save_parquet(container, blob_name, data)
            logging.info(f"Saved PARQUET: {blob_name}")
        else:
            blob_name = base_path + ".json"
            save_json(container, blob_name, data)
            logging.info(f"Saved JSON: {blob_name}")
    except Exception as e:
        logging.error(f"Weather error ({airport_code}): {e}")


def run_weather_ingestion():
    logging.info(f"Starting weather ingestion for: {CITIES}")
    for airport_code, city_name in CITIES.items():
        fetch_and_save_weather(airport_code, city_name)
