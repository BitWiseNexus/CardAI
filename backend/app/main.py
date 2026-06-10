"""
CardAI — FastAPI application entry point.

Endpoints:
  GET  /health          → liveness check
  GET  /api/cache/stats → how many responses are cached (quota audit)
  POST /api/chat        → session-aware hybrid RAG chatbot with SSE streaming
  POST /api/ingest      → trigger scraper + upsert + FAISS rebuild (admin)

Quota-conservation measures
────────────────────────────
1. Local regex router    → 0 Gemini calls for intent classification (was 1)
2. SQL-only queries      → 0 embedding calls (router skips FAISS)
3. Response cache        → repeated identical queries cost 0 API calls
4. 429 retry-backoff     → honours Retry-After instead of failing hard
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app import db, vector_store
from app.models import ChatRequest
from app.router import format_context_for_prompt, retrieve

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
    description="Automated Credit Card Analytics & Hybrid RAG Chatbot",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "gemini-2.5-flash-lite")

# In-memory session store
_sessions: dict[str, list[dict]] = defaultdict(list)

# Response cache: {cache_key: full_response_text}
# Keyed by hash(normalized_query).  Cleared by POST /api/cache/clear.
_response_cache: dict[str, str] = {}


def _cache_key(user_message: str) -> str:
    normalized = user_message.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are CardAI, an expert financial advisor specialising in US credit cards.
You have access to a live database of credit card products retrieved for this specific query.

Guidelines:
1. Answer ONLY from the provided card data — never hallucinate fees, APRs, or rewards.
2. If the data is insufficient to fully answer, say so clearly.
3. Present comparisons in structured markdown tables when showing multiple cards.
4. Always mention the annual fee and signup bonus prominently.
5. If the user references "that card" or "the first card", use conversation history.
6. End each response with a brief "Bottom line:" summary.
"""

# ---------------------------------------------------------------------------
# Streaming generation with 429 retry-backoff
# ---------------------------------------------------------------------------

_RETRY_AFTER_RE = re.compile(r'retry in (\d+(?:\.\d+)?)s', re.IGNORECASE)
MAX_RETRIES = 2


async def stream_generation(
    user_message: str,
    history: list[dict],
    context_block: str,
) -> AsyncGenerator[str, None]:
    """
    Stream SSE chunks from Gemini. On 429, waits Retry-After seconds then retries
    (up to MAX_RETRIES). Caches the complete response so duplicate queries are free.
    """
    cache_k = _cache_key(user_message)
    if cache_k in _response_cache:
        log.info("Cache HIT for query: %s", user_message[:60])
        cached = _response_cache[cache_k]
        # Re-stream cached text token-by-token (no API call)
        for word in cached.split(" "):
            yield f'data: {json.dumps({"token": word + " "})}\n\n'
            await asyncio.sleep(0)
        yield "data: [DONE]\n\n"
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        yield 'data: {"error": "GEMINI_API_KEY not configured"}\n\n'
        yield "data: [DONE]\n\n"
        return

    client = genai.Client(api_key=api_key)

    # Build Gemini content list
    contents: list[genai_types.Content] = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(
                text=f"[RETRIEVED CARD DATA — use as factual source]\n\n{context_block}"
            )],
        ),
        genai_types.Content(
            role="model",
            parts=[genai_types.Part(text="Understood. I will base my response on the retrieved card data above.")],
        ),
    ]
    for msg in history[-20:]:
        contents.append(genai_types.Content(
            role="user" if msg["role"] == "user" else "model",
            parts=[genai_types.Part(text=msg["content"])],
        ))
    contents.append(genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message)],
    ))

    accumulated: list[str] = []
    last_error: str = ""

    for attempt in range(1, MAX_RETRIES + 2):
        accumulated.clear()
        try:
            response_stream = client.models.generate_content_stream(
                model=GENERATION_MODEL,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=2048,
                ),
            )
            for chunk in response_stream:
                text = chunk.text
                if text:
                    accumulated.append(text)
                    yield f"data: {json.dumps({'token': text})}\n\n"
                    await asyncio.sleep(0)

            # Success — cache and exit
            _response_cache[cache_k] = "".join(accumulated)
            log.info("Cache STORE for query: %s", user_message[:60])
            yield "data: [DONE]\n\n"
            return

        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            is_daily_quota  = "PerDay" in last_error or "per_day" in last_error.lower()
            is_minute_quota = ("429" in last_error or "RESOURCE_EXHAUSTED" in last_error) and not is_daily_quota
            is_unavailable  = "503" in last_error or "UNAVAILABLE" in last_error

            # Only retry transient errors (per-minute 429 or 503).
            # Daily quota exhaustion is permanent until midnight — don't waste calls.
            if (is_minute_quota or is_unavailable) and attempt <= MAX_RETRIES:
                m = _RETRY_AFTER_RE.search(last_error)
                wait = float(m.group(1)) + 2 if m else 35.0
                log.warning(
                    "Gemini %s on attempt %d/%d — waiting %.0fs",
                    "429/min" if is_minute_quota else "503", attempt, MAX_RETRIES + 1, wait,
                )
                yield f'data: {json.dumps({"heartbeat": f"Rate limited — retrying in {int(wait)}s"})}\n\n'
                await asyncio.sleep(wait)
                continue

            # Non-retriable or out of retries
            log.error("Gemini generation failed after %d attempts: %s", attempt, last_error)
            yield f"data: {json.dumps({'error': last_error})}\n\n"
            yield "data: [DONE]\n\n"
            return


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

    try:
        retrieval      = await retrieve(user_message)
        context_block  = format_context_for_prompt(retrieval)
    except Exception as exc:  # noqa: BLE001
        log.error("Retrieval failed: %s", exc)
        context_block = "Card database temporarily unavailable."

    history             = _sessions[session_id]
    collected_tokens: list[str] = []

    async def _event_generator() -> AsyncGenerator[str, None]:
        async for chunk in stream_generation(user_message, history, context_block):
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
