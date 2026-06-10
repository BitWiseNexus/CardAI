"""
CardAI — FastAPI application entry point.

Endpoints:
  GET  /health          → liveness check
  POST /api/chat        → session-aware hybrid RAG chatbot with SSE streaming
  POST /api/ingest      → trigger scraper + upsert + FAISS rebuild (admin)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app import db, vector_store
from app.models import ChatMessage, ChatRequest
from app.router import format_context_for_prompt, retrieve

load_dotenv()
log = logging.getLogger("main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CardAI",
    description="Automated Credit Card Analytics & Hybrid RAG Chatbot",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store: {session_id: [ChatMessage]}
# Production should use Redis or Supabase; this is sufficient for Phase 1 dev.
_sessions: dict[str, list[dict]] = defaultdict(list)

GENERATION_MODEL = "gemini-2.5-flash"
MAX_HISTORY_TOKENS = 4096   # Approximate token budget for history buffer


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are CardAI, an expert financial advisor specialising in US credit cards.
You have access to a live database of credit card products retrieved for this specific query.

Guidelines:
1. Answer ONLY from the provided card data — never hallucinate fees, APRs, or rewards.
2. If the data is insufficient to fully answer, say so clearly and suggest what additional information the user could provide.
3. Present comparisons in structured markdown tables when showing multiple cards.
4. Always mention the annual fee and signup bonus prominently — these are the highest-stakes numbers for consumers.
5. If the user asks a follow-up question about "the first card" or "that card", use the conversation history to determine which card they mean.
6. End each response with a brief "Bottom line:" summary of your recommendation.
"""


# ---------------------------------------------------------------------------
# Streaming generation
# ---------------------------------------------------------------------------

async def stream_generation(
    user_message: str,
    history: list[dict],
    context_block: str,
) -> AsyncGenerator[str, None]:
    """
    Yield SSE-formatted chunks from Gemini's streaming API.

    SSE format: each chunk is  "data: <json_payload>\n\n"
    The final chunk is         "data: [DONE]\n\n"
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        yield 'data: {"error": "GEMINI_API_KEY not configured on server"}\n\n'
        return

    client = genai.Client(api_key=api_key)

    # Build the conversation turns for Gemini
    contents: list[genai_types.Content] = []

    # Inject retrieved card context as a system-style user turn before history
    context_turn = genai_types.Content(
        role="user",
        parts=[genai_types.Part(
            text=f"[RETRIEVED CARD DATA — use this as your factual source]\n\n{context_block}"
        )],
    )
    context_ack = genai_types.Content(
        role="model",
        parts=[genai_types.Part(text="Understood. I will base my response on the retrieved card data above.")],
    )
    contents.extend([context_turn, context_ack])

    # Append trimmed conversation history
    for msg in history[-20:]:   # hard cap at last 20 turns to control token spend
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            genai_types.Content(role=role, parts=[genai_types.Part(text=msg["content"])])
        )

    # Append the current user message
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_message)],
        )
    )

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
                payload = json.dumps({"token": text})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0)   # yield control so the event loop can flush

    except Exception as exc:  # noqa: BLE001
        log.error("Gemini streaming error: %s", exc)
        error_payload = json.dumps({"error": str(exc)})
        yield f"data: {error_payload}\n\n"

    finally:
        yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "CardAI", "version": app.version}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """
    Session-aware SSE streaming chat endpoint.

    Flow:
    1. Extract the latest user message.
    2. Run the hybrid RAG router (SQL + FAISS retrieval).
    3. Format retrieval results into a context block.
    4. Stream the Gemini-generated response token-by-token via SSE.
    5. Persist the completed exchange to the session history.
    """
    session_id = request.session_id or "default"
    messages = request.messages
    user_message = messages[-1].content

    # Retrieve relevant card context
    try:
        retrieval = await retrieve(user_message)
        context_block = format_context_for_prompt(retrieval)
    except Exception as exc:  # noqa: BLE001
        log.error("Retrieval pipeline failed: %s", exc)
        context_block = "Card database temporarily unavailable."

    # Load session history
    history = _sessions[session_id]

    # Collect the full response text while streaming so we can store it
    collected_tokens: list[str] = []

    async def _event_generator() -> AsyncGenerator[str, None]:
        async for chunk in stream_generation(user_message, history, context_block):
            if chunk != "data: [DONE]\n\n":
                try:
                    data = json.loads(chunk[len("data: "):].strip())
                    if "token" in data:
                        collected_tokens.append(data["token"])
                except (json.JSONDecodeError, IndexError):
                    pass
            yield chunk

        # After streaming completes, persist to session
        full_response = "".join(collected_tokens)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": full_response})
        # Cap history at 40 messages to bound memory usage
        if len(history) > 40:
            _sessions[session_id] = history[-40:]

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # Disable Nginx buffering if behind a proxy
        },
    )


# ---------------------------------------------------------------------------
# Admin: ingest endpoint (Phase 1 convenience — protect in prod with auth)
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    url: str
    rebuild_index: bool = True


@app.post("/api/ingest")
async def ingest(req: IngestRequest) -> dict:
    """
    Trigger a scrape + Supabase upsert + FAISS index rebuild for a given URL.
    Runs synchronously (may take 30–60s). Add a background task queue in prod.
    """
    from scripts.scraper import scrape  # Local import to avoid circular dep at startup

    start = time.perf_counter()

    result = await scrape(req.url)
    if result.errors and not result.cards:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {result.errors}")

    upserted = db.upsert_cards(result.cards)

    if req.rebuild_index:
        all_cards = db.get_all_cards()
        vector_store.build_index(all_cards)

    elapsed = round(time.perf_counter() - start, 2)
    return {
        "scraped_cards": len(result.cards),
        "upserted_rows": len(upserted),
        "index_rebuilt": req.rebuild_index,
        "elapsed_seconds": elapsed,
        "errors": result.errors,
    }
