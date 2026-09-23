"""Pydantic models for everything that comes out of ingest."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    id: str
    client_id: str
    row: int
    date: date
    description: str
    counterparty: str
    source_category: str
    amount: float
    currency: str
    balance: Optional[float] = None
    is_fx: bool = False  # currency differs from the base currency


class Profile(BaseModel):
    client_id: str
    display_name: str
    source_file: str
    age: int
    age_band: str
    age_estimated: bool
    gender: Optional[str] = None
    marital_status: str = "single"
    occupation: str = "Unknown"
    segment: str = "Mass market"
    currency: str = "CHF"
    uploaded: bool = False


class ReturnAssumptions(BaseModel):
    low: float
    medium: float
    high: float
    volatility: float = 0.0
    fees: float = 0.0


class Product(BaseModel):
    id: str
    name: str
    category: str
    product_type: str
    characteristics: str
    risk_profile_required: str
    risk_details: str
    price: str
    url: str
    # attributes from config/products.yaml
    segment: str = "private"
    basket: Optional[str] = None
    risk_class: int = 0
    knowledge: Optional[str] = None
    knowledge_level: str = "none"
    complex: bool = False
    min_horizon_years: float = 0
    returns: Optional[ReturnAssumptions] = None
    eligibility: dict[str, Any] = Field(default_factory=dict)
