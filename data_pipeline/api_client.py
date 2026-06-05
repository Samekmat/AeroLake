import logging
import os

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def fetch_airlabs_schedules(limit: int = 100, dep_iata: str = "KRK") -> list[dict]:
    """
    Fetches planned flight data (schedules) from the AirLabs API.
    """
    load_dotenv()
    api_key = os.getenv("AIRLABS_API_KEY")

    if not api_key:
        raise ValueError(
            "Critical Error: AIRLABS_API_KEY missing from environment variables (.env)."
        )

    url = "https://airlabs.co/api/v9/schedules"
    params = {
        "api_key": api_key,
        "dep_iata": dep_iata,
    }

    logging.info(f"Querying AirLabs API for departure airport: {dep_iata}...")

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json().get("response", [])
        data_limited = data[:limit]

        logging.info(
            f"Successfully fetched {len(data_limited)} records (limited from {len(data)} total)."
        )
        return data_limited

    except requests.exceptions.RequestException as e:
        logging.error(f"Error communicating with AirLabs API: {e}")
        raise


def fetch_airlabs_airports(iata_code: str = None) -> list[dict]:
    """
    Fetches airports dictionary from the AirLabs API.
    If iata_code is provided, fetches only that specific airport.
    """
    load_dotenv()
    api_key = os.getenv("AIRLABS_API_KEY")
    if not api_key:
        raise ValueError("AIRLABS_API_KEY missing from environment variables.")

    url = "https://airlabs.co/api/v9/airports"
    params = {"api_key": api_key}
    if iata_code:
        params["iata_code"] = iata_code

    logging.info(f"Querying AirLabs API for airports (iata_code={iata_code})...")
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json().get("response", [])
        logging.info(f"Successfully fetched {len(data)} airport records.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching airports from AirLabs API: {e}")
        raise


def fetch_airlabs_fleets(airline_iata: str = None) -> list[dict]:
    """
    Fetches fleets dictionary from the AirLabs API.
    If airline_iata is provided, fetches fleets for that specific airline.
    """
    load_dotenv()
    api_key = os.getenv("AIRLABS_API_KEY")
    if not api_key:
        raise ValueError("AIRLABS_API_KEY missing from environment variables.")

    url = "https://airlabs.co/api/v9/fleets"
    params = {"api_key": api_key}
    if airline_iata:
        params["airline_iata"] = airline_iata

    logging.info(f"Querying AirLabs API for fleets (airline_iata={airline_iata})...")
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json().get("response", [])
        logging.info(f"Successfully fetched {len(data)} fleet records.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching fleets from AirLabs API: {e}")
        raise


def fetch_airlabs_airlines(iata_code: str = None) -> list[dict]:
    """
    Fetches airlines dictionary from the AirLabs API.
    If iata_code is provided, fetches that specific airline.
    """
    load_dotenv()
    api_key = os.getenv("AIRLABS_API_KEY")
    if not api_key:
        raise ValueError("AIRLABS_API_KEY missing from environment variables.")

    url = "https://airlabs.co/api/v9/airlines"
    params = {"api_key": api_key}
    if iata_code:
        params["iata_code"] = iata_code

    logging.info(f"Querying AirLabs API for airlines (iata_code={iata_code})...")
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json().get("response", [])
        logging.info(f"Successfully fetched {len(data)} airline records.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching airlines from AirLabs API: {e}")
        raise


if __name__ == "__main__":
    try:
        logging.info("Starting local test for AirLabs API client...")
        sample_data = fetch_airlabs_schedules(limit=5, dep_iata="KRK")
        logging.info(f"Test schedules successful. Fetched {len(sample_data)} records.")

        sample_airports = fetch_airlabs_airports(iata_code="KRK")
        logging.info(f"Test airports successful. Fetched {len(sample_airports)} records.")

        sample_fleets = fetch_airlabs_fleets(airline_iata="LO")
        logging.info(f"Test fleets successful. Fetched {len(sample_fleets)} records.")
    except Exception as err:
        logging.error(f"Local test failed: {err}")
