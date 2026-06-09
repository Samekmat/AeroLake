import json
import logging
import os
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import azure.functions as func
import pandas as pd
import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

# ---------------- CONFIG ----------------
API_KEY = os.getenv("OPENWEATHER_API_KEY_R")

CONNECTION_STRING = os.getenv(
    "AZURE_STORAGE_CONNECTION_STRING_aerolakeblob"
)

CONTAINER_NAME = "open-weather-map"

TIMEZONE = os.getenv(
    "TIMEZONE",
    "Europe/Warsaw"
)

OUTPUT_FORMAT = os.getenv(
    "OUTPUT_FORMAT",
    "json"
).lower()

# ---------------- CITIES ----------------
CITIES = {
    "KRK": "Krakow,PL"
}

# ---------------- HELPERS ----------------
def save_json(container, blob_name: str, data: dict):

    container.upload_blob(
        name=blob_name,
        data=json.dumps(data, ensure_ascii=False),
        overwrite=True
    )


def save_parquet(container, blob_name: str, data: dict):

    df = pd.json_normalize(data)

    buffer = BytesIO()

    df.to_parquet(
        buffer,
        index=False
    )

    buffer.seek(0)

    container.upload_blob(
        name=blob_name,
        data=buffer.getvalue(),
        overwrite=True
    )


# ---------------- WEATHER ----------------
def fetch_and_save_weather(
    airport_code: str,
    city_name: str
):

    try:

        weather_url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={city_name}"
            f"&appid={API_KEY}"
            f"&units=metric"
        )

        response = requests.get(
            weather_url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        blob_service = (
            BlobServiceClient.from_connection_string(
                CONNECTION_STRING
            )
        )

        container = blob_service.get_container_client(
            CONTAINER_NAME
        )

        try:
            container.create_container()

        except ResourceExistsError:
            pass

        now = datetime.now(
            ZoneInfo(TIMEZONE)
        )

        base_path = (
            f"weather/{airport_code}/"
            f"{now:%Y/%m/%d/%H%M%S}"
        )

        if OUTPUT_FORMAT == "parquet":

            blob_name = (
                base_path + ".parquet"
            )

            save_parquet(
                container,
                blob_name,
                data
            )

            logging.info(
                f"Saved PARQUET: {blob_name}"
            )

        else:

            blob_name = (
                base_path + ".json"
            )

            save_json(
                container,
                blob_name,
                data
            )

            logging.info(
                f"Saved JSON: {blob_name}"
            )

    except Exception as e:

        logging.error(
            f"Weather error ({airport_code}): {e}"
        )


# ---------------- TIMER ----------------
@app.timer_trigger(
    schedule="0 0 * * * *", 
    # schedule="0 */5 * * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False
)
def city_weather(
    myTimer: func.TimerRequest
) -> None:

    if myTimer.past_due:

        logging.info(
            "The timer is past due!"
        )

    logging.info(
        "Weather timer executed"
    )

    logging.info(
        f"TIMEZONE: {TIMEZONE}"
    )

    logging.info(
        f"OUTPUT_FORMAT: {OUTPUT_FORMAT}"
    )

    for airport_code, city_name in CITIES.items():

        fetch_and_save_weather(
            airport_code,
            city_name
        )