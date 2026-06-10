"""
Probes working models to find their daily limits and picks the best one.
Also checks embedding model status.
"""
import os, sys, time, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import urllib.request, urllib.error

KEY = os.environ["GEMINI_API_KEY"]
BASE = "https://generativelanguage.googleapis.com/v1beta/models"

def call_model(model: str, prompt: str = "Respond with exactly: OK") -> tuple[bool, str, str]:
    """Returns (success, response_text_or_error, daily_limit)."""
    url  = f"{BASE}/{model}:generateContent?key={KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"maxOutputTokens": 20}}).encode()
    req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            parts = data["candidates"][0]["content"].get("parts", [])
            # Thinking models emit thought parts before the text part
            text = next((p["text"] for p in parts if "text" in p and not p.get("thought")), "")
            return True, text.strip(), "unknown"
    except urllib.error.HTTPError as e:
        err = json.loads(e.read()).get("error", {})
        daily = "?"
        for d in err.get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in v.get("quotaId", ""):
                    daily = v.get("quotaValue", "?")
        return False, err.get("message", "")[:80], daily

def check_embedding(model: str = "models/gemini-embedding-001") -> bool:
    url  = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent?key={KEY}"
    body = json.dumps({"model": model, "content": {"parts": [{"text": "test"}]}}).encode()
    req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            dim  = len(data["embedding"]["values"])
            return True, f"dim={dim}"
    except urllib.error.HTTPError as e:
        err = json.loads(e.read()).get("error", {})
        return False, err.get("message", "")[:60]

# ── Generation models ──────────────────────────────────────────────────────
WORKING = [
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
]

print("Testing working models with a real prompt to surface actual limits:")
print(f"{'MODEL':<35} {'OK?':<6} {'DAILY_LIMIT':>12}  RESPONSE")
print("-" * 75)
results = []
for model in WORKING:
    ok, text, limit = call_model(model, "What is 2+2? Answer in one word.")
    results.append((model, ok, limit, text))
    status = "OK" if ok else "FAIL"
    print(f"{model:<35} {status:<6} {limit:>12}  {text[:40]}")
    time.sleep(1)

# ── Embedding model ─────────────────────────────────────────────────────────
print()
print("Embedding model check:")
ok, info = check_embedding()
print(f"  gemini-embedding-001 -> {'OK' if ok else 'FAIL'} | {info}")

# ── Recommendation ─────────────────────────────────────────────────────────
print()
working_gen = [r for r in results if r[1]]
if working_gen:
    # Prefer newest/most capable: 3.5 > 3.1 > 3 > 2.5 > flash-latest
    priority = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
                "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-flash-lite-latest"]
    best = next((r[0] for p in priority for r in working_gen if r[0] == p), working_gen[0][0])
    print(f"RECOMMENDATION: Switch GENERATION_MODEL to '{best}'")
