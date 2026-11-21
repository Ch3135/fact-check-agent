"""Small wrapper around the Vercel AI Gateway (OpenAI compatible API).

On Vercel the gateway accepts the deployment's OIDC token, so no provider
keys are needed. Locally, set AI_GATEWAY_API_KEY instead.
"""
import asyncio
import json
import os
import re

import httpx

# Any OpenAI compatible endpoint works here; the gateway is the default.
GATEWAY_URL = os.getenv("LLM_BASE_URL", "https://ai-gateway.vercel.sh/v1")


class LLMError(Exception):
    pass


def _token(oidc_header: str | None) -> str:
    token = os.getenv("LLM_API_KEY") or os.getenv("AI_GATEWAY_API_KEY") or oidc_header or os.getenv("VERCEL_OIDC_TOKEN")
    if not token:
        raise LLMError(
            "No gateway credentials. Set AI_GATEWAY_API_KEY, or run on Vercel "
            "where the OIDC token is provided automatically."
        )
    return token


# Optional backup model, any OpenAI compatible endpoint. Off unless set.
FALLBACK_URL = os.getenv("FALLBACK_LLM_BASE_URL")
FALLBACK_KEY = os.getenv("FALLBACK_LLM_API_KEY", "none")
FALLBACK_MODEL = os.getenv("FALLBACK_LLM_MODEL", "openai")


async def _post(url: str, token: str, body: dict, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            f"{url}/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}
        )


async def chat(
    model: str,
    messages: list[dict],
    *,
    oidc: str | None = None,
    json_mode: bool = False,
    temperature: float = 0,
    timeout: float = 90,
    allow_fallback: bool = True,
) -> dict:
    """Send one chat completion and return the text plus any citations.

    If the main endpoint refuses the request (no credits, auth, rate limit)
    and FALLBACK_LLM_BASE_URL is set, the same prompt goes to the fallback.
    """
    body = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    error = None
    try:
        r = await _post(GATEWAY_URL, _token(oidc), body, timeout)
        if r.status_code == 200:
            return _unpack(r.json())
        error = f"{model} returned {r.status_code}: {r.text[:300]}"
    except (httpx.HTTPError, LLMError) as exc:
        error = f"{model} failed: {exc}"

    if not (allow_fallback and FALLBACK_URL):
        raise LLMError(error)

    body["model"] = FALLBACK_MODEL
    for attempt in range(3):
        try:
            r = await _post(FALLBACK_URL, FALLBACK_KEY, body, timeout)
        except httpx.HTTPError as exc:
            error = f"fallback failed: {exc}"
            continue
        if r.status_code == 200:
            return _unpack(r.json(), fallback=True)
        error = f"fallback returned {r.status_code}: {r.text[:200]}"
        if r.status_code != 429:
            break
        await asyncio.sleep(3 * (attempt + 1))
    raise LLMError(error)


def _unpack(data: dict, fallback: bool = False) -> dict:
    msg = data["choices"][0]["message"]
    return {
        "content": msg.get("content") or "",
        "citations": _collect_citations(data, msg),
        "fallback": fallback,
    }


def _collect_citations(data: dict, msg: dict) -> list[dict]:
    """Search models report sources in a few different shapes. Gather them all."""
    seen, out = set(), []

    def add(url, title=""):
        # skip Google redirect links, they are useless to a reader
        if url and url.startswith("http") and "google.com/goto" not in url and url not in seen:
            seen.add(url)
            out.append({"url": url, "title": title or ""})

    for ann in msg.get("annotations") or []:
        c = ann.get("url_citation") or ann
        add(c.get("url"), c.get("title"))
    for item in data.get("search_results") or []:
        add(item.get("url"), item.get("title"))
    for url in data.get("citations") or []:
        add(url if isinstance(url, str) else url.get("url"))
    # last resort: plain links in the text
    for url in re.findall(r"https?://[^\s\)\]\"'>]+", msg.get("content") or ""):
        add(url.rstrip(".,;"))
    return out


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in code fences or add a sentence. Strip that."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("Model did not return JSON")
    return json.loads(text[start : end + 1])
