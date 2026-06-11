"""
Phase 3 unit tests — region detection, card-type detection, search query
construction, cache TTL behaviour, and card extraction parsing.

Unit tests mock Tavily and the LLM chain — zero API calls.
Integration tests (real Tavily + LLM) are gated by RUN_INTEGRATION=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agent
from app.models import Region, SearchQuery
from app.router import classify_intent, detect_card_type, detect_region

RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION") == "1"


# ---------------------------------------------------------------------------
# Region detection
# ---------------------------------------------------------------------------

def test_region_detection_india():
    assert detect_region("Tell me about the HDFC Regalia card") == Region.IN
    assert detect_region("best cards in India with no joining fee") == Region.IN
    assert detect_region("cards with fuel surcharge waiver for petrol") == Region.IN
    assert detect_region("compare ICICI Sapphiro vs Axis Magnus") == Region.IN


def test_region_detection_us():
    assert detect_region("Chase Sapphire Preferred vs Reserve") == Region.US
    assert detect_region("best US cards with Priority Pass") == Region.US
    assert detect_region("Capital One Venture X review") == Region.US


def test_region_detection_default():
    # Ambiguous queries default to US
    assert detect_region("which card should I get?") == Region.US
    assert detect_region("best card for big spenders") == Region.US


def test_region_override_wins():
    decision = classify_intent("best dining card", region_override="IN")
    assert decision.region == Region.IN
    decision = classify_intent("best HDFC card", region_override="US")
    assert decision.region == Region.US


def test_region_override_both_falls_back_to_detection():
    decision = classify_intent("best HDFC card", region_override="BOTH")
    assert decision.region == Region.IN


# ---------------------------------------------------------------------------
# Card type detection
# ---------------------------------------------------------------------------

def test_card_type_detection_travel():
    assert detect_card_type("best card for airports and flights") == "travel"
    assert detect_card_type("I travel a lot internationally") == "travel"


def test_card_type_detection_fuel():
    assert detect_card_type("best petrol card") == "fuel"
    assert detect_card_type("card with fuel surcharge waiver") == "fuel"


def test_card_type_detection_none():
    assert detect_card_type("what should I get?") is None


# ---------------------------------------------------------------------------
# Search query builder
# ---------------------------------------------------------------------------

def test_search_query_builder():
    sq = SearchQuery(raw_query="best travel cards India", region=Region.IN, card_type_hint="travel")
    queries = agent._build_search_queries(sq)
    assert 2 <= len(queries) <= 3
    assert all(q.strip() for q in queries)
    assert any("India" in q for q in queries)


def test_search_query_builder_no_hint_uses_generic():
    sq = SearchQuery(raw_query="something good", region=Region.US, card_type_hint=None)
    queries = agent._build_search_queries(sq)
    assert 2 <= len(queries) <= 3
    assert any("USA" in q for q in queries)


# ---------------------------------------------------------------------------
# Cache TTL behaviour (mocked DB + search)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_ttl_fresh(monkeypatch):
    """Fresh cache (< TTL) → no web search fired."""
    search_called = False

    async def _spy_search(queries, region):
        nonlocal search_called
        search_called = True
        return [], []

    monkeypatch.setattr(agent.db, "is_cache_stale", lambda region, hours: False)
    monkeypatch.setattr(agent, "_search_web", _spy_search)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    sq = SearchQuery(raw_query="best travel cards", region=Region.US, card_type_hint="travel")
    result = await agent.fetch_cards_for_query(sq)

    assert result == []
    assert search_called is False


@pytest.mark.asyncio
async def test_cache_ttl_stale(monkeypatch):
    """Stale cache (> TTL) → web search fires."""
    search_called = False

    async def _spy_search(queries, region):
        nonlocal search_called
        search_called = True
        return [], []

    monkeypatch.setattr(agent.db, "is_cache_stale", lambda region, hours: True)
    monkeypatch.setattr(agent, "_search_web", _spy_search)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    sq = SearchQuery(raw_query="best travel cards", region=Region.US, card_type_hint="travel")
    result = await agent.fetch_cards_for_query(sq)

    assert result == []
    assert search_called is True


@pytest.mark.asyncio
async def test_stale_but_no_tavily_key(monkeypatch):
    """Stale cache but missing TAVILY_API_KEY → no crash, empty result."""
    monkeypatch.setattr(agent.db, "is_cache_stale", lambda region, hours: True)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    sq = SearchQuery(raw_query="best cards", region=Region.IN)
    assert await agent.fetch_cards_for_query(sq) == []


# ---------------------------------------------------------------------------
# Extraction parsing
# ---------------------------------------------------------------------------

_INR_CARD_JSON = """
[
  {
    "name": "HDFC Regalia Gold",
    "issuer": "HDFC Bank",
    "annual_fee": 2500,
    "regular_apr_low": 0,
    "regular_apr_high": 0,
    "signup_bonus": "Welcome voucher worth ₹2500",
    "signup_bonus_value_usd": 30,
    "reward_multipliers": {"travel": 4.0, "dining": 4.0, "fuel": 1.0},
    "lounge_access": true,
    "foreign_transaction_fee": 2.0,
    "credit_score_required": "Good",
    "source_url": "https://example.com/regalia",
    "description": "Premium lifestyle card with lounge access and milestone benefits.",
    "region": "IN",
    "currency": "INR",
    "reward_type": "points",
    "reward_rate_description": "4 reward points per ₹150",
    "fuel_surcharge_waiver": true,
    "domestic_lounge_access": 3,
    "international_lounge_access": 6,
    "milestone_benefits": "₹1500 voucher on ₹1.5L quarterly spend",
    "joining_fee": 2500
  }
]
"""


def test_inr_card_extraction():
    cards = agent._parse_cards_json(_INR_CARD_JSON, Region.IN, "https://fallback.com")
    assert len(cards) == 1
    card = cards[0]
    assert card.region == Region.IN
    assert card.currency == "INR"
    assert card.joining_fee == 2500
    assert card.fuel_surcharge_waiver is True
    assert card.domestic_lounge_access == 3
    assert card.reward_type == "points"


_USD_CARD_JSON = """
Here is the extracted data:
```json
[
  {
    "name": "Chase Sapphire Preferred",
    "issuer": "Chase",
    "annual_fee": 95,
    "regular_apr_low": 21.49,
    "regular_apr_high": 28.49,
    "signup_bonus": "60,000 points after $4,000 spend",
    "signup_bonus_value_usd": 750,
    "lounge_access": false,
    "foreign_transaction_fee": 0,
    "credit_score_required": "Excellent",
    "source_url": "https://example.com/csp",
    "description": "Flexible travel rewards card with transfer partners."
  }
]
```
"""


def test_usd_card_extraction():
    """Parser tolerates prose + markdown fences and fills region defaults."""
    cards = agent._parse_cards_json(_USD_CARD_JSON, Region.US, "https://fallback.com")
    assert len(cards) == 1
    card = cards[0]
    assert card.region == Region.US
    assert card.currency == "USD"
    assert card.annual_fee == 95
    assert card.joining_fee == 0


def test_extraction_clamps_bad_apr():
    bad = '[{"name": "X Card", "issuer": "X Bank", "annual_fee": 0, "regular_apr_low": 20, "regular_apr_high": 10, "signup_bonus": "None", "source_url": "u", "description": "d"}]'
    cards = agent._parse_cards_json(bad, Region.US, "https://fallback.com")
    assert len(cards) == 1
    assert cards[0].regular_apr_high >= cards[0].regular_apr_low


def test_dedupe_cards():
    cards = agent._parse_cards_json(_INR_CARD_JSON, Region.IN, "u") * 2
    assert len(agent._dedupe_cards(cards)) == 1


# ---------------------------------------------------------------------------
# Integration tests (real APIs — RUN_INTEGRATION=1)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not RUN_INTEGRATION, reason="set RUN_INTEGRATION=1 to run live tests")
@pytest.mark.asyncio
async def test_live_search_india_travel():
    sq = SearchQuery(raw_query="best travel cards India", region=Region.IN, card_type_hint="travel")
    snippets, urls = await agent._search_web(agent._build_search_queries(sq), Region.IN)
    assert snippets
    cards = await agent._extract_cards_from_results(snippets, Region.IN, urls)
    assert cards
    assert all(c.region == Region.IN for c in cards)


@pytest.mark.skipif(not RUN_INTEGRATION, reason="set RUN_INTEGRATION=1 to run live tests")
@pytest.mark.asyncio
async def test_live_search_us_travel():
    sq = SearchQuery(raw_query="best travel cards USA", region=Region.US, card_type_hint="travel")
    snippets, urls = await agent._search_web(agent._build_search_queries(sq), Region.US)
    assert snippets
    cards = await agent._extract_cards_from_results(snippets, Region.US, urls)
    assert cards


@pytest.mark.skipif(not RUN_INTEGRATION, reason="set RUN_INTEGRATION=1 to run live tests")
@pytest.mark.asyncio
async def test_full_pipeline_india():
    sq = SearchQuery(raw_query="best travel cards India", region=Region.IN, card_type_hint="travel")
    cards = await agent.fetch_cards_for_query(sq)
    # Either fresh cards were fetched, or the cache was already warm
    assert isinstance(cards, list)
