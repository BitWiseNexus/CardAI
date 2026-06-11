"""
ReAct Web Search Agent for credit card data (Phase 3).

For each user query:
1. Formulates targeted search queries for the card type + region
2. Calls Tavily search API to get live web results
3. Feeds results to the LLM chain for structured extraction into CreditCard objects
4. Upserts into Supabase and rebuilds FAISS index
5. Returns list of fresh CreditCard objects ready for RAG context

Design rules:
- Cache-first: if Supabase has rows for this region fetched < TTL hours ago,
  skip the search entirely and serve cached rows.
- Never crash the chat: every failure path logs and returns [] / cached rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from app import db, llm, vector_store
from app.models import CreditCard, Region, SearchQuery

load_dotenv()
log = logging.getLogger("agent")

CACHE_TTL_HOURS = int(os.environ.get("CARD_CACHE_TTL_HOURS", "24"))
TAVILY_MAX_RESULTS = 5
TAVILY_BATCH_SIZE = 2      # parallel searches per batch
TAVILY_BATCH_DELAY = 0.5   # seconds between batches (rate-limit courtesy)


# ---------------------------------------------------------------------------
# Search query construction
# ---------------------------------------------------------------------------

SEARCH_TEMPLATES: dict[str, list[str]] = {
    "travel_IN": [
        "best travel credit cards India {year} annual fee lounge access reward points",
        "top travel cards India comparison HDFC Regalia Axis Atlas miles points benefits",
    ],
    "travel_US": [
        "best travel credit cards USA {year} annual fee airport lounge signup bonus",
        "top travel rewards cards comparison Chase Amex Capital One miles cashback",
    ],
    "no_fee_IN": [
        "best lifetime free LTF credit cards India {year} no annual fee",
        "zero annual fee credit cards India HDFC ICICI Axis SBI benefits cashback",
    ],
    "no_fee_US": [
        "best no annual fee credit cards USA {year} cashback rewards",
        "Chase Freedom vs Discover it no annual fee comparison {year}",
    ],
    "dining_IN": [
        "best dining credit cards India {year} restaurant rewards discount",
    ],
    "dining_US": [
        "best dining credit cards USA {year} restaurant cashback points",
    ],
    "fuel_IN": [
        "best fuel credit cards India {year} fuel surcharge waiver cashback petrol",
    ],
    "fuel_US": [
        "best gas credit cards USA {year} gas station cashback rewards",
    ],
    "cashback_IN": [
        "best cashback credit cards India {year} flat cashback online shopping",
    ],
    "cashback_US": [
        "best cashback credit cards USA {year} flat rate rotating categories",
    ],
    "lounge_IN": [
        "credit cards India unlimited domestic lounge access {year} Priority Pass",
    ],
    "lounge_US": [
        "best credit cards USA airport lounge access {year} Priority Pass Centurion",
    ],
    "lifestyle_IN": [
        "best lifestyle credit cards India {year} shopping movies milestone benefits",
    ],
    "lifestyle_US": [
        "best everyday rewards credit cards USA {year} groceries online shopping",
    ],
    "business_IN": [
        "best business credit cards India {year} corporate expenses rewards",
    ],
    "business_US": [
        "best business credit cards USA {year} signup bonus cashback",
    ],
}

_GENERIC_TEMPLATES: dict[str, list[str]] = {
    "IN": [
        "best credit cards India {year} comparison annual fee rewards benefits",
        "top HDFC ICICI Axis SBI credit cards {year} features fees",
    ],
    "US": [
        "best credit cards USA {year} comparison annual fee rewards benefits",
        "top Chase Amex Citi Capital One credit cards {year} features fees",
    ],
}


def _build_search_queries(search_query: SearchQuery) -> list[str]:
    """
    Generates 2-3 targeted search queries from the user's intent.
    Falls back to generic region templates when no card type was detected.
    """
    year = datetime.now().year
    region = search_query.region.value
    key = f"{search_query.card_type_hint}_{region}" if search_query.card_type_hint else None

    templates = SEARCH_TEMPLATES.get(key or "", []) or _GENERIC_TEMPLATES[region]
    queries = [t.format(year=year) for t in templates]

    # Ground the search in the user's own words as a final query
    suffix = "India" if search_query.region == Region.IN else "USA"
    user_q = f"{search_query.raw_query} credit card {suffix} {year}"
    if user_q not in queries:
        queries.append(user_q)

    return queries[:3]


# ---------------------------------------------------------------------------
# Tavily web search
# ---------------------------------------------------------------------------

async def _search_web(queries: list[str], region: Region) -> tuple[list[str], list[str]]:
    """
    Runs Tavily searches in parallel batches. Returns (raw text snippets, source urls).
    Tavily config: search_depth="advanced", include_answer=True, max_results=5.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        log.warning("TAVILY_API_KEY not set — web search agent disabled")
        return [], []

    from tavily import AsyncTavilyClient  # deferred so app starts without the package

    client = AsyncTavilyClient(api_key=api_key)

    async def _one(query: str) -> dict[str, Any]:
        try:
            return await client.search(
                query=query,
                search_depth="advanced",
                include_answer=True,
                max_results=TAVILY_MAX_RESULTS,
                country="india" if region == Region.IN else "united states",
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Tavily search failed for '%s': %s", query, exc)
            return {}

    responses: list[dict[str, Any]] = []
    for i in range(0, len(queries), TAVILY_BATCH_SIZE):
        batch = queries[i : i + TAVILY_BATCH_SIZE]
        responses.extend(await asyncio.gather(*[_one(q) for q in batch]))
        if i + TAVILY_BATCH_SIZE < len(queries):
            await asyncio.sleep(TAVILY_BATCH_DELAY)

    snippets: list[str] = []
    urls: list[str] = []
    for resp in responses:
        if not resp:
            continue
        if resp.get("answer"):
            snippets.append(f"[Search summary] {resp['answer']}")
        for result in resp.get("results", []):
            content = result.get("content") or ""
            url = result.get("url") or ""
            title = result.get("title") or ""
            if content:
                snippets.append(f"[{title}] ({url})\n{content}")
            if url:
                urls.append(url)

    log.info("Tavily returned %d snippet(s) from %d quer(ies)", len(snippets), len(queries))
    return snippets, urls


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
You are a precise financial data extraction engine.

Below are web search results about {region_label} credit cards.
Extract ALL distinct credit card products you can identify.

Return ONLY a valid JSON array. Each element must follow this exact schema:
[
  {{
    "name": "string — full card name",
    "issuer": "string — bank name (e.g. {issuer_examples})",
    "annual_fee": number,
    "regular_apr_low": number,
    "regular_apr_high": number,
    "signup_bonus": "string — welcome benefit description, or 'None'",
    "signup_bonus_value_usd": number,
    "reward_multipliers": {{
      "travel": number, "dining": number, "groceries": number, "gas": number,
      "online_shopping": number, "other": number,
      "fuel": number, "utilities": number, "emi_transactions": number
    }},
    "lounge_access": boolean,
    "foreign_transaction_fee": number,
    "credit_score_required": "Excellent | Good | Fair | Poor",
    "source_url": "string — the URL the card info came from",
    "description": "string — 2-4 sentence qualitative summary covering perks, ideal user, and differentiators",
    "region": "{region}",
    "currency": "{currency}",
    "reward_type": "cashback | points | miles | null",
    "reward_rate_description": "string like '5 reward points per ₹150' or null",
    "fuel_surcharge_waiver": boolean,
    "domestic_lounge_access": number of free domestic lounge visits per quarter or null,
    "international_lounge_access": number of free international lounge visits per year or null,
    "milestone_benefits": "string like '₹1000 voucher on ₹1L annual spend' or null",
    "joining_fee": number
  }}
]

Rules:
- Fees are in {currency}. APRs are percentages.
- If a numeric value is not mentioned, use 0 for fees/APR and 1.0 for reward multipliers.
- Only include cards with at least a name, an issuer, and one concrete fact (fee, reward, or benefit).
- Do NOT invent data not present in the search results.
- Do NOT wrap the output in markdown fences or add commentary.
- If no cards are found, return an empty array [].

SEARCH RESULTS:
{raw_text}
"""

MAX_EXTRACTION_CHARS = 60_000


def _parse_cards_json(raw_response: str, region: Region, fallback_url: str) -> list[CreditCard]:
    """Extract and validate card objects from the LLM's JSON output."""
    clean = re.sub(r"^```(?:json)?\s*", "", raw_response.strip(), flags=re.MULTILINE)
    clean = re.sub(r"```$", "", clean.strip())

    # Tolerate prose around the array — grab the outermost [...]
    start, end = clean.find("["), clean.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in LLM output: {clean[:300]}")
    clean = clean[start : end + 1]

    data: list[dict[str, Any]] = json.loads(clean)

    cards: list[CreditCard] = []
    for raw_card in data:
        if not isinstance(raw_card, dict):
            continue
        raw_card.setdefault("region", region.value)
        raw_card.setdefault("currency", "INR" if region == Region.IN else "USD")
        raw_card.setdefault("signup_bonus", "None")
        if not raw_card.get("source_url"):
            raw_card["source_url"] = fallback_url
        # Models sometimes emit APR high < low or negatives — clamp instead of dropping
        try:
            low = float(raw_card.get("regular_apr_low") or 0)
            high = float(raw_card.get("regular_apr_high") or 0)
            raw_card["regular_apr_low"] = max(low, 0.0)
            raw_card["regular_apr_high"] = max(high, raw_card["regular_apr_low"])
        except (TypeError, ValueError):
            raw_card["regular_apr_low"] = 0.0
            raw_card["regular_apr_high"] = 0.0
        try:
            cards.append(CreditCard(**raw_card))
        except ValidationError as exc:
            log.warning("Card validation failed — skipping %s: %s", raw_card.get("name"), exc)

    return cards


async def _extract_cards_from_results(
    raw_results: list[str], region: Region, source_urls: list[str]
) -> list[CreditCard]:
    """
    Sends combined search results to the LLM chain (Groq → Cerebras → Gemini)
    for structured extraction into CreditCard objects.
    """
    if not raw_results:
        return []

    combined = "\n\n---\n\n".join(raw_results)[:MAX_EXTRACTION_CHARS]
    is_india = region == Region.IN
    prompt = EXTRACTION_PROMPT.format(
        region_label="Indian" if is_india else "US",
        region=region.value,
        currency="INR" if is_india else "USD",
        issuer_examples="HDFC Bank, ICICI Bank, Axis Bank, SBI Cards" if is_india
        else "Chase, American Express, Citi, Capital One",
        raw_text=combined,
    )

    try:
        response = await llm.complete(prompt, temperature=0.0)
        fallback_url = source_urls[0] if source_urls else "https://tavily.com/search"
        cards = _parse_cards_json(response, region, fallback_url)
        log.info("Extracted %d valid card(s) for region %s", len(cards), region.value)
        return cards
    except Exception as exc:  # noqa: BLE001
        log.error("Card extraction failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Dedup + persistence
# ---------------------------------------------------------------------------

def _dedupe_cards(cards: list[CreditCard]) -> list[CreditCard]:
    """Deduplicate by (name, issuer), case-insensitive, keeping the first occurrence."""
    seen: set[tuple[str, str]] = set()
    unique: list[CreditCard] = []
    for card in cards:
        key = (card.name.strip().lower(), card.issuer.strip().lower())
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique


async def _store_cards(cards: list[CreditCard]) -> None:
    """Upsert into Supabase and rebuild the FAISS index (both off the event loop)."""
    if not cards:
        return
    try:
        await asyncio.to_thread(db.upsert_cards, cards)
        all_cards = await asyncio.to_thread(db.get_all_cards)
        await asyncio.to_thread(vector_store.build_index, all_cards)
        log.info("Stored %d card(s) and rebuilt FAISS index (%d total)", len(cards), len(all_cards))
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to store cards / rebuild index: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def fetch_cards_for_query(search_query: SearchQuery) -> list[CreditCard]:
    """
    Main entry point. Checks cache TTL, runs search if stale, returns cards.
    Never raises — the chat must keep working even if search/extraction fails.
    """
    try:
        stale = await asyncio.to_thread(db.is_cache_stale, search_query.region, CACHE_TTL_HOURS)
    except Exception as exc:  # noqa: BLE001
        log.error("Cache check crashed (assuming stale): %s", exc)
        stale = True

    if not stale:
        log.info("Cache fresh for region %s — skipping web search", search_query.region.value)
        return []

    if not os.environ.get("TAVILY_API_KEY"):
        log.warning("Cache stale but TAVILY_API_KEY missing — serving existing data only")
        return []

    try:
        queries = _build_search_queries(search_query)
        log.info("Search agent firing %d quer(ies): %s", len(queries), queries)

        snippets, urls = await _search_web(queries, search_query.region)
        if not snippets:
            return []

        cards = _dedupe_cards(
            await _extract_cards_from_results(snippets, search_query.region, urls)
        )
        if cards:
            await _store_cards(cards)
        return cards
    except Exception as exc:  # noqa: BLE001
        log.error("Search agent failed (chat continues with cached data): %s", exc)
        return []
