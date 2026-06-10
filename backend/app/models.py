"""
Pydantic schemas for the CardAI application.
Covers both the scraper output (CreditCard) and the API boundary (ChatRequest/ChatResponse).
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Scraper / ETL schemas
# ---------------------------------------------------------------------------

class RewardMultipliers(BaseModel):
    """Points / cash-back multipliers per spend category."""
    travel: float = Field(default=1.0, description="Multiplier on travel spend (e.g. 3x = 3.0)")
    dining: float = Field(default=1.0, description="Multiplier on dining spend")
    groceries: float = Field(default=1.0, description="Multiplier on grocery spend")
    gas: float = Field(default=1.0, description="Multiplier on gas/fuel spend")
    online_shopping: float = Field(default=1.0, description="Multiplier on online purchases")
    other: float = Field(default=1.0, description="Multiplier on all other spend")


class CreditCard(BaseModel):
    """
    Canonical data model for a single credit card.
    Scalar fields go to PostgreSQL; description goes to FAISS.
    """
    name: str = Field(..., description="Full marketing name of the card")
    issuer: str = Field(..., description="Issuing bank (e.g. Chase, Amex, Citi)")
    annual_fee: float = Field(..., ge=0, description="Annual fee in USD")
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
# API boundary schemas
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    """Payload sent by the frontend to POST /api/chat."""
    messages: list[ChatMessage] = Field(..., min_length=1)
    session_id: str | None = Field(default=None, description="Optional session identifier for memory")


class RouterDecision(BaseModel):
    """Internal schema — what the intent router decides to do."""
    mode: str = Field(..., pattern="^(sql|vector|hybrid)$")
    sql_filter: str | None = Field(default=None, description="WHERE clause fragment if mode includes sql")
    semantic_query: str | None = Field(default=None, description="Cleaned semantic query for FAISS if mode includes vector")
    raw_intent: str = Field(..., description="The user's last message, unchanged")
