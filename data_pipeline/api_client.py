import logging
import os

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def fetch_airlabs_schedules(limit: int = 5, dep_iata: str = "KRK") -> list[dict]:
    """
    Fetches planned flight data (schedules) from the AirLabs API.

    The function sends a GET request to the /schedules endpoint, authenticating with
    the API key defined in environment variables. It filters results by departure
    airport to minimize the size of downloaded data.

    Args:
        limit (int, optional): Maximum number of records to return. Defaults to 5.
        dep_iata (str, optional): Three-letter departure airport IATA code,
            e.g., 'KRK' for Kraków. Defaults to "KRK".
            full list on: https://airlabs.co/docs/schedules

    Returns:
        list[dict]: A list of dictionaries, each representing a single flight.
            Returns an empty list if no data is found.

    Raises:
        ValueError: If the AIRLABS_API_KEY is not found in the environment variables.
        requests.exceptions.RequestException: In case of a network or HTTP error.
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


if __name__ == "__main__":
    try:
        logging.info("Starting local test for AirLabs API client...")
        sample_data = fetch_airlabs_schedules(limit=5, dep_iata="KRK")
        logging.info(f"Test successful. Fetched {len(sample_data)} records.")
    except Exception as err:
        logging.error(f"Local test failed: {err}")
