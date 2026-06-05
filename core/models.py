from datetime import datetime

from pydantic import BaseModel, Field


class CleanSchedule(BaseModel):
    flight_code: str = Field(..., description="Flight code (IATA code)")
    status: str = Field(..., description="Status of the flight")
    departure_airport: str = Field(..., description="Departure airport IATA code")
    arrival_airport: str = Field(..., description="Arrival airport IATA code")
    scheduled_departure_utc: datetime = Field(..., description="Scheduled departure time in UTC")
    scheduled_arrival_utc: datetime = Field(..., description="Scheduled arrival time in UTC")
    departure_delay_mins: int = Field(default=0, description="Departure delay in minutes")
    arrival_delay_mins: int = Field(default=0, description="Arrival delay in minutes")

    # Joined columns
    dep_airport_name: str | None = None
    dep_lat: float | None = None
    dep_lng: float | None = None
    arr_airport_name: str | None = None
    arr_lat: float | None = None
    arr_lng: float | None = None
    airline_name: str | None = None
