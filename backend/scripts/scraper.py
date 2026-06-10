"""
Async Playwright + Gemini ETL scraper.

Usage:
    python -m scripts.scraper --url "https://..." [--output cards.json]

The scraper:
1. Navigates to a bank's credit card catalog page with Playwright.
2. Extracts all visible text and structured list content.
3. Feeds the raw text to Gemini Flash with a strict JSON schema prompt.
4. Validates the response with Pydantic (CreditCard model).
5. Writes validated cards to a JSON file for downstream ingestion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout
from pydantic import ValidationError

# Import relative to the backend/ root when run as a module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import CreditCard, RewardMultipliers, ScraperResult

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("scraper")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_MODEL = os.environ.get("SCRAPER_MODEL", "gemini-2.5-flash-lite")
MAX_CHARS = 120_000                 # Token budget guard (Gemini Flash 1M context)
RETRY_LIMIT = 2
RETRY_DELAY = 10                    # seconds between Gemini retries


# ---------------------------------------------------------------------------
# Playwright: fetch raw page text
# ---------------------------------------------------------------------------

async def fetch_page_text(url: str, page: Page) -> str:
    """
    Navigate to *url* and extract meaningful text content.
    Strips script/style noise and collapses whitespace.
    """
    log.info("Navigating to %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except PWTimeout:
        log.warning("Page load timed out for %s — continuing with partial content", url)

    # Wait for any lazy-loaded card grids
    for selector in ["[class*='card']", "[class*='product']", "table", "ul"]:
        try:
            await page.wait_for_selector(selector, timeout=5_000)
            break
        except PWTimeout:
            continue

    # Extract text from the body (JS-rendered content included)
    raw_text: str = await page.evaluate(
        """() => {
            const remove = ['script', 'style', 'noscript', 'nav', 'footer', 'header'];
            remove.forEach(tag => document.querySelectorAll(tag).forEach(el => el.remove()));
            return document.body?.innerText || '';
        }"""
    )
    # Collapse excessive blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", raw_text).strip()
    log.info("Extracted %d characters from %s", len(cleaned), url)
    return cleaned[:MAX_CHARS]


# ---------------------------------------------------------------------------
# Gemini: parse raw text into structured cards
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
You are a precise financial data extraction engine.

Below is raw text scraped from a bank's credit card catalog page.
Extract ALL credit card products you can identify.

Return ONLY a valid JSON array. Each element must follow this exact schema:
[
  {{
    "name": "string — full card name",
    "issuer": "string — bank name",
    "annual_fee": number,
    "regular_apr_low": number,
    "regular_apr_high": number,
    "signup_bonus": "string",
    "signup_bonus_value_usd": number,
    "reward_multipliers": {{
      "travel": number,
      "dining": number,
      "groceries": number,
      "gas": number,
      "online_shopping": number,
      "other": number
    }},
    "lounge_access": boolean,
    "foreign_transaction_fee": number,
    "credit_score_required": "Excellent | Good | Fair | Poor",
    "source_url": "{source_url}",
    "description": "string — 2-4 sentence qualitative summary covering perks, ideal user, and differentiators"
  }}
]

Rules:
- If a numeric value is not mentioned, use 0 for fees and 1.0 for reward multipliers.
- APR ranges like "19.99%–29.99%" → annual_fee_low=19.99, regular_apr_high=29.99.
- Do NOT wrap the output in markdown fences or add commentary.
- If no cards are found, return an empty array [].

RAW TEXT:
{raw_text}
"""


def _parse_gemini_response(raw_response: str, source_url: str) -> list[CreditCard]:
    """Extract and validate card objects from Gemini's JSON output."""
    # Strip markdown fences if the model added them despite instructions
    clean = re.sub(r"^```(?:json)?\s*", "", raw_response.strip(), flags=re.MULTILINE)
    clean = re.sub(r"```$", "", clean.strip())

    try:
        data: list[dict[str, Any]] = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}\n---\n{clean[:500]}") from exc

    cards: list[CreditCard] = []
    for raw_card in data:
        raw_card.setdefault("source_url", source_url)
        try:
            card = CreditCard(**raw_card)
            cards.append(card)
        except ValidationError as exc:
            log.warning("Card validation failed — skipping: %s", exc)

    return cards


async def extract_cards_with_gemini(raw_text: str, source_url: str) -> list[CreditCard]:
    """Send raw_text to Gemini Flash and return validated CreditCard objects."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY is not set in the environment.")

    client = genai.Client(api_key=api_key)
    prompt = EXTRACTION_PROMPT.format(raw_text=raw_text, source_url=source_url)

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            log.info("Calling Gemini Flash (attempt %d/%d)…", attempt, RETRY_LIMIT)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,   # deterministic for structured extraction
                    max_output_tokens=8192,
                ),
            )
            raw_text_response = response.text
            cards = _parse_gemini_response(raw_text_response, source_url)
            log.info("Gemini returned %d valid cards", len(cards))
            return cards

        except ValueError as exc:
            log.error("Parse error on attempt %d: %s", attempt, exc)
            if attempt == RETRY_LIMIT:
                raise
            await asyncio.sleep(RETRY_DELAY)

        except Exception as exc:  # noqa: BLE001
            log.error("Gemini API error on attempt %d: %s", attempt, exc)
            if attempt == RETRY_LIMIT:
                raise
            await asyncio.sleep(RETRY_DELAY * attempt)  # exponential-ish backoff

    return []  # unreachable but satisfies type checker


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def scrape(url: str) -> ScraperResult:
    """Full ETL pipeline for a single URL."""
    errors: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        try:
            raw_text = await fetch_page_text(url, page)
        except Exception as exc:  # noqa: BLE001
            log.error("Page fetch failed: %s", exc)
            errors.append(f"Page fetch: {exc}")
            raw_text = ""
        finally:
            await browser.close()

    cards: list[CreditCard] = []
    if raw_text:
        try:
            cards = await extract_cards_with_gemini(raw_text, url)
        except Exception as exc:  # noqa: BLE001
            log.error("Gemini extraction failed: %s", exc)
            errors.append(f"Gemini extraction: {exc}")

    return ScraperResult(url=url, cards=cards, errors=errors)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="CardAI ETL Scraper")
    parser.add_argument("--url", required=True, help="Target bank catalog URL")
    parser.add_argument(
        "--output",
        default="scraped_cards.json",
        help="Output JSON file path (default: scraped_cards.json)",
    )
    args = parser.parse_args()

    start = time.perf_counter()
    result = await scrape(args.url)
    elapsed = time.perf_counter() - start

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "url": result.url,
                "card_count": len(result.cards),
                "elapsed_seconds": round(elapsed, 2),
                "errors": result.errors,
                "cards": [c.model_dump() for c in result.cards],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    log.info(
        "Done — %d cards extracted in %.2fs → %s",
        len(result.cards),
        elapsed,
        output_path,
    )
    if result.errors:
        log.warning("Errors encountered: %s", result.errors)


if __name__ == "__main__":
    asyncio.run(main())
