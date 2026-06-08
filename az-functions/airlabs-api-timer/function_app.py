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
# API_KEY = os.getenv("AIRLABS_API_KEY_R")
API_KEY = os.getenv("AIRLABS_API_KEY_MS")


CONNECTION_STRING = os.getenv(
    "AZURE_STORAGE_CONNECTION_STRING_aerolakeblob"
)

CONTAINER_NAME = "container-for-airlabs-api-timer"

TIMEZONE = os.getenv("TIMEZONE", "Europe/Warsaw")
OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "json").lower()  # json | parquet


# ---------------- AIRPORTS (DICT) ----------------
# AIRPORTS = os.getenv("AIRPORTS", "KRK").split(",")
AIRPORTS = {
    "KRK": "Krakow"
}


# ---------------- HELPERS ----------------
def save_json(container, blob_name: str, data: dict):
    container.upload_blob(
        name=blob_name,
        data=json.dumps(data, ensure_ascii=False),
        overwrite=True
    )


def save_parquet(container, blob_name: str, data: dict):
    records = data.get("response", data)

    df = pd.json_normalize(records)

    buffer = BytesIO()
    df.to_parquet(buffer, index=False)
    buffer.seek(0)

    container.upload_blob(
        name=blob_name,
        data=buffer.getvalue(),
        overwrite=True
    )


# ---------------- CLIENT ----------------
def get_container():
    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container = blob_service.get_container_client(CONTAINER_NAME)

    try:
        container.create_container()
    except ResourceExistsError:
        pass

    return container


# ---------------- SCHEDULES ----------------
def fetch_and_save_schedules(iata: str):
    try:
        iata = iata.strip().upper()

        url = (
            f"https://airlabs.co/api/v9/schedules"
            f"?dep_iata={iata}"
            f"&api_key={API_KEY}"
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


# ---------------- FLIGHTS ----------------
def fetch_and_save_flights(iata: str, direction: str):
    try:
        iata = iata.strip().upper()

        if direction == "arrivals":
            url = (
                f"https://airlabs.co/api/v9/flights"
                f"?api_key={API_KEY}"
                f"&arr_iata={iata}"
            )
        else:
            url = (
                f"https://airlabs.co/api/v9/flights"
                f"?api_key={API_KEY}"
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


# ---------------- TIMER: SCHEDULES ----------------
@app.timer_trigger(
    schedule="0 0 * * * *",  # 1 h
    # schedule="0 */5 * * * *",  # 15 min
    arg_name="timer",
    run_on_startup=False
)
def schedules_timer(timer: func.TimerRequest):

    if timer.past_due:
        logging.info("Schedules timer is late")

    logging.info(f"AIRPORTS: {AIRPORTS}")
    logging.info(f"TIMEZONE: {TIMEZONE}")
    logging.info(f"OUTPUT_FORMAT: {OUTPUT_FORMAT}")

    for iata, city in AIRPORTS.items():
        fetch_and_save_schedules(iata)


# ---------------- TIMER: FLIGHTS ----------------
@app.timer_trigger(
    # schedule="0 */5 * * * *",  # 5 min
    schedule="0 */15 * * * *",  # 15 min
    arg_name="timer",
    run_on_startup=False
)
def flights_timer(timer: func.TimerRequest):

    if timer.past_due:
        logging.info("Flights timer is late")

    logging.info(f"AIRPORTS: {AIRPORTS}")
    logging.info(f"TIMEZONE: {TIMEZONE}")
    logging.info(f"OUTPUT_FORMAT: {OUTPUT_FORMAT}")
    logging.info("FUNCTION: flights_timer")

    for iata, city in AIRPORTS.items():

        fetch_and_save_flights(iata, "arrivals")
        fetch_and_save_flights(iata, "departures")