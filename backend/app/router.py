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
from app.models import RouterDecision

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


def classify_intent(user_message: str) -> RouterDecision:
    """
    Pure local classifier — no network calls, no quota usage.
    Returns a RouterDecision in under 1ms.
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

    log.info(
        "Local router: mode=%s | sql_filter=%s | has_numeric=%s | has_semantic=%s",
        mode, sql_filter, has_numeric, has_semantic,
    )

    return RouterDecision(
        mode=mode,
        sql_filter=sql_filter,
        semantic_query=semantic_query,
        raw_intent=user_message,
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

async def retrieve(user_message: str, top_k: int = 5) -> dict[str, Any]:
    """
    Classify intent locally, run retrieval path(s), return unified context dict.
    """
    decision = classify_intent(user_message)

    sql_results: list[dict[str, Any]] = []
    vector_results: list[dict[str, Any]] = []

    # SQL branch
    if decision.mode in ('sql', 'hybrid') and decision.sql_filter:
        try:
            sql_results = db.query_cards_by_filter(decision.sql_filter, limit=top_k)
        except Exception as exc:  # noqa: BLE001
            log.error("SQL retrieval failed: %s", exc)

    # Vector branch — also runs for hybrid and pure vector
    if decision.mode in ('vector', 'hybrid'):
        semantic_q = decision.semantic_query or user_message
        try:
            vector_results = vector_store.search(semantic_q, top_k=top_k)
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
        return (
            f"[{label}] {card.get('name', 'Unknown')} ({card.get('issuer', '')})\n"
            f"  Annual Fee: ${card.get('annual_fee', '?')} | "
            f"APR: {card.get('regular_apr_low', '?')}–{card.get('regular_apr_high', '?')}%\n"
            f"  Signup Bonus: {card.get('signup_bonus', 'N/A')} "
            f"(~${card.get('signup_bonus_value_usd', 0)} value)\n"
            f"  Travel {card.get('travel_multiplier', 1)}x | "
            f"Dining {card.get('dining_multiplier', 1)}x | "
            f"Groceries {card.get('groceries_multiplier', 1)}x\n"
            f"  Lounge Access: {'Yes' if card.get('lounge_access') else 'No'} | "
            f"Foreign Fee: {card.get('foreign_transaction_fee', 0)}%\n"
            f"  Description: {card.get('description', '')}"
        )

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
