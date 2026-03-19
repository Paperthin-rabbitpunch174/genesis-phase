"""
phase/llm.py — Unified LLM client for all PHASE components.

Single async function that all modules use. Handles:
  - OpenRouter API calls
  - Automatic fallback on empty responses
  - Budget tracking via callback
  - Timeout enforcement
  - Retry with exponential backoff (max 3 attempts)

Never call the OpenRouter API directly from any other module.
Always go through call_llm().

Usage:
    from llm import call_llm
    response = await call_llm(
        model="anthropic/claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=200,
        tag="coder",   # for budget tracking
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Callable, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = int(os.environ.get("PHASE_LLM_TIMEOUT", "60"))


# ── Cost tracking ─────────────────────────────────────────────────────────────

_usage_callbacks: list[Callable[[str, float, int, int], None]] = []


def register_usage_callback(fn: Callable[[str, float, int, int], None]) -> None:
    """
    Register a callback that fires after every LLM call.
    Signature: fn(tag, cost_usd, prompt_tokens, completion_tokens)
    """
    _usage_callbacks.append(fn)


def _fire_usage(tag: str, cost: float, prompt_tok: int, completion_tok: int) -> None:
    for fn in _usage_callbacks:
        try:
            fn(tag, cost, prompt_tok, completion_tok)
        except Exception:
            pass


# ── Model pricing ($/Mtok — rough estimates for budget tracking) ──────────────

_PRICE_MAP: dict[str, tuple[float, float]] = {
    # (input_price, output_price) per million tokens
    "anthropic/claude-opus-4-6":           (15.00, 75.00),
    "anthropic/claude-sonnet-4-6":         (3.00,  15.00),
    "anthropic/claude-haiku-4-5-20251001": (0.80,   4.00),
    "google/gemini-flash-1.5":             (0.075,  0.30),
    "google/gemini-2.5-pro-preview":       (1.25,  10.00),
    "meta-llama/llama-3.1-8b-instruct":    (0.06,   0.06),
    "openai/gpt-4.1":                      (2.00,   8.00),
}

def _estimate_cost(model: str, prompt_tok: int, completion_tok: int) -> float:
    inp, out = _PRICE_MAP.get(model, (3.00, 15.00))  # default to Sonnet pricing
    return prompt_tok / 1_000_000 * inp + completion_tok / 1_000_000 * out


# ── Main call ─────────────────────────────────────────────────────────────────

async def call_llm(
    model:       str,
    messages:    list[dict],
    max_tokens:  int  = 1000,
    temperature: float = 0.7,
    tag:         str  = "unknown",
    fallback_models: Optional[list[str]] = None,
    timeout:     int  = DEFAULT_TIMEOUT,
) -> str:
    """
    Call an LLM via OpenRouter. Returns the response text.
    Returns empty string on unrecoverable failure (never raises).

    Args:
        model:           Primary model string (from model_config.yaml)
        messages:        List of {"role": ..., "content": ...} dicts
        max_tokens:      Maximum response tokens
        temperature:     Sampling temperature
        tag:             Label for budget tracking (e.g. "coder", "solid_v1")
        fallback_models: Models to try if primary returns empty
        timeout:         Seconds before giving up
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.error("OPENROUTER_API_KEY not set")
        return ""

    models_to_try = [model] + (fallback_models or [])

    for attempt_model in models_to_try:
        result = await _single_call(
            api_key=api_key,
            model=attempt_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tag=tag,
            timeout=timeout,
        )
        if result:
            return result
        logger.warning("llm: empty response from %s, trying fallback", attempt_model)

    return ""


async def _single_call(
    api_key:     str,
    model:       str,
    messages:    list[dict],
    max_tokens:  int,
    temperature: float,
    tag:         str,
    timeout:     int,
    retries:     int = 2,
) -> str:
    headers = {
        "Authorization":  f"Bearer {api_key}",
        "Content-Type":   "application/json",
        "HTTP-Referer":   "https://github.com/EXOAI-1/phase",
        "X-Title":        "PHASE",
    }
    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }

    delay = 1.0
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    body = await resp.json()

            if resp.status != 200:
                logger.warning("llm: %s status %d: %s", model, resp.status,
                               body.get("error", {}).get("message", ""))
                if attempt < retries:
                    await asyncio.sleep(delay); delay *= 2
                continue

            # Extract text
            choices = body.get("choices", [])
            if not choices:
                return ""
            text = (choices[0].get("message") or {}).get("content", "")
            text = (text or "").strip()

            # Track usage
            usage = body.get("usage", {})
            pt    = usage.get("prompt_tokens", 0)
            ct    = usage.get("completion_tokens", 0)
            cost  = _estimate_cost(model, pt, ct)
            elapsed = time.time() - t0
            logger.debug("llm: %s [%s] %.1fs $%.5f pt=%d ct=%d",
                         model, tag, elapsed, cost, pt, ct)
            _fire_usage(tag, cost, pt, ct)

            return text

        except asyncio.TimeoutError:
            logger.warning("llm: timeout on %s (attempt %d)", model, attempt + 1)
        except Exception as exc:
            logger.warning("llm: error on %s: %s", model, exc)

        if attempt < retries:
            await asyncio.sleep(delay); delay *= 2

    return ""
