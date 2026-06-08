import logging

import azure.functions as func

from data_pipeline.data_processor import process_silver_layer

app = func.FunctionApp()


# Timer trigger executing every hour (15 minutes after the hour)
# Cron expression: 0 15 * * * *
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
