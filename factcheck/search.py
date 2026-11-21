"""Backup search sources for when Perplexity Sonar is not available.

Sonar (through the AI Gateway) does its own web search and is used when the
gateway is available. Otherwise:
  - SerpAPI (Google results), when SERPAPI_API_KEY is set
  - Wikipedia, which needs no key
"""
import asyncio
import os
import re

import httpx


async def serpapi_search(query: str, num: int = 8) -> dict:
    key = os.getenv("SERPAPI_API_KEY")
    if not key:
        raise RuntimeError("SERPAPI_API_KEY is not set")

    params = {"engine": "google", "q": query, "num": num, "api_key": key, "hl": "en"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get("https://serpapi.com/search.json", params=params)
    r.raise_for_status()
    data = r.json()

    lines, sources = [], []
    box = data.get("answer_box") or {}
    if box:
        ans = box.get("answer") or box.get("snippet") or box.get("result")
        if ans:
            lines.append(f"Google answer box: {ans} ({box.get('link', '')})")
            if box.get("link"):
                sources.append({"url": box["link"], "title": box.get("title", "")})
    kg = data.get("knowledge_graph") or {}
    if kg.get("description"):
        lines.append(f"Knowledge panel ({kg.get('title', '')}): {kg['description']}")

    for item in data.get("organic_results", [])[:num]:
        date = f" [{item['date']}]" if item.get("date") else ""
        lines.append(f"- {item.get('title', '')}{date}: {item.get('snippet', '')} ({item.get('link', '')})")
        if item.get("link") and "google.com/goto" not in item["link"]:
            sources.append({"url": item["link"], "title": item.get("title", "")})

    return {"content": "\n".join(lines) or "No results.", "citations": sources}


# Wikipedia needs no key, so it is the last backup. It is good for founders,
# dates, populations and company figures, weaker for very recent numbers.
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "fact-check-agent/1.0 (https://github.com/Ch3135/fact-check-agent)"}
STOP = set("the a an of in on and or to is was were are has have had with for from by at as that this its it be been".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.,]*", text.lower()) if w not in STOP and len(w) > 2}


async def _wiki_extract(client: httpx.AsyncClient, title: str) -> str:
    # full-text extracts only come back one page per request
    r = await client.get(WIKI_API, params={
        "action": "query", "prop": "extracts", "explaintext": 1,
        "titles": title, "format": "json", "redirects": 1,
    })
    r.raise_for_status()
    pages = r.json()["query"]["pages"]
    return next(iter(pages.values())).get("extract", "")


def _best_sentences(text: str, keys: set[str], n: int) -> list[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 25 < len(s.strip()) < 600]

    def score(s: str) -> float:
        hits = len(keys & _words(s))
        has_number = bool(re.search(r"\d", s))
        recent = len(re.findall(r"\b20(2[3-9])\b", s))
        return hits * 2 + has_number + recent * 0.5

    ranked = sorted(sentences, key=score, reverse=True)
    return [s for s in ranked[:n] if score(s) >= 3]


async def wikipedia_search(claim: str, query: str, pages: int = 3) -> dict:
    async with httpx.AsyncClient(timeout=20, headers=WIKI_HEADERS) as client:
        r = await client.get(WIKI_API, params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": pages, "format": "json",
        })
        r.raise_for_status()
        titles = [h["title"] for h in r.json()["query"]["search"]]
        if not titles:
            return {"content": "No results.", "citations": []}
        texts = await asyncio.gather(*[_wiki_extract(client, t) for t in titles], return_exceptions=True)

    keys = _words(query) | _words(claim)
    lines, sources = [], []
    for title, text in zip(titles, texts):
        if isinstance(text, Exception) or not text:
            continue
        intro = " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[:2])
        best = _best_sentences(text, keys, 8)
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        lines.append(f"Wikipedia, {title} ({url}):\n{intro}\n" + "\n".join(f"- {s}" for s in best))
        sources.append({"url": url, "title": title})
    return {"content": "\n\n".join(lines) or "No results.", "citations": sources}
