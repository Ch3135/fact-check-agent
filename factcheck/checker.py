"""Claim extraction and verification.

Flow for one document:
  1. extract_claims()  one model call pulls the checkable facts out as JSON
  2. verify_claim()    per claim:
       a. research()   collect evidence: Perplexity Sonar if the gateway is
                       available, else Google (SerpAPI) if a key is set,
                       else Wikipedia
       b. judge()      a model compares the claim to the evidence and returns
                       Verified / Inaccurate / False with the real figure
       c. if the judge says the evidence is thin and Groq is set up, run a
          live web search (Groq browser_search) and judge again

Search and judging are split on purpose. Search models are good at finding
pages but go easy on a claim when asked for a verdict; a separate judge with
strict rules is better at calling out outdated numbers.
"""
import os
from datetime import date

from .llm import LLMError, available, chat, parse_json
from .search import serpapi_search, wikipedia_search

MAX_CLAIMS = int(os.getenv("MAX_CLAIMS", "20"))
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
"search_query": "a neutral web search query that would find the true figure. Leave out the document's own number, e.g. 'Tesla vehicle deliveries 2023', not 'Tesla 5 million deliveries'"}}]}}"""


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

Research notes from live sources:
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
  "source_urls": ["most relevant links from the list above, best first, max 3"],
  "enough_evidence": true if the notes clearly settle the claim and contain the real figure, otherwise false}}"""


WEB_PROMPT = """Today is {today}. Use web search to check this claim:
"{claim}"

Find the latest reliable figure or fact (official sources, company reports,
statistics bodies, major news). Report the real figure, the date it refers
to, and the source names with URLs. Be brief."""


async def extract_claims(text: str, oidc: str | None = None) -> list[dict]:
    messages = [
        {"role": "system", "content": EXTRACT_PROMPT.format(today=today(), max_claims=MAX_CLAIMS)},
        {"role": "user", "content": text},
    ]
    res = await chat("extract", messages, oidc=oidc, json_mode=True)
    try:
        data = parse_json(res["content"])
    except (LLMError, ValueError):
        res = await chat("extract", messages, oidc=oidc, json_mode=True)
        data = parse_json(res["content"])

    claims = []
    for c in data.get("claims", [])[:MAX_CLAIMS]:
        if not isinstance(c, dict) or not c.get("claim"):
            continue
        claims.append(
            {
                "id": len(claims) + 1,
                "claim": c["claim"].strip(),
                "quote": (c.get("quote") or "").strip(),
                "category": c.get("category") or "other",
                "search_query": c.get("search_query") or c["claim"],
            }
        )
    return claims


async def research(claim: str, query: str, oidc: str | None = None) -> dict:
    if "gateway" in available(oidc):
        try:
            prompt = RESEARCH_PROMPT.format(today=today(), claim=claim, query=query)
            res = await chat("search", [{"role": "user", "content": prompt}], oidc=oidc, only="gateway", timeout=60)
            return {**res, "engine": "perplexity-sonar"}
        except LLMError:
            pass
    if os.getenv("SERPAPI_API_KEY"):
        try:
            return {**await serpapi_search(query), "engine": "google"}
        except Exception:
            pass
    return {**await wikipedia_search(claim, query), "engine": "wikipedia"}


async def web_search(claim: str, oidc: str | None = None) -> dict:
    prompt = WEB_PROMPT.format(today=today(), claim=claim)
    res = await chat("search", [{"role": "user", "content": prompt}], oidc=oidc, only="groq", web_search=True)
    return {**res, "engine": "web search"}


async def judge(claim: str, evidence: str, sources: list[dict], oidc: str | None = None) -> dict:
    links = ", ".join(s["url"] for s in sources[:10]) or "none"
    messages = [{"role": "user", "content": JUDGE_PROMPT.format(today=today(), claim=claim, evidence=evidence, sources=links)}]
    try:
        res = await chat("judge", messages, oidc=oidc, json_mode=True)
        return parse_json(res["content"])
    except (LLMError, ValueError):
        res = await chat("judge", messages, oidc=oidc, json_mode=True)  # bad JSON, try once more
        return parse_json(res["content"])


def _plain(text) -> str:
    # model output sometimes has long dashes and odd hyphens; keep it plain
    return str(text or "").replace("\u2014", "-").replace("\u2013", "-").replace("\u2011", "-").replace("\u202f", " ")


def _confidence(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 50


async def verify_claim(claim: str, query: str | None = None, oidc: str | None = None) -> dict:
    query = query or claim
    found = await research(claim, query, oidc)
    evidence = found["content"].strip() or "No results."
    sources = found["citations"]
    engines = [found["engine"]]

    result = await judge(claim, evidence, sources, oidc)

    thin = not result.get("enough_evidence", True) or _confidence(result.get("confidence")) < 70
    if thin and found["engine"] != "perplexity-sonar" and "groq" in available(oidc):
        try:
            extra = await web_search(claim, oidc)
            evidence = f"{extra['content'].strip()}\n\n{evidence}"
            sources = extra["citations"] + [s for s in sources if s not in extra["citations"]]
            engines.insert(0, extra["engine"])
            result = await judge(claim, evidence, sources, oidc)
        except LLMError:
            pass  # keep the first verdict

    verdict = str(result.get("verdict", "")).strip().capitalize()
    if verdict not in VERDICTS:
        verdict = "False"

    # only keep links that actually came back from the search, models
    # sometimes invent plausible looking URLs
    known = {s["url"]: s for s in sources}
    picked = [u for u in result.get("source_urls") or [] if u in known] or [s["url"] for s in sources[:3]]

    return {
        "verdict": verdict,
        "correct_fact": _plain(result.get("correct_fact")),
        "explanation": _plain(result.get("explanation")),
        "confidence": _confidence(result.get("confidence")),
        "sources": [known[u] for u in picked[:3] if u in known],
        "evidence": evidence,
        "search_engine": " + ".join(engines),
    }
