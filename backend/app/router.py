"""
Hybrid RAG Router — Intent classification and dual-retrieval orchestration.

Given the user's latest message, the router decides:
  - "sql"    → convert numeric constraints to a PostgREST filter string
  - "vector" → run a semantic FAISS search
  - "hybrid" → run both and merge results

The decision itself is made by a lightweight Gemini call with a constrained
output schema, keeping latency low (this is on the hot path).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

from app import db, vector_store
from app.models import RouterDecision

load_dotenv()
log = logging.getLogger("router")

ROUTER_MODEL = "gemini-2.5-flash"   # Small, fast — only produces a tiny JSON blob


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

ROUTER_SYSTEM = """\
You are an intent classification engine for a credit card recommendation system.
Given the user's message, output ONLY a JSON object with this exact schema:

{
  "mode": "sql" | "vector" | "hybrid",
  "sql_filter": "<PostgREST filter string or null>",
  "semantic_query": "<cleaned semantic query string or null>",
  "raw_intent": "<user message verbatim>"
}

PostgREST filter string rules:
- Use comma-separated "column.operator.value" triples.
- Operators: eq, neq, lt, lte, gt, gte, is, like, ilike, in
- Example: "annual_fee.lte.100,lounge_access.eq.true"

Column names available:
  annual_fee, regular_apr_low, regular_apr_high, signup_bonus_value_usd,
  travel_multiplier, dining_multiplier, groceries_multiplier, gas_multiplier,
  online_shopping_mult, other_multiplier, lounge_access, foreign_transaction_fee,
  credit_score_required

Classification rules:
- "sql"    → the query mentions ONLY numeric comparisons or boolean flags
- "vector" → the query mentions ONLY qualitative preferences (perks, lifestyle, vibe)
- "hybrid" → the query mentions BOTH numeric constraints AND qualitative preferences
- When in doubt, prefer "hybrid"

Do NOT add markdown fences or any commentary. Output only the JSON object.
"""


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

async def classify_intent(user_message: str) -> RouterDecision:
    """
    Send user_message to the router LLM and parse its RouterDecision output.
    Falls back to pure vector search on any parsing failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=ROUTER_MODEL,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_message)],
                )
            ],
            config=genai_types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM,
                temperature=0.0,
                max_output_tokens=512,
            ),
        )

        raw = response.text.strip()
        # Strip accidental markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```$", "", raw.strip())

        decision_data = json.loads(raw)
        decision_data["raw_intent"] = user_message
        return RouterDecision(**decision_data)

    except Exception as exc:  # noqa: BLE001
        log.warning("Router classification failed (%s) — falling back to vector", exc)
        return RouterDecision(
            mode="vector",
            sql_filter=None,
            semantic_query=user_message,
            raw_intent=user_message,
        )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

async def retrieve(user_message: str, top_k: int = 5) -> dict[str, Any]:
    """
    Classify intent, run the appropriate retrieval path(s), and return a
    unified context dict ready for the generation step.

    Returns:
        {
            "decision": RouterDecision,
            "sql_results": [...],   # may be empty
            "vector_results": [...] # may be empty
        }
    """
    decision = await classify_intent(user_message)
    log.info("Router decision: mode=%s", decision.mode)

    sql_results: list[dict[str, Any]] = []
    vector_results: list[dict[str, Any]] = []

    # --- SQL branch ---
    if decision.mode in ("sql", "hybrid") and decision.sql_filter:
        try:
            sql_results = db.query_cards_by_filter(decision.sql_filter, limit=top_k)
        except Exception as exc:  # noqa: BLE001
            log.error("SQL retrieval failed: %s", exc)

    # --- Vector branch ---
    if decision.mode in ("vector", "hybrid"):
        semantic_q = decision.semantic_query or user_message
        try:
            vector_results = vector_store.search(semantic_q, top_k=top_k)
        except FileNotFoundError:
            log.warning("FAISS index not found — vector results unavailable")
        except Exception as exc:  # noqa: BLE001
            log.error("Vector retrieval failed: %s", exc)

    return {
        "decision": decision,
        "sql_results": sql_results,
        "vector_results": vector_results,
    }


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

def format_context_for_prompt(retrieval: dict[str, Any]) -> str:
    """
    Merge SQL and vector results into a single formatted context block
    that the generation LLM can reference.
    Deduplicates by card name.
    """
    seen: set[str] = set()
    lines: list[str] = []

    def _format_card(card: dict[str, Any], source_label: str) -> str:
        return (
            f"[{source_label}] {card.get('name', 'Unknown')} ({card.get('issuer', '')})\n"
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
