import logging
import os

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load environment variables from .env file
load_dotenv()


class Config:
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AIRLABS_API_KEY = os.getenv("AIRLABS_API_KEY")

    RAW_CONTAINER = "container-for-airlabs-api-timer"
    CLEAN_CONTAINER = "clean-data"
    WEATHER_CONTAINER = "open-weather-map"

    @classmethod
    def validate(cls):
        """Validates that all required environment variables are set."""
        missing = []
        if not cls.AZURE_STORAGE_CONNECTION_STRING:
            missing.append("AZURE_STORAGE_CONNECTION_STRING")
        if not cls.AIRLABS_API_KEY:
            missing.append("AIRLABS_API_KEY")

        if missing:
            raise ValueError(
                f"Critical configuration error: Missing environment variables: {', '.join(missing)}"
            )
        logging.info("Configuration loaded and validated successfully.")
