"""
Pydantic schemas for the CardAI application.
Covers the scraper/agent output (CreditCard), the API boundary (ChatRequest),
and the Phase 3 multi-region search agent (Region, SearchQuery).
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

class Region(str, Enum):
    US = "US"
    IN = "IN"


# ---------------------------------------------------------------------------
# Scraper / ETL / Agent schemas
# ---------------------------------------------------------------------------

class RewardMultipliers(BaseModel):
    """Points / cash-back multipliers per spend category."""
    travel: float = Field(default=1.0, description="Multiplier on travel spend (e.g. 3x = 3.0)")
    dining: float = Field(default=1.0, description="Multiplier on dining spend")
    groceries: float = Field(default=1.0, description="Multiplier on grocery spend")
    gas: float = Field(default=1.0, description="Multiplier on gas/fuel spend")
    online_shopping: float = Field(default=1.0, description="Multiplier on online purchases")
    other: float = Field(default=1.0, description="Multiplier on all other spend")
    # India-specific categories
    fuel: float = Field(default=1.0, description="Multiplier on fuel spend (fuel surcharge waiver category)")
    utilities: float = Field(default=1.0, description="Multiplier on utility bill payments")
    emi_transactions: float = Field(default=1.0, description="Multiplier on EMI transactions")


class CreditCard(BaseModel):
    """
    Canonical data model for a single credit card.
    Scalar fields go to PostgreSQL; description goes to FAISS.
    """
    name: str = Field(..., description="Full marketing name of the card")
    issuer: str = Field(..., description="Issuing bank (e.g. Chase, Amex, HDFC, Axis)")
    annual_fee: float = Field(..., ge=0, description="Annual fee in the card's currency")
    regular_apr_low: float = Field(..., ge=0, description="Lowest ongoing APR (%)")
    regular_apr_high: float = Field(..., ge=0, description="Highest ongoing APR (%)")
    signup_bonus: str = Field(..., description="Human-readable signup bonus description")
    signup_bonus_value_usd: float = Field(
        default=0.0, ge=0, description="Estimated USD value of the signup bonus"
    )
    reward_multipliers: RewardMultipliers = Field(default_factory=RewardMultipliers)
    lounge_access: bool = Field(default=False, description="Airport lounge access included")
    foreign_transaction_fee: float = Field(
        default=0.0, ge=0, description="Foreign transaction fee as a percentage"
    )
    credit_score_required: str = Field(
        default="Good",
        description="Minimum credit tier (Excellent / Good / Fair / Poor)",
    )
    source_url: str = Field(..., description="URL the card data was scraped from")
    description: str = Field(
        ...,
        description="Rich qualitative description for vector embedding",
    )

    # --- Phase 3: multi-region fields ---
    region: Region = Field(default=Region.US, description="Market the card is issued in")
    currency: str = Field(default="USD", description='"USD" or "INR"')
    reward_type: str | None = Field(
        default=None, description='"cashback" | "points" | "miles"'
    )
    reward_rate_description: str | None = Field(
        default=None, description='e.g. "5 reward points per ₹150"'
    )
    fuel_surcharge_waiver: bool = Field(default=False, description="Fuel surcharge waiver (India)")
    domestic_lounge_access: int | None = Field(
        default=None, description="Free domestic lounge visits per quarter (India)"
    )
    international_lounge_access: int | None = Field(
        default=None, description="Free international lounge visits per year"
    )
    milestone_benefits: str | None = Field(
        default=None, description='e.g. "₹1000 voucher on ₹1L annual spend"'
    )
    joining_fee: float = Field(default=0.0, ge=0, description="One-time joining fee (India)")

    @field_validator("regular_apr_high")
    @classmethod
    def apr_high_ge_low(cls, v: float, info: Any) -> float:
        low = info.data.get("regular_apr_low", 0.0)
        if v < low:
            raise ValueError("regular_apr_high must be >= regular_apr_low")
        return v


class ScraperResult(BaseModel):
    """Wrapper returned by a single scraper run."""
    url: str
    cards: list[CreditCard]
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 3: Search agent schemas
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    """What the chat pipeline hands to the web search agent."""
    raw_query: str = Field(..., description="The user's message, unchanged")
    region: Region = Field(default=Region.US)
    card_type_hint: str | None = Field(
        default=None, description='"travel" | "cashback" | "dining" | "fuel" | "lifestyle" | "business"'
    )
    numeric_filters: dict = Field(
        default_factory=dict,
        description='Extracted constraints, e.g. {"annual_fee": {"op": "lte", "val": 5000}}',
    )


# ---------------------------------------------------------------------------
# API boundary schemas
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    """Payload sent by the frontend to POST /api/chat."""
    messages: list[ChatMessage] = Field(..., min_length=1)
    session_id: str | None = Field(default=None, description="Optional session identifier for memory")
    region: str | None = Field(
        default=None,
        pattern="^(US|IN|BOTH)$",
        description="Region selected in the UI; overrides auto-detection unless BOTH",
    )


class RouterDecision(BaseModel):
    """Internal schema — what the intent router decides to do."""
    mode: str = Field(..., pattern="^(sql|vector|hybrid)$")
    sql_filter: str | None = Field(default=None, description="WHERE clause fragment if mode includes sql")
    semantic_query: str | None = Field(default=None, description="Cleaned semantic query for FAISS if mode includes vector")
    raw_intent: str = Field(..., description="The user's last message, unchanged")
    region: Region = Field(default=Region.US, description="Detected market for this query")
    card_type: str | None = Field(default=None, description="Detected card category hint")
