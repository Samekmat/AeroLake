import logging

import azure.functions as func

from data_pipeline.data_processor import process_silver_layer
from data_pipeline.ingest_airlabs import run_flights_ingestion, run_schedules_ingestion
from data_pipeline.ingest_weather import run_weather_ingestion

app = func.FunctionApp()


# 1. Schedules Ingestion: runs every hour on the hour
# Cron: 0 0 * * * *
@app.timer_trigger(
    schedule="0 0 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False
)
def schedules_timer_trigger(timer: func.TimerRequest) -> None:
    logging.info("Executing Airlabs Schedules Ingestion Trigger...")
    if timer.past_due:
        logging.warning("Schedules trigger is running past due.")
    run_schedules_ingestion()


# 2. Flights Ingestion: runs every 15 minutes
# Cron: 0 */15 * * * *
@app.timer_trigger(
    schedule="0 */15 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False
)
def flights_timer_trigger(timer: func.TimerRequest) -> None:
    logging.info("Executing Airlabs Flights Ingestion Trigger...")
    if timer.past_due:
        logging.warning("Flights trigger is running past due.")
    run_flights_ingestion()


# 3. Weather Ingestion: runs every hour on the hour
# Cron: 0 0 * * * *
@app.timer_trigger(
    schedule="0 0 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False
)
def weather_timer_trigger(timer: func.TimerRequest) -> None:
    logging.info("Executing OpenWeather Ingestion Trigger...")
    if timer.past_due:
        logging.warning("Weather trigger is running past due.")
    run_weather_ingestion()


# 4. Silver Layer Processing: runs every hour, 15 minutes after the hour
# Cron: 0 15 * * * *
@app.timer_trigger(
    schedule="0 15 * * * *", arg_name="timer", run_on_startup=False, use_monitor=False
)
def silver_layer_timer_trigger(timer: func.TimerRequest) -> None:
    logging.info("Executing AeroLake Silver Layer Data Pipeline (Timer Trigger)...")
    if timer.past_due:
        logging.warning("The timer is running past due.")

    try:
        process_silver_layer()
        logging.info("AeroLake Silver Layer Data Pipeline executed successfully.")
    except Exception as e:
        logging.error(f"Critical failure in AeroLake Silver Layer execution: {e}")
        raise
