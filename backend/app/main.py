"""
CardAI — FastAPI application entry point.

Endpoints:
  GET  /health          → liveness check + LLM provider status
  GET  /api/cache/stats → how many responses are cached (quota audit)
  POST /api/chat        → session-aware hybrid RAG chatbot with SSE streaming
  POST /api/ingest      → trigger scraper + upsert + FAISS rebuild (admin)

Phase 3 pipeline per message:
  1. Local regex router      → intent + region + card type (0 API calls)
  2. Web search agent        → Tavily search + LLM extraction, only if the
                               regional cache is stale (24h TTL)
  3. Hybrid retrieval        → Supabase SQL filter + FAISS vector search
  4. Streaming generation    → multi-provider LLM chain (Groq → Cerebras → Gemini)

Quota-conservation measures
────────────────────────────
1. Local regex router    → 0 LLM calls for intent classification
2. SQL-only queries      → 0 embedding calls (router skips FAISS)
3. Response cache        → repeated identical queries cost 0 API calls
4. Provider failover     → rate-limited providers are skipped automatically
5. 24h search cache      → Tavily fires at most once per region per TTL window
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db, llm, vector_store
from app.agent import fetch_cards_for_query
from app.models import ChatRequest, SearchQuery
from app.router import classify_intent, format_context_for_prompt, retrieve

load_dotenv()
log = logging.getLogger("main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CardAI",
    description="Real-Time Multi-Region Credit Card Search Agent & Hybrid RAG Chatbot",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
_sessions: dict[str, list[dict]] = defaultdict(list)

# Response cache: {cache_key: full_response_text}
# Keyed by hash(region + normalized_query).  Cleared by POST /api/cache/clear.
_response_cache: dict[str, str] = {}


def _cache_key(user_message: str, region: str | None = None) -> str:
    normalized = f"{region or 'AUTO'}|{user_message.strip().lower()}"
    return hashlib.md5(normalized.encode()).hexdigest()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are CardAI, an expert financial advisor for credit cards in both the United States and India.

Regional expertise:
- US cards: Annual fees in USD, APR ranges, signup bonuses (miles/points/cashback), Priority Pass lounge access
- India cards: Annual/joining fees in INR, reward points (not miles), fuel surcharge waivers, domestic + international lounge access, milestone benefits, LTF (lifetime free) cards

Guidelines:
1. Answer ONLY from the provided card data — never hallucinate fees, rewards, or benefits
2. Always clarify the currency (USD/INR) when stating fees or benefits
3. For Indian cards: mention joining fee vs annual fee separately (often waived on spend)
4. For US cards: mention APR prominently (Indian cards rarely have variable APR)
5. Present comparisons as markdown tables with currency clearly labelled
6. If comparing across regions, note they serve different markets and are not directly comparable
7. End with a "Bottom line:" recommendation
8. If no relevant cards found, say so and suggest what the user could specify
"""

# ---------------------------------------------------------------------------
# Streaming generation via the multi-provider LLM chain
# ---------------------------------------------------------------------------

async def stream_generation(
    user_message: str,
    history: list[dict],
    context_block: str,
    region: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream SSE chunks from the LLM provider chain (Groq → Cerebras → Gemini).
    Rate-limited providers fail over automatically inside llm.stream_chat.
    Caches the complete response so duplicate queries are free.
    """
    cache_k = _cache_key(user_message, region)
    if cache_k in _response_cache:
        log.info("Cache HIT for query: %s", user_message[:60])
        cached = _response_cache[cache_k]
        # Re-stream cached text token-by-token (no API call)
        for word in cached.split(" "):
            yield f'data: {json.dumps({"token": word + " "})}\n\n'
            await asyncio.sleep(0)
        yield "data: [DONE]\n\n"
        return

    messages: list[dict] = [
        {
            "role": "user",
            "content": f"[RETRIEVED CARD DATA — use as factual source]\n\n{context_block}",
        },
        {
            "role": "assistant",
            "content": "Understood. I will base my response on the retrieved card data above.",
        },
    ]
    for msg in history[-20:]:
        messages.append({
            "role": "user" if msg["role"] == "user" else "assistant",
            "content": msg["content"],
        })
    messages.append({"role": "user", "content": user_message})

    accumulated: list[str] = []
    try:
        async for token in llm.stream_chat(
            messages,
            system=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=2048,
        ):
            accumulated.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"
            await asyncio.sleep(0)

        _response_cache[cache_k] = "".join(accumulated)
        log.info("Cache STORE for query: %s", user_message[:60])
        yield "data: [DONE]\n\n"
    except Exception as exc:  # noqa: BLE001
        log.error("Generation failed across all providers: %s", exc)
        if accumulated:
            # Stream broke mid-response — tell the client and stop
            yield f"data: {json.dumps({'error': f'Stream interrupted: {exc}'})}\n\n"
        else:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "CardAI",
        "version": app.version,
        "cached_responses": len(_response_cache),
        "llm_providers": llm.provider_status(),
        "search_agent": bool(os.environ.get("TAVILY_API_KEY")),
    }


@app.get("/api/cache/stats")
async def cache_stats() -> dict:
    return {
        "cached_entries": len(_response_cache),
        "keys": list(_response_cache.keys()),
    }


@app.post("/api/cache/clear")
async def cache_clear() -> dict:
    _response_cache.clear()
    return {"cleared": True}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    session_id   = request.session_id or "default"
    messages     = request.messages
    user_message = messages[-1].content
    ui_region    = request.region  # 'US' | 'IN' | 'BOTH' | None

    history             = _sessions[session_id]
    collected_tokens: list[str] = []

    async def _event_generator() -> AsyncGenerator[str, None]:
        # 1. Classify intent locally (0 API calls)
        decision = classify_intent(user_message, region_override=ui_region)

        # 2. Run the web search agent if the regional cache is stale.
        #    Skipped entirely on response-cache hits — no point re-searching.
        if _cache_key(user_message, ui_region) not in _response_cache:
            yield f'data: {json.dumps({"heartbeat": "Checking live card data…"})}\n\n'
            search_query = SearchQuery(
                raw_query=user_message,
                region=decision.region,
                card_type_hint=decision.card_type,
                numeric_filters={},
            )
            try:
                fresh_cards = await fetch_cards_for_query(search_query)
                if fresh_cards:
                    log.info("Agent fetched %d fresh card(s)", len(fresh_cards))
            except Exception as exc:  # noqa: BLE001
                log.error("Search agent error (continuing with cached data): %s", exc)

        # 3. Hybrid retrieval (SQL + FAISS) over the now-fresh database
        try:
            retrieval     = await retrieve(user_message, region_override=ui_region)
            context_block = format_context_for_prompt(retrieval)
        except Exception as exc:  # noqa: BLE001
            log.error("Retrieval failed: %s", exc)
            context_block = "Card database temporarily unavailable."

        # 4. Stream generation through the provider chain
        async for chunk in stream_generation(user_message, history, context_block, ui_region):
            if chunk not in ("data: [DONE]\n\n",):
                try:
                    d = json.loads(chunk[6:].strip())
                    if "token" in d:
                        collected_tokens.append(d["token"])
                except (json.JSONDecodeError, ValueError):
                    pass
            yield chunk

        full_response = "".join(collected_tokens)
        if full_response:
            history.append({"role": "user",      "content": user_message})
            history.append({"role": "assistant",  "content": full_response})
        if len(history) > 40:
            _sessions[session_id] = history[-40:]

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Admin: ingest
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    url: str
    rebuild_index: bool = True


@app.post("/api/ingest")
async def ingest(req: IngestRequest) -> dict:
    from scripts.scraper import scrape  # deferred to avoid circular import

    start  = time.perf_counter()
    result = await scrape(req.url)

    if result.errors and not result.cards:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {result.errors}")

    upserted = db.upsert_cards(result.cards)

    if req.rebuild_index:
        all_cards = db.get_all_cards()
        vector_store.build_index(all_cards)

    elapsed = round(time.perf_counter() - start, 2)
    return {
        "scraped_cards":  len(result.cards),
        "upserted_rows":  len(upserted),
        "index_rebuilt":  req.rebuild_index,
        "elapsed_seconds": elapsed,
        "errors":         result.errors,
    }
