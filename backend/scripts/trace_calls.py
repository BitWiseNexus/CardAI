"""Traces exactly how many Gemini API calls fire per query type."""
import os, sys, asyncio
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

# Monkey-patch before any app imports
from google.genai import models as _m

call_log: list[tuple] = []
_orig_gen    = _m.Models.generate_content
_orig_embed  = _m.Models.embed_content
_orig_stream = _m.Models.generate_content_stream

def _gen(self, *a, **kw):
    call_log.append(("generate_content", kw.get("model", a[0] if a else "?")))
    return _orig_gen(self, *a, **kw)

def _embed(self, *a, **kw):
    call_log.append(("embed_content", kw.get("model", a[0] if a else "?")))
    return _orig_embed(self, *a, **kw)

def _stream(self, *a, **kw):
    call_log.append(("generate_stream", kw.get("model", a[0] if a else "?")))
    return _orig_stream(self, *a, **kw)

_m.Models.generate_content        = _gen
_m.Models.embed_content           = _embed
_m.Models.generate_content_stream = _stream

from app.router import retrieve, format_context_for_prompt

QUERIES = [
    "Which cards have no annual fee?",
    "Best card for airport lounge access",
    "What is the best card for dining?",
    "Cards with APR under 22% and travel rewards",
    "Compare Sapphire Preferred vs Sapphire Reserve",
    "Show me cards with signup bonus over $500",
]

async def test(query: str) -> None:
    call_log.clear()
    result = await retrieve(query)
    ctx = format_context_for_prompt(result)
    mode = result["decision"].mode
    filt = result["decision"].sql_filter
    nsql = len(result["sql_results"])
    nvec = len(result["vector_results"])
    napi = len(call_log)
    print(f"Q: {query}")
    print(f"   mode={mode} | filter={filt}")
    print(f"   sql_rows={nsql} | vec_rows={nvec}")
    print(f"   API calls ({napi}): {call_log}")
    print()

async def main():
    for q in QUERIES:
        await test(q)

asyncio.run(main())
