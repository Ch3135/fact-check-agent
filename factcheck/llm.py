"""Small wrapper around the Vercel AI Gateway (OpenAI compatible API).

On Vercel the gateway accepts the deployment's OIDC token, so no provider
keys are needed. Locally, set AI_GATEWAY_API_KEY instead.
"""
import json
import os
import re

import httpx

GATEWAY_URL = os.getenv("AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1")


class LLMError(Exception):
    pass


def _token(oidc_header: str | None) -> str:
    token = os.getenv("AI_GATEWAY_API_KEY") or oidc_header or os.getenv("VERCEL_OIDC_TOKEN")
    if not token:
        raise LLMError(
            "No gateway credentials. Set AI_GATEWAY_API_KEY, or run on Vercel "
            "where the OIDC token is provided automatically."
        )
    return token


async def chat(
    model: str,
    messages: list[dict],
    *,
    oidc: str | None = None,
    json_mode: bool = False,
    temperature: float = 0,
    timeout: float = 90,
) -> dict:
    """Send one chat completion and return the raw message plus any citations."""
    body = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {_token(oidc)}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{GATEWAY_URL}/chat/completions", json=body, headers=headers)

    if r.status_code != 200:
        raise LLMError(f"{model} returned {r.status_code}: {r.text[:300]}")

    data = r.json()
    msg = data["choices"][0]["message"]
    return {
        "content": msg.get("content") or "",
        "citations": _collect_citations(data, msg),
    }


def _collect_citations(data: dict, msg: dict) -> list[dict]:
    """Search models report sources in a few different shapes. Gather them all."""
    seen, out = set(), []

    def add(url, title=""):
        if url and url.startswith("http") and url not in seen:
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
