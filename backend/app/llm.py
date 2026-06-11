"""
Multi-provider LLM layer with automatic failover.

Fixes the Gemini free-tier quota problem by chaining several free
OpenAI-compatible providers. A request walks the chain until one
provider answers; rate-limited or failing providers are skipped.

Default chains (override via GENERATION_CHAIN / EXTRACTION_CHAIN env):
  1. groq:llama-3.3-70b-versatile   — 1,000 req/day free, ~300 tok/s
  2. cerebras:gpt-oss-120b          — 1M tokens/day free
  3. gemini:gemini-2.5-flash-lite   — last resort (low daily quota)

All providers are reached through their OpenAI-compatible endpoints,
so a single AsyncOpenAI client class covers every hop.
Providers whose API key is missing from .env are skipped silently.
"""

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
log = logging.getLogger("llm")

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

PROVIDER_KEY_ENV: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

DEFAULT_GENERATION_CHAIN = (
    "groq:llama-3.3-70b-versatile,"
    "cerebras:gpt-oss-120b,"
    "gemini:gemini-2.5-flash-lite"
)
DEFAULT_EXTRACTION_CHAIN = (
    "groq:llama-3.3-70b-versatile,"
    "cerebras:gpt-oss-120b,"
    "gemini:gemini-2.5-flash-lite"
)


class LLMUnavailableError(RuntimeError):
    """Every provider in the chain failed or was rate-limited."""


def _parse_chain(env_var: str, default: str) -> list[tuple[str, str]]:
    """Parse 'provider:model,provider:model' into [(provider, model), ...]."""
    raw = os.environ.get(env_var, default)
    chain: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        provider, model = entry.split(":", 1)
        provider = provider.strip().lower()
        if provider not in PROVIDER_BASE_URLS:
            log.warning("Unknown LLM provider '%s' in %s — skipping", provider, env_var)
            continue
        chain.append((provider, model.strip()))
    return chain


def available_chain(env_var: str, default: str) -> list[tuple[str, str]]:
    """Chain entries whose provider has an API key configured."""
    chain = [
        (p, m) for p, m in _parse_chain(env_var, default)
        if os.environ.get(PROVIDER_KEY_ENV[p])
    ]
    if not chain:
        log.warning(
            "No LLM provider keys found for %s — set GROQ_API_KEY / CEREBRAS_API_KEY / GEMINI_API_KEY",
            env_var,
        )
    return chain


def provider_status() -> dict[str, bool]:
    """Which providers have keys configured — surfaced on /health."""
    return {p: bool(os.environ.get(env)) for p, env in PROVIDER_KEY_ENV.items()}


def _client_for(provider: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ[PROVIDER_KEY_ENV[provider]],
        base_url=PROVIDER_BASE_URLS[provider],
    )


# ---------------------------------------------------------------------------
# Streaming chat (generation path)
# ---------------------------------------------------------------------------

async def stream_chat(
    messages: list[dict],
    system: str,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> AsyncGenerator[str, None]:
    """
    Stream completion tokens, walking the provider chain on failure.
    Fails over only before the first token — once a provider starts
    streaming, its errors propagate (a mid-stream restart would
    duplicate output for the user).
    """
    chain = available_chain("GENERATION_CHAIN", DEFAULT_GENERATION_CHAIN)
    if not chain:
        raise LLMUnavailableError("No LLM provider API keys configured")

    full_messages = [{"role": "system", "content": system}, *messages]
    errors: list[str] = []

    for provider, model in chain:
        client = _client_for(provider)
        started = False
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    started = True
                    yield chunk.choices[0].delta.content
            if started:
                return
            # Provider returned an empty stream — treat as failure, try next
            errors.append(f"{provider}/{model}: empty stream")
            log.warning("LLM %s/%s returned empty stream — trying next provider", provider, model)
        except Exception as exc:  # noqa: BLE001
            if started:
                raise
            errors.append(f"{provider}/{model}: {exc}")
            log.warning("LLM %s/%s failed (%s) — trying next provider", provider, model, exc)
        finally:
            await client.close()

    raise LLMUnavailableError("All LLM providers failed: " + " | ".join(errors))


# ---------------------------------------------------------------------------
# Non-streaming completion (extraction path)
# ---------------------------------------------------------------------------

async def complete(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> str:
    """Single-shot completion with provider failover. Used for structured extraction."""
    chain = available_chain("EXTRACTION_CHAIN", DEFAULT_EXTRACTION_CHAIN)
    if not chain:
        raise LLMUnavailableError("No LLM provider API keys configured")

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    errors: list[str] = []
    for provider, model in chain:
        client = _client_for(provider)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content or ""
            if text.strip():
                log.info("LLM completion served by %s/%s (%d chars)", provider, model, len(text))
                return text
            errors.append(f"{provider}/{model}: empty response")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider}/{model}: {exc}")
            log.warning("LLM %s/%s failed (%s) — trying next provider", provider, model, exc)
        finally:
            await client.close()

    raise LLMUnavailableError("All LLM providers failed: " + " | ".join(errors))
