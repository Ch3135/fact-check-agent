import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from factcheck.checker import MAX_CLAIMS, extract_claims, verify_claim
from factcheck.llm import LLMError, available
from factcheck.pdf_text import PdfError, extract_text

MAX_UPLOAD = 4 * 1024 * 1024  # Vercel caps request bodies at about 4.5 MB
STATIC = Path(__file__).parent / "static"
SAMPLE_PDF = STATIC / "sample_trap.pdf"
SAMPLE_BYTES = SAMPLE_PDF.read_bytes()
SAMPLE_CLAIMS = json.loads((STATIC / "sample_claims.json").read_text(encoding="utf-8"))

app = FastAPI(title="Fact-Check Agent")


def oidc_token(request: Request) -> str | None:
    # Vercel passes the deployment's OIDC token on every function request.
    return request.headers.get("x-vercel-oidc-token")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sample_trap.pdf")
def sample_pdf():
    return FileResponse(SAMPLE_PDF, media_type="application/pdf", filename="sample_trap.pdf")


@app.get("/sample_report.json")
def sample_report():
    return FileResponse(STATIC / "sample_report.json", media_type="application/json")


@app.get("/api/health")
def health(request: Request):
    return {"ok": True, "providers": available(oidc_token(request)), "max_claims": MAX_CLAIMS}


@app.post("/api/extract")
async def extract(request: Request, file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is over 4 MB. Try a smaller PDF.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That does not look like a PDF file.")

    try:
        text, pages, truncated = await run_in_threadpool(extract_text, data)
    except PdfError as exc:
        raise HTTPException(422, str(exc)) from exc

    is_sample = data == SAMPLE_BYTES
    if is_sample:
        claims = SAMPLE_CLAIMS
    else:
        try:
            claims = await extract_claims(text, oidc_token(request))
        except (LLMError, ValueError) as exc:
            raise HTTPException(502, "Claim extraction is temporarily unavailable. Try again in a moment.") from exc

    return {"filename": file.filename, "pages": pages, "truncated": truncated,
            "extraction": "preset" if is_sample else "live",
            "claim_limit_reached": len(claims) > MAX_CLAIMS, "claims": claims[:MAX_CLAIMS]}


class VerifyIn(BaseModel):
    claim: str = Field(min_length=3, max_length=1000)
    search_query: str | None = Field(default=None, max_length=300)


@app.post("/api/verify")
async def verify(body: VerifyIn, request: Request):
    # The browser calls this once per claim, so each request stays well under
    # the function time limit even for a long document.
    try:
        return await verify_claim(body.claim, body.search_query, oidc_token(request))
    except (LLMError, ValueError) as exc:
        raise HTTPException(502, "This claim could not be checked. Try again in a moment.") from exc
