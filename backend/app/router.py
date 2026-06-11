"""
Hybrid RAG Router — Intent classification and dual-retrieval orchestration.

Intent classification is done LOCALLY with regex — zero Gemini API calls.
This saves 1 of the 3 API calls that previously fired per user message.

Router logic:
  "sql"    → query contains numeric comparisons or boolean flags only
  "vector" → query contains only qualitative/lifestyle preferences
  "hybrid" → query contains both, or is ambiguous (default safe choice)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app import db, vector_store
from app.models import Region, RouterDecision

log = logging.getLogger("router")


# ---------------------------------------------------------------------------
# Local rule-based intent classifier (no LLM, no API call)
# ---------------------------------------------------------------------------

# Patterns that signal a numeric/boolean SQL constraint
_SQL_PATTERNS = [
    r'\b(under|below|less\s+than|at\s+most|max(?:imum)?|no\s+more\s+than)\s*\$?\d+',
    r'\b(over|above|more\s+than|at\s+least|min(?:imum)?)\s*\$?\d+',
    r'\$\s*\d+',                           # any dollar amount
    r'\b\d+(\.\d+)?\s*%',                  # any percentage
    r'\bno\s+annual\s+fee\b',
    r'\bno\s+foreign\s+transaction\b',
    r'\bfree\s+card\b',
    r'\blounge\s+access\b',                # boolean flag in DB
    r'\bapr\b',
    r'\bannual\s+fee\b',
    r'\binterest\s+rate\b',
]

# Patterns that signal a semantic/qualitative query
_VECTOR_PATTERNS = [
    r'\bbest\s+for\b',
    r'\bgood\s+for\b',
    r'\bideal\s+for\b',
    r'\brecommend\b',
    r'\bsuggest\b',
    r'\bluxury\b',
    r'\bpremium\b',
    r'\bperks?\b',
    r'\blifestyle\b',
    r'\btravel(l?er|ling)?\b',
    r'\bdining\b',
    r'\brestaurant\b',
    r'\bgroceries?\b',
    r'\bgas\s+station\b',
    r'\bcashback\b',
    r'\breward(s|ing)?\b',
    r'\bpoints?\b',
    r'\bmiles?\b',
    r'\bbusiness\b',
    r'\bstudent\b',
    r'\bcompare\b',
]

# Numeric extraction: "under $100" → lte.100, "over $500" → gte.500
_NUMERIC_EXTRACTORS = [
    # annual fee
    (r'(?:annual\s+fee|fee)\s*(?:under|below|less\s+than|<)\s*\$?(\d+(?:\.\d+)?)', 'annual_fee', 'lte'),
    (r'(?:annual\s+fee|fee)\s*(?:over|above|more\s+than|>)\s*\$?(\d+(?:\.\d+)?)',  'annual_fee', 'gte'),
    (r'no\s+annual\s+fee',                                                           'annual_fee', 'eq0'),
    # APR
    (r'apr\s*(?:under|below|less\s+than|<)\s*(\d+(?:\.\d+)?)\s*%?',               'regular_apr_low', 'lte'),
    (r'apr\s*(?:over|above|more\s+than|>)\s*(\d+(?:\.\d+)?)\s*%?',                'regular_apr_low', 'gte'),
    # signup bonus value
    (r'(?:bonus|signup\s+bonus)\s*(?:over|above|more\s+than|>|worth\s+more\s+than)\s*\$?(\d+)', 'signup_bonus_value_usd', 'gte'),
    (r'(?:bonus|signup\s+bonus)\s*(?:under|below|less\s+than|<)\s*\$?(\d+)',       'signup_bonus_value_usd', 'lte'),
    # lounge
    (r'lounge\s+access',                                                             'lounge_access', 'eq_true'),
    # no foreign transaction fee
    (r'no\s+foreign\s+transaction',                                                  'foreign_transaction_fee', 'eq0'),
]


# ---------------------------------------------------------------------------
# Phase 3: region + card-type detection (still pure regex, zero API calls)
# ---------------------------------------------------------------------------

_INDIA_PATTERNS = [
    r'\bindia\b', r'\bindian\b', r'\binr\b', r'\brupees?\b', r'₹',
    r'\bhdfc\b', r'\bicici\b', r'\bsbi\b', r'\baxis\b', r'\bkotak\b',
    r'\byes\s+bank\b', r'\bindusind\b', r'\bau\s+bank\b', r'\brupay\b',
    r'\bamex\s+india\b', r'\bciti\s+india\b',
    r'\bregalia\b', r'\bmagnus\b', r'\batlas\b', r'\bmillenia\b',
    r'\bsapphiro\b', r'\brubyx\b', r'\bsimplyclick\b', r'\bmoneyback\b',
    r'\breward\s+points?\b',   # Indian cards use "reward points" not "miles"
    r'\bfuel\s+surcharge\b', r'\bpetrol\b',
    r'\blounge.*india\b', r'\bdomestic\s+lounge\b',
    r'\bmilestone\b', r'\bjoin(?:ing)?\s+fee\b',
    r'\bltf\b', r'\blifetime\s+free\b',
]

_US_PATTERNS = [
    r'\busa?\b', r'\bunited\s+states\b', r'\bamerican?\b',
    r'\bchase\b', r'\bamex\b', r'\bamerican\s+express\b',
    r'\bciti(?:bank)?\b', r'\bcapital\s+one\b',
    r'\bwells\s+fargo\b', r'\bdiscover\b',
    r'\bsapphire\b', r'\bfreedom\b', r'\bventure\b', r'\bautograph\b',
    r'\bcash\s*back\b', r'\bmiles\b',
    r'\bpriority\s+pass\b', r'\bapr\b',
]

_CARD_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ('travel',    [r'\btravel(l?er|ling)?\b', r'\bairlines?\b', r'\bflights?\b',
                   r'\bairports?\b', r'\bmiles\b', r'\bhotels?\b']),
    ('lounge',    [r'\blounges?\b', r'\bpriority\s+pass\b']),
    ('fuel',      [r'\bfuel\b', r'\bpetrol\b', r'\bgas\s+station\b', r'\bdiesel\b',
                   r'\bfuel\s+surcharge\b']),
    ('dining',    [r'\bdining\b', r'\brestaurants?\b', r'\bfood\b', r'\bzomato\b', r'\bswiggy\b']),
    ('cashback',  [r'\bcash\s*back\b', r'\bcashback\b', r'\bflat\s+rate\b']),
    ('no_fee',    [r'\bno\s+annual\s+fee\b', r'\bzero\s+annual\s+fee\b', r'\bltf\b',
                   r'\blifetime\s+free\b', r'\bfree\s+card\b']),
    ('business',  [r'\bbusiness\b', r'\bcorporate\b']),
    ('lifestyle', [r'\blifestyle\b', r'\bshopping\b', r'\bmovies?\b', r'\bgroceries\b']),
]


def detect_region(message: str) -> Region:
    """Returns Region.IN or Region.US (default US when ambiguous)."""
    msg = message.lower()
    india_hits = sum(1 for p in _INDIA_PATTERNS if re.search(p, msg, re.IGNORECASE))
    us_hits = sum(1 for p in _US_PATTERNS if re.search(p, msg, re.IGNORECASE))
    if india_hits > us_hits:
        return Region.IN
    return Region.US


def detect_card_type(message: str) -> str | None:
    """Returns 'travel'|'lounge'|'fuel'|'dining'|'cashback'|'no_fee'|'business'|'lifestyle'|None."""
    msg = message.lower()
    for card_type, patterns in _CARD_TYPE_PATTERNS:
        if any(re.search(p, msg, re.IGNORECASE) for p in patterns):
            return card_type
    return None


def _build_sql_filter(message: str) -> str | None:
    """Extract PostgREST filter conditions from the message text."""
    msg = message.lower()
    conditions: list[str] = []

    for pattern, column, op in _NUMERIC_EXTRACTORS:
        match = re.search(pattern, msg, re.IGNORECASE)
        if match:
            if op == 'eq0':
                conditions.append(f'{column}.eq.0')
            elif op == 'eq_true':
                conditions.append(f'{column}.eq.true')
            else:
                value = match.group(1)
                conditions.append(f'{column}.{op}.{value}')

    return ','.join(conditions) if conditions else None


def classify_intent(user_message: str, region_override: str | None = None) -> RouterDecision:
    """
    Pure local classifier — no network calls, no quota usage.
    Returns a RouterDecision in under 1ms.

    region_override: 'US' | 'IN' from the UI selector takes precedence;
    'BOTH' or None falls back to regex detection on the message.
    """
    msg = user_message.lower()

    has_numeric = any(re.search(p, msg, re.IGNORECASE) for p in _SQL_PATTERNS)
    has_semantic = any(re.search(p, msg, re.IGNORECASE) for p in _VECTOR_PATTERNS)

    if has_numeric and has_semantic:
        mode = 'hybrid'
    elif has_numeric:
        mode = 'sql'
    else:
        # Default to hybrid so we always get both SQL + vector results.
        # Pure-vector only when there are zero numeric signals.
        mode = 'hybrid' if not has_semantic else 'vector'

    sql_filter = _build_sql_filter(user_message) if mode in ('sql', 'hybrid') else None
    semantic_query = user_message if mode in ('vector', 'hybrid') else None

    if region_override in (Region.US.value, Region.IN.value):
        region = Region(region_override)
    else:
        region = detect_region(user_message)
    card_type = detect_card_type(user_message)

    log.info(
        "Local router: mode=%s | region=%s | card_type=%s | sql_filter=%s",
        mode, region.value, card_type, sql_filter,
    )

    return RouterDecision(
        mode=mode,
        sql_filter=sql_filter,
        semantic_query=semantic_query,
        raw_intent=user_message,
        region=region,
        card_type=card_type,
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

async def retrieve(
    user_message: str,
    top_k: int = 5,
    region_override: str | None = None,
) -> dict[str, Any]:
    """
    Classify intent locally, run retrieval path(s), return unified context dict.
    When the region is known (not 'BOTH'), SQL results are filtered to that
    region and vector results from the matching region are preferred.
    """
    decision = classify_intent(user_message, region_override=region_override)
    region_locked = region_override != 'BOTH'

    sql_results: list[dict[str, Any]] = []
    vector_results: list[dict[str, Any]] = []

    # SQL branch
    if decision.mode in ('sql', 'hybrid') and decision.sql_filter:
        sql_filter = decision.sql_filter
        if region_locked:
            sql_filter = f"{sql_filter},region.eq.{decision.region.value}"
        try:
            sql_results = db.query_cards_by_filter(sql_filter, limit=top_k)
        except Exception as exc:  # noqa: BLE001
            log.error("SQL retrieval failed: %s", exc)

    # Vector branch — also runs for hybrid and pure vector
    if decision.mode in ('vector', 'hybrid'):
        semantic_q = decision.semantic_query or user_message
        try:
            # Over-fetch so region filtering still leaves enough candidates
            raw = vector_store.search(semantic_q, top_k=top_k * 3 if region_locked else top_k)
            if region_locked:
                matching = [c for c in raw if c.get("region", "US") == decision.region.value]
                vector_results = (matching or raw)[:top_k]
            else:
                vector_results = raw[:top_k]
        except FileNotFoundError:
            log.warning("FAISS index not found — vector results unavailable")
        except Exception as exc:  # noqa: BLE001
            log.error("Vector retrieval failed: %s", exc)

    # If SQL mode produced no results (filter too strict), fall back to vector
    if decision.mode == 'sql' and not sql_results:
        log.info("SQL returned 0 results — falling back to vector search")
        try:
            vector_results = vector_store.search(user_message, top_k=top_k)
        except Exception:  # noqa: BLE001
            pass

    return {
        "decision": decision,
        "sql_results": sql_results,
        "vector_results": vector_results,
    }


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def format_context_for_prompt(retrieval: dict[str, Any]) -> str:
    """Merge SQL and vector results into a deduplicated context block."""
    seen: set[str] = set()
    lines: list[str] = []

    def _format_card(card: dict[str, Any], label: str) -> str:
        region = card.get('region', 'US') or 'US'
        symbol = '₹' if card.get('currency') == 'INR' or region == 'IN' else '$'
        base = (
            f"[{label}] {card.get('name', 'Unknown')} ({card.get('issuer', '')}) — Region: {region}\n"
            f"  Annual Fee: {symbol}{card.get('annual_fee', '?')} | "
            f"APR: {card.get('regular_apr_low', '?')}–{card.get('regular_apr_high', '?')}%\n"
            f"  Signup Bonus: {card.get('signup_bonus', 'N/A')} "
            f"(~${card.get('signup_bonus_value_usd', 0)} value)\n"
            f"  Travel {card.get('travel_multiplier', 1)}x | "
            f"Dining {card.get('dining_multiplier', 1)}x | "
            f"Groceries {card.get('groceries_multiplier', 1)}x\n"
            f"  Lounge Access: {'Yes' if card.get('lounge_access') else 'No'} | "
            f"Foreign Fee: {card.get('foreign_transaction_fee', 0)}%\n"
        )
        # India-specific facts (only render when present)
        india_bits: list[str] = []
        if card.get('joining_fee'):
            india_bits.append(f"Joining Fee: {symbol}{card['joining_fee']}")
        if card.get('reward_type'):
            india_bits.append(f"Reward Type: {card['reward_type']}")
        if card.get('reward_rate_description'):
            india_bits.append(f"Reward Rate: {card['reward_rate_description']}")
        if card.get('fuel_surcharge_waiver'):
            india_bits.append("Fuel Surcharge Waiver: Yes")
        if card.get('domestic_lounge_access') is not None:
            india_bits.append(f"Domestic Lounge: {card['domestic_lounge_access']}/quarter")
        if card.get('international_lounge_access') is not None:
            india_bits.append(f"Intl Lounge: {card['international_lounge_access']}/year")
        if card.get('milestone_benefits'):
            india_bits.append(f"Milestones: {card['milestone_benefits']}")
        if india_bits:
            base += "  " + " | ".join(india_bits) + "\n"
        return base + f"  Description: {card.get('description', '')}"

    for card in retrieval.get("sql_results", []):
        key = card.get("name", "")
        if key not in seen:
            seen.add(key)
            lines.append(_format_card(card, "DB"))

    for card in retrieval.get("vector_results", []):
        key = card.get("name", "")
        if key not in seen:
            seen.add(key)
            lines.append(_format_card(card, "Semantic"))

    if not lines:
        return "No relevant credit card data found in the database."

    return "\n\n".join(lines)
