"""Model calls. Two OpenAI compatible providers are supported:

  gateway  Vercel AI Gateway. On Vercel it accepts the deployment's OIDC
           token, locally it needs AI_GATEWAY_API_KEY.
  groq     Groq, with GROQ_API_KEY. Its gpt-oss models can also run a live
           web search (browser_search tool), which we use when the cheaper
           sources don't settle a claim.

Gateway is tried first when it is configured. If it refuses (no credits,
bad auth) it is skipped for a few minutes and Groq takes over.
"""
import asyncio
import json
import os
import re
import time

import httpx

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1"
GROQ_URL = "https://api.groq.com/openai/v1"

MODELS = {
    "gateway": {
        "extract": os.getenv("EXTRACT_MODEL", "openai/gpt-4.1-mini"),
        "judge": os.getenv("JUDGE_MODEL", "openai/gpt-4.1"),
        "search": os.getenv("SEARCH_MODEL", "perplexity/sonar"),
    },
    # Groq's free tier allows about 8k tokens a minute per model, so each
    # task has backups that are tried when the first one is rate limited.
    "groq": {
        "extract": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
        "judge": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        "search": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
    },
}

_skip_until: dict[str, float] = {}


class LLMError(Exception):
    pass


def _credentials(oidc: str | None) -> list[tuple[str, str, str]]:
    """(name, base_url, token) for every provider that is set up, in order."""
    out = []
    gw = os.getenv("AI_GATEWAY_API_KEY") or oidc or os.getenv("VERCEL_OIDC_TOKEN")
    if gw and os.getenv("DISABLE_GATEWAY") != "1":
        out.append(("gateway", GATEWAY_URL, gw))
    if os.getenv("GROQ_API_KEY"):
        out.append(("groq", GROQ_URL, os.environ["GROQ_API_KEY"]))
    return out


def available(oidc: str | None = None) -> list[str]:
    now = time.time()
    return [name for name, _, _ in _credentials(oidc) if _skip_until.get(name, 0) < now]


async def _post(url: str, token: str, body: dict, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            f"{url}/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}
        )


def _wait_hint(r: httpx.Response) -> float:
    try:
        return float(r.headers.get("retry-after", "5"))
    except ValueError:
        return 5.0


async def chat(
    task: str,
    messages: list[dict],
    *,
    oidc: str | None = None,
    json_mode: bool = False,
    only: str | None = None,
    web_search: bool = False,
    timeout: float = 20,
) -> dict:
    """Run one completion for a task ("extract", "judge" or "search").

    Returns {"content", "citations", "provider", "model"}.
    """
    errors = []
    now = time.time()
    for name, url, token in _credentials(oidc):
        if only and name != only:
            continue
        if _skip_until.get(name, 0) > now:
            errors.append(f"{name} skipped, it refused the last request (no credits or bad key)")
            continue

        models = MODELS[name][task]
        models = models if isinstance(models, list) else [models]
        if web_search:
            models = [m for m in models if m.startswith("openai/gpt-oss")]

        res = await _try_models(name, url, token, task, models, messages, json_mode, web_search, timeout, errors)
        if res:
            return res

    if not errors:
        raise LLMError("No model provider is configured. Set GROQ_API_KEY or AI_GATEWAY_API_KEY.")
    raise LLMError(" | ".join(errors))


async def _try_models(name, url, token, task, models, messages, json_mode, web_search, timeout, errors):
    """Go through the models in order. On a rate limit move straight to the
    next one; if all are limited, wait for the shortest reset and go again."""
    deadline = time.time() + 12
    while True:
        waits = []
        for model in models:
            body = {"model": model, "messages": messages, "temperature": 0}
            if json_mode and not web_search:
                body["response_format"] = {"type": "json_object"}
            if name == "groq" and model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "medium" if task == "judge" else "low"
            if model.startswith("qwen/"):
                body["reasoning_format"] = "hidden"  # keep <think> text out of the JSON
            if web_search:
                body["tools"] = [{"type": "browser_search"}]
                body["tool_choice"] = "required"
            try:
                r = await _post(url, token, body, timeout)
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {exc}")
                continue
            if r.status_code == 200:
                return {**_unpack(r.json()), "provider": name, "model": model}
            if r.status_code in (401, 402, 403):
                # no credits or bad key, skip this provider for a while
                _skip_until[name] = time.time() + 300
                errors.append(f"{name} {r.status_code}: {r.text[:200]}")
                return None
            if r.status_code == 429:
                waits.append(_wait_hint(r))
                continue
            errors.append(f"{model} {r.status_code}: {r.text[:200]}")
        if not waits or time.time() + min(waits) > deadline:
            if waits:
                errors.append(f"{name}: rate limited")
            return None
        await asyncio.sleep(min(waits) + 0.5)


def _unpack(data: dict) -> dict:
    msg = data["choices"][0]["message"]
    return {"content": msg.get("content") or "", "citations": _collect_citations(data, msg)}


def _collect_citations(data: dict, msg: dict) -> list[dict]:
    """Search models report sources in a few different shapes. Gather them all."""
    seen, out = set(), []

    def add(url, title=""):
        if not isinstance(url, str) or not url.startswith(("https://", "http://")) or url in seen:
            return
        # search-engine result pages and redirect links are no use to a reader
        if "google.com/goto" in url or "exa.ai/search" in url:
            return
        seen.add(url)
        out.append({"url": url, "title": title or ""})

    for ann in msg.get("annotations") or []:
        c = ann.get("url_citation") or ann
        add(c.get("url"), c.get("title"))
    for item in data.get("search_results") or []:
        add(item.get("url"), item.get("title"))
    for url in data.get("citations") or []:
        add(url if isinstance(url, str) else url.get("url"))
    # Groq browser_search: pages the model opened
    for tool in msg.get("executed_tools") or []:
        if tool.get("name") == "browser.open":
            m = re.search(r"URL:\s*\nL\d+:\s*(\S+)", tool.get("output") or "")
            if m:
                add(m.group(1))
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
