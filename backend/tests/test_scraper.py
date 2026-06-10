"""
Pytest tests for the ETL scraper pipeline.

Tests cover:
- Pydantic model validation (unit)
- Gemini JSON parsing (unit — mocked)
- Full scraper integration (skipped unless RUN_INTEGRATION=1 env var is set)
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import CreditCard, RewardMultipliers, ScraperResult
from scripts.scraper import _parse_gemini_response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CARD_JSON = json.dumps([
    {
        "name": "Test Rewards Card",
        "issuer": "Test Bank",
        "annual_fee": 95.0,
        "regular_apr_low": 20.24,
        "regular_apr_high": 29.24,
        "signup_bonus": "Earn 60,000 points after spending $4,000 in 3 months",
        "signup_bonus_value_usd": 750.0,
        "reward_multipliers": {
            "travel": 3.0,
            "dining": 3.0,
            "groceries": 1.0,
            "gas": 1.0,
            "online_shopping": 1.0,
            "other": 1.0,
        },
        "lounge_access": False,
        "foreign_transaction_fee": 0.0,
        "credit_score_required": "Excellent",
        "source_url": "https://example.com/cards",
        "description": "A great travel rewards card with strong dining perks.",
    }
])


# ---------------------------------------------------------------------------
# Unit: Pydantic model validation
# ---------------------------------------------------------------------------

class TestCreditCardModel:
    def test_valid_card(self) -> None:
        card = CreditCard(
            name="Test Card",
            issuer="Test Bank",
            annual_fee=0,
            regular_apr_low=19.99,
            regular_apr_high=29.99,
            signup_bonus="None",
            source_url="https://example.com",
            description="A no-frills card.",
        )
        assert card.annual_fee == 0
        assert card.reward_multipliers.travel == 1.0

    def test_apr_high_less_than_low_raises(self) -> None:
        with pytest.raises(Exception):
            CreditCard(
                name="Bad Card",
                issuer="Bank",
                annual_fee=0,
                regular_apr_low=30.0,
                regular_apr_high=20.0,   # invalid
                signup_bonus="None",
                source_url="https://example.com",
                description="Should fail.",
            )

    def test_negative_annual_fee_raises(self) -> None:
        with pytest.raises(Exception):
            CreditCard(
                name="Bad Card",
                issuer="Bank",
                annual_fee=-50,   # invalid
                regular_apr_low=19.99,
                regular_apr_high=29.99,
                signup_bonus="None",
                source_url="https://example.com",
                description="Should fail.",
            )


# ---------------------------------------------------------------------------
# Unit: Gemini response parser
# ---------------------------------------------------------------------------

class TestParseGeminiResponse:
    def test_valid_json_parsed(self) -> None:
        cards = _parse_gemini_response(VALID_CARD_JSON, "https://example.com/cards")
        assert len(cards) == 1
        assert cards[0].name == "Test Rewards Card"
        assert cards[0].annual_fee == 95.0

    def test_markdown_fences_stripped(self) -> None:
        fenced = f"```json\n{VALID_CARD_JSON}\n```"
        cards = _parse_gemini_response(fenced, "https://example.com")
        assert len(cards) == 1

    def test_empty_array(self) -> None:
        cards = _parse_gemini_response("[]", "https://example.com")
        assert cards == []

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid JSON"):
            _parse_gemini_response("not json at all", "https://example.com")

    def test_invalid_card_skipped(self) -> None:
        bad_card = json.dumps([{"name": "Incomplete Card"}])  # missing required fields
        cards = _parse_gemini_response(bad_card, "https://example.com")
        assert cards == []   # ValidationError triggers skip, not crash


# ---------------------------------------------------------------------------
# Integration: full scrape pipeline (requires network + GEMINI_API_KEY)
# ---------------------------------------------------------------------------

INTEGRATION = os.environ.get("RUN_INTEGRATION", "0") == "1"


@pytest.mark.skipif(not INTEGRATION, reason="Set RUN_INTEGRATION=1 to run")
@pytest.mark.asyncio
async def test_full_scrape_pipeline() -> None:
    from scripts.scraper import scrape

    # Uses a stable, public credit card comparison page for testing
    result = await scrape("https://creditcards.chase.com/")
    assert isinstance(result, ScraperResult)
    # Don't assert card count — page structure may vary
    assert result.url.startswith("https://")
