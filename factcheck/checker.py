"""Claim extraction and verification.

Flow for one document:
  1. extract_claims()  - one LLM call pulls out checkable facts as JSON
  2. verify_claim()    - per claim:
       a. research()   - Perplexity Sonar searches the live web and writes up
                         what current sources say, with links
       b. judge()      - a second model compares the claim to that write-up
                         and returns Verified / Inaccurate / False

Search and judging are split on purpose. Sonar is good at finding pages but
tends to be generous when asked for a verdict; a separate judge with strict
rules is better at calling out outdated numbers.
"""
import asyncio
import os
from datetime import date

from .llm import LLMError, chat, parse_json
from .search import serpapi_search, wikipedia_search

EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "openai/gpt-4.1-mini")
SEARCH_MODEL = os.getenv("SEARCH_MODEL", "perplexity/sonar")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "openai/gpt-4.1")
MAX_CLAIMS = int(os.getenv("MAX_CLAIMS", "20"))
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "sonar")  # or "serpapi"

VERDICTS = ("Verified", "Inaccurate", "False")


def today() -> str:
    return date.today().strftime("%d %B %Y")


EXTRACT_PROMPT = """You pull checkable factual claims out of documents.

Today is {today}.

Read the document and list every specific claim that can be checked against
public sources: statistics, percentages, user counts, market sizes, revenue
and other money figures, dates and years, rankings, names of people in roles
(CEO, founder), technical specs and record figures.

Rules:
- One fact per claim. Split sentences that contain two numbers.
- Rewrite each claim so it makes sense on its own (resolve "it", "the
  company", "last year" using the document).
- Keep the exact number or date the document uses. Do not correct anything.
- When a sentence names a person in a role ("under CEO X"), add a separate
  claim such as "X is the CEO of Y".
- Skip opinions, slogans, predictions and vague statements with no figure
  or named fact.
- At most {max_claims} claims. If there are more, keep the most specific and
  the most important ones.

Return JSON only:
{{"claims": [{{"claim": "...", "quote": "short exact quote from the text",
"category": "statistic|date|financial|technical|person|other",
"search_query": "a good web search query to check this"}}]}}"""


RESEARCH_PROMPT = """Today is {today}. Find the most recent, reliable facts that
confirm or contradict this claim. Prefer official sources, company filings,
government or international statistics bodies, and major news outlets.

Claim: "{claim}"
Suggested search: {query}

Report what the sources say: the actual current figure or fact, the date it
refers to, and whether the claim's figure was ever true (for example an older
year's number). Quote numbers exactly and name each source. Be brief."""


JUDGE_PROMPT = """You are the final checker in a fact-checking tool. Today is {today}.

Claim from the uploaded document:
"{claim}"

Research notes from a live web search:
---
{evidence}
---
Source links found: {sources}

Decide one verdict:
- "Verified": the evidence supports the claim as stated and it is current.
  Small rounding is fine (e.g. 8.1 billion vs 8.12 billion).
- "Inaccurate": the claim is wrong in a checkable way. This includes outdated
  figures (a number that was true in an earlier year but has since changed),
  wrong years, wrong amounts, wrong names, or a real figure exaggerated or
  understated.
- "False": the evidence contradicts the claim outright or there is no
  evidence at all that it was ever true (made-up stats, events that did not
  happen).

When sources disagree, trust official and company-reported figures over blogs,
and full-year totals over quarterly ones when the claim is about a year.

Only use figures that appear in the research notes. Never fill in a number
from memory. If the notes do not contain the real figure, say in
correct_fact that the actual figure could not be confirmed from the sources.

Be strict. If the claim uses an old number while a newer one exists, it is
"Inaccurate" even if the old number was once right. Do not guess beyond the
evidence. If the evidence is thin, say so and lower the confidence.

Return JSON only:
{{"verdict": "Verified|Inaccurate|False",
  "correct_fact": "the accurate, current fact in one sentence with the real figure and its date. For Verified, restate the confirmed fact.",
  "explanation": "one or two plain sentences on why",
  "confidence": 0-100,
  "source_urls": ["most relevant links from the list above, best first, max 3"]}}"""


async def extract_claims(text: str, oidc: str | None = None) -> list[dict]:
    res = await chat(
        EXTRACT_MODEL,
        [
            {"role": "system", "content": EXTRACT_PROMPT.format(today=today(), max_claims=MAX_CLAIMS)},
            {"role": "user", "content": text},
        ],
        oidc=oidc,
        json_mode=True,
    )
    data = parse_json(res["content"])
    claims = []
    for i, c in enumerate(data.get("claims", [])[:MAX_CLAIMS]):
        if not isinstance(c, dict) or not c.get("claim"):
            continue
        claims.append(
            {
                "id": i + 1,
                "claim": c["claim"].strip(),
                "quote": (c.get("quote") or "").strip(),
                "category": c.get("category") or "other",
                "search_query": c.get("search_query") or c["claim"],
            }
        )
    return claims


async def research(claim: str, query: str, oidc: str | None = None) -> dict:
    if SEARCH_BACKEND == "serpapi":
        return {**await serpapi_search(query), "engine": "google-serpapi"}
    try:
        res = await chat(
            SEARCH_MODEL,
            [{"role": "user", "content": RESEARCH_PROMPT.format(today=today(), claim=claim, query=query)}],
            oidc=oidc,
            timeout=60,
            allow_fallback=False,  # a fallback model without search would just guess
        )
        return {**res, "engine": SEARCH_MODEL}
    except LLMError:
        pass
    # Sonar is unavailable (no credits, outage). Use Google via SerpAPI if a
    # key is set, otherwise Wikipedia, which needs no key.
    if os.getenv("SERPAPI_API_KEY"):
        try:
            return {**await serpapi_search(query), "engine": "google-serpapi"}
        except Exception:
            pass
    return {**await wikipedia_search(claim, query), "engine": "wikipedia"}


async def judge(claim: str, evidence: str, sources: list[dict], oidc: str | None = None) -> dict:
    links = ", ".join(s["url"] for s in sources[:8]) or "none"
    res = await chat(
        JUDGE_MODEL,
        [{"role": "user", "content": JUDGE_PROMPT.format(today=today(), claim=claim, evidence=evidence, sources=links)}],
        oidc=oidc,
        json_mode=True,
    )
    return parse_json(res["content"])


async def verify_claim(claim: str, query: str | None = None, oidc: str | None = None) -> dict:
    query = query or claim
    try:
        found = await research(claim, query, oidc)
    except Exception:
        # one retry, search providers time out now and then
        await asyncio.sleep(1)
        found = await research(claim, query, oidc)

    evidence = found["content"].strip() or "No results."
    sources = found["citations"]

    result = await judge(claim, evidence, sources, oidc)

    verdict = str(result.get("verdict", "")).strip().capitalize()
    if verdict not in VERDICTS:
        verdict = "False"

    known = {s["url"]: s for s in sources}
    # only keep links that actually came back from the search, models
    # sometimes invent plausible looking URLs
    picked = [u for u in result.get("source_urls") or [] if u in known]
    if not picked:
        picked = [s["url"] for s in sources[:3]]

    try:
        confidence = max(0, min(100, int(result.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50

    return {
        "verdict": verdict,
        "correct_fact": result.get("correct_fact", ""),
        "explanation": result.get("explanation", ""),
        "confidence": confidence,
        "sources": [known.get(u, {"url": u, "title": ""}) for u in picked[:3]],
        "evidence": evidence,
        "search_engine": found.get("engine", ""),
    }
