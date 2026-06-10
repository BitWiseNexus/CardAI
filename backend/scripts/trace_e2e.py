"""
Full end-to-end API call audit — traces every Gemini call from query to streamed response.
Counts tokens and API calls without going through the HTTP server.
"""
import os, sys, asyncio, json
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from google.genai import models as _m

total_calls: list[dict] = []

_orig_gen    = _m.Models.generate_content
_orig_embed  = _m.Models.embed_content
_orig_stream = _m.Models.generate_content_stream

def _gen(self, *a, **kw):
    total_calls.append({"type": "generate_content", "model": kw.get("model","?")})
    return _orig_gen(self, *a, **kw)

def _embed(self, *a, **kw):
    total_calls.append({"type": "embed_content", "model": kw.get("model","?")})
    return _orig_embed(self, *a, **kw)

def _stream(self, *a, **kw):
    total_calls.append({"type": "generate_stream", "model": kw.get("model","?")})
    return _orig_stream(self, *a, **kw)

_m.Models.generate_content        = _gen
_m.Models.embed_content           = _embed
_m.Models.generate_content_stream = _stream

from app.router import retrieve, format_context_for_prompt
from app.main import stream_generation

CASES = [
    # (label, query, expect_sql, expect_vec)
    ("No annual fee [SQL only]",           "Which cards have no annual fee?",                     True,  False),
    ("Lounge access [SQL only]",           "Best card for airport lounge access",                 True,  False),
    ("Dining rewards [Vector only]",       "What is the best card for dining?",                   False, True),
    ("Hybrid: APR + travel",               "Cards with APR under 22% and good travel rewards",    True,  True),
    ("Hybrid: bonus over $500",            "Show me cards with signup bonus over $500",            True,  False),
    ("Session follow-up [vector]",         "Compare Sapphire Preferred vs Sapphire Reserve",      False, True),
]

async def run_case(label: str, query: str, expect_sql: bool, expect_vec: bool) -> None:
    total_calls.clear()

    # --- Retrieval ---
    result   = await retrieve(query)
    ctx      = format_context_for_prompt(result)
    decision = result["decision"]

    retrieval_calls = list(total_calls)
    total_calls.clear()

    # --- Generation (consume full stream) ---
    tokens = []
    async for chunk in stream_generation(query, [], ctx):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            try:
                d = json.loads(chunk[6:])
                if "token" in d:
                    tokens.append(d["token"])
            except Exception:
                pass

    generation_calls = list(total_calls)
    all_calls        = retrieval_calls + generation_calls
    response_preview = "".join(tokens)[:120].replace("\n", " ")

    ok_sql = (len(result["sql_results"]) > 0) == expect_sql or not expect_sql
    ok_vec = (len(result["vector_results"]) > 0) == expect_vec or not expect_vec

    print(f"{'PASS' if ok_sql and ok_vec else 'FAIL'}  {label}")
    print(f"      mode={decision.mode} | sql_filter={decision.sql_filter}")
    print(f"      sql_rows={len(result['sql_results'])} | vec_rows={len(result['vector_results'])}")
    print(f"      API calls total={len(all_calls)}: retrieval={retrieval_calls} generation={generation_calls}")
    print(f"      Response: {response_preview}...")
    print()

async def main():
    print("=" * 70)
    print("CardAI — End-to-End API Call Audit")
    print("=" * 70)
    print()
    for args in CASES:
        await run_case(*args)
    print("Done.")

asyncio.run(main())
