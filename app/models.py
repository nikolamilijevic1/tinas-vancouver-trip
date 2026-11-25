from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    origin: str = Field(default="YOW")
    origins: list[str] | None = Field(default=None, description="Optional list of origin IATA codes, e.g. ['YOW', 'YYZ', 'YTZ']")
    destination: str = Field(default="YVR")
    depart_start: str = Field(description="Earliest departure date YYYY-MM-DD")
    depart_end: str = Field(description="Latest departure date YYYY-MM-DD")
    return_start: str = Field(description="Earliest return date YYYY-MM-DD")
    return_end: str = Field(description="Latest return date YYYY-MM-DD")
    min_nights: int = Field(default=5, ge=1, le=30)
    max_nights: int = Field(default=10, ge=1, le=30)
    nonstop_only: bool = False
    include_carry_on: bool = True
    include_nearby_airports: bool = False
    max_pairs: int = Field(default=10, ge=3, le=18)
    force_refresh: bool = False


class PairSearchRequest(BaseModel):
    origin: str = "YOW"
    destination: str = "YVR"
    depart_date: str
    return_date: str
    nonstop_only: bool = False
    include_carry_on: bool = True
    force_refresh: bool = False


class FlightLegOut(BaseModel):
    airline_code: str
    airline_name: str
    flight_number: str
    from_code: str
    from_name: str
    to_code: str
    to_name: str
    departure_date: str
    departure_time: str
    arrival_date: str
    arrival_time: str
    duration_minutes: int
    aircraft: str | None = None


class LayoverOut(BaseModel):
    airport_code: str
    airport_name: str
    city: str
    duration_minutes: int
    overnight: bool = False


class FlightOptionOut(BaseModel):
    id: str
    origin: str
    destination: str
    depart_date: str
    return_date: str
    nights: int
    price_cad: int
    airlines: list[str]
    primary_airline: str
    airline_code: str = ""
    stops: int
    total_duration_minutes: int
    legs: list[FlightLegOut]
    layovers: list[LayoverOut]
    has_overnight_layover: bool = False
    is_nearby_airport: bool = False
    carry_on_included: bool = True
    rank_score: int = 0
    note: str | None = None
    booking_url: str


class MatrixCellOut(BaseModel):
    origin: str
    destination: str
    depart_date: str
    return_date: str
    nights: int
    cheapest_cad: int | None = None
    cheapest_nonstop_cad: int | None = None
    booking_url: str | None = None


class EmailAlertIn(BaseModel):
    enabled: bool = True
    email: str = Field(default="", max_length=254)
    origin: str = "YOW"
    destination: str = "YVR"
    depart_start: str = "2026-12-18"
    depart_end: str = "2026-12-23"
    return_start: str = "2026-12-27"
    return_end: str = "2027-01-04"
    min_nights: int = Field(default=5, ge=1, le=30)
    max_nights: int = Field(default=10, ge=1, le=30)
    nonstop_only: bool = False
    include_carry_on: bool = True
    max_price_cad: int = Field(default=850, ge=100, le=5000)
    daily_summary: bool = False
