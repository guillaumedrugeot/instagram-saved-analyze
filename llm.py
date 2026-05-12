#!/usr/bin/env python3
"""
Unified LLM client. Reads LLM_PROVIDER and LLM_API_KEY from environment.

Supported providers:
  mistral  — mistralai SDK    (default model: mistral-small-latest)
  gemini   — google-genai SDK (default model: gemini-2.0-flash)
  openai   — openai SDK       (default model: gpt-4o-mini)
  claude   — anthropic SDK    (default model: claude-sonnet-4-6)

Usage:
    from llm import generate
    text = generate("Summarise this reel...")
    json_text = generate("Classify this:", json_mode=True)
"""

import os
import time


# ── Default models per provider ───────────────────────────────────────────────

DEFAULTS = {
    "mistral": "mistral-small-latest",
    "gemini":  "gemini-2.0-flash",
    "openai":  "gpt-4o-mini",
    "claude":  "claude-sonnet-4-6",
}


def _provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "mistral").lower()
    if p not in DEFAULTS:
        raise ValueError(f"Unknown LLM_PROVIDER={p!r}. Choose: {list(DEFAULTS)}")
    return p


def _model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULTS[_provider()])


def _api_key() -> str:
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise EnvironmentError("LLM_API_KEY is not set. Add it to config.env.")
    return key


# ── Provider implementations ──────────────────────────────────────────────────

def _call_mistral(prompt: str, json_mode: bool, image_paths: list = None) -> str:
    import base64
    from mistralai.client import Mistral
    client = Mistral(api_key=_api_key())
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    if image_paths:
        content = []
        for path in image_paths:
            raw = open(path, "rb").read()
            b64 = base64.b64encode(raw).decode()
            suffix = str(path).rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp",
                    "gif": "image/gif"}.get(suffix, "image/jpeg")
            content.append({"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}})
        content.append({"type": "text", "text": prompt})
    else:
        content = prompt

    resp = client.chat.complete(
        model=_model(),
        messages=[{"role": "user", "content": content}],
        **kwargs,
    )
    return resp.choices[0].message.content


def _call_gemini(prompt: str, json_mode: bool, image_paths: list = None) -> str:
    from google import genai
    client = genai.Client(api_key=_api_key())
    config = {"response_mime_type": "application/json"} if json_mode else None
    resp = client.models.generate_content(
        model=_model(),
        contents=prompt,
        config=config,
    )
    return resp.text


def _call_openai(prompt: str, json_mode: bool, image_paths: list = None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=_api_key())
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return resp.choices[0].message.content


def _call_claude(prompt: str, json_mode: bool, image_paths: list = None) -> str:
    import base64
    import anthropic
    client = anthropic.Anthropic(api_key=_api_key())

    system = "Respond with valid JSON only. No explanation, no markdown fences." if json_mode else anthropic.NOT_GIVEN

    if image_paths:
        content = []
        for path in image_paths:
            raw = open(path, "rb").read()
            b64 = base64.b64encode(raw).decode()
            suffix = str(path).rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "png": "image/png", "webp": "image/webp",
                    "gif": "image/gif"}.get(suffix, "image/jpeg")
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        content.append({"type": "text", "text": prompt})
    else:
        content = prompt

    resp = client.messages.create(
        model=_model(),
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return resp.content[0].text


_PROVIDERS = {
    "mistral": _call_mistral,
    "gemini":  _call_gemini,
    "openai":  _call_openai,
    "claude":  _call_claude,
}


# ── Public API ────────────────────────────────────────────────────────────────

def generate(prompt: str, json_mode: bool = False,
             image_paths: list = None, retries: int = 4) -> str:
    """
    Call the configured LLM. Retries on rate-limit (429) errors with backoff.

    Args:
        prompt:    The user prompt to send.
        json_mode: Request a JSON response (provider-specific mechanism).
        retries:   Max retry attempts on rate-limit errors.

    Returns:
        The model's text response.

    Raises:
        RuntimeError: If rate-limited after all retries, or on other errors.
    """
    import re
    provider = _provider()
    call_fn = _PROVIDERS[provider]

    kwargs = {"image_paths": image_paths} if (image_paths and provider in ("mistral", "claude")) else {}

    for attempt in range(retries + 1):
        try:
            return call_fn(prompt, json_mode, **kwargs)
        except Exception as e:
            msg = str(e)
            is_rate_limit = any(s in msg for s in ("429", "RESOURCE_EXHAUSTED", "rate_limit", "RateLimitError"))
            if is_rate_limit and attempt < retries:
                m = re.search(r"retry[^0-9]*(\d+)", msg, re.IGNORECASE)
                wait = int(m.group(1)) + 2 if m else 2 ** (attempt + 3)
                print(f"[llm] Rate limited ({provider}) — retrying in {wait}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
            else:
                raise RuntimeError(f"LLM call failed ({provider}): {e}") from e

    raise RuntimeError(f"LLM rate limit exceeded after {retries} retries")
