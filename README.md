# Fact-Check Agent

Upload a text-based PDF to verify checkable claims with current web evidence. The report shows the original quote and page, verdict, corrected fact when available, and sources. It exports CSV or JSON and can retry failed individual requests.

**Public app** [fact-check-agent-ecru.vercel.app](https://fact-check-agent-ecru.vercel.app)

**Author** Shubham Kumar · Assessment Part 2

## Try it

1. Upload your own PDF or choose **Check sample PDF** to run the included trap document through the live pipeline.
2. Review the counts, filter the verdicts, and open the cited sources or search notes.
3. If a provider is unavailable, **View sample report** opens a saved source review for four selected claims from the sample. It uses no API credits and is labelled separately in the app and exports.

| Verdict | Evidence needed |
| --- | --- |
| Verified | A source supports the claim at the stated date, scope and units. |
| Inaccurate | A source supports a corrected figure, date or value for the same claim. |
| False | A source directly contradicts the premise or a categorical claim. |
| Unsubstantiated | Search results do not settle the claim. Review manually or narrow the query. |

Failed requests show **Not checked** and can be retried. A current source does not invalidate an explicitly dated figure for an earlier period.

## How it works

- **Extract** — FastAPI accepts multipart PDFs. pypdf supplies page-numbered text. The extraction model identifies atomic claims and neutral search queries. The server retains claims with a quote it can locate in the PDF text.
- **Verify** — The server tries Perplexity Sonar via Vercel AI Gateway, optional SerpAPI, Groq browser search, then Wikipedia as a fallback. A separate model compares evidence with the claim. A definitive verdict requires a source URL returned by the search and selected by the judge.
- **Report** — The browser checks up to three claims concurrently. The report preserves page context and distinguishes unsettled evidence from failed requests.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt uvicorn
export GROQ_API_KEY=your_key
uvicorn app:app --reload
```

Open http://localhost:8000. Set at least one model credential for live checks. The saved sample report runs without credentials.

| Configuration | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Groq extraction, judging and browser search. |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway extraction, judging and Perplexity Sonar. Vercel can supply its deployment OIDC token instead. |
| `SERPAPI_API_KEY` | Optional Google results before Groq browser search and Wikipedia. |
| `DISABLE_GATEWAY` | Set to `1` to skip Gateway. |
| `MAX_CLAIMS` | Defaults to 20 claims per PDF. |
| `EXTRACT_MODEL`, `JUDGE_MODEL`, `SEARCH_MODEL` | Override the AI Gateway models. |

## Deployment and checks

The GitHub repository is linked to the Vercel project. Vercel runs FastAPI from `app.py` using `requirements.txt`. Store provider keys as deployment environment variables. `GET /api/health` reports configured providers. It does not spend API credits.

```bash
python -m unittest discover -s tests
```

`samples/sample_trap.pdf` contains planted errors and true controls. The generator documents the expected cases. The app handles text-based PDFs up to 4 MB and reads the first 100 pages and 40,000 extracted characters. Report coverage is shown when a limit is reached. Scans need a searchable text layer. PDFs and reports are not stored by the app. Extracted text and claims are sent to external model and search providers.

## Project layout

- `app.py` — web routes and API
- `factcheck/` — PDF parsing, evidence retrieval, model calls and verdict logic
- `static/` — application interface and sample artifacts
- `samples/` — trap PDF and generator
- `tests/` — regression tests for traceability and evidence handling
