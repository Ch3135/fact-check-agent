from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from factcheck.checker import JUDGE_MODEL, MAX_CLAIMS, SEARCH_MODEL, extract_claims, verify_claim
from factcheck.llm import LLMError
from factcheck.pdf_text import PdfError, extract_text

MAX_UPLOAD = 4 * 1024 * 1024  # Vercel caps request bodies at about 4.5 MB
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Fact-Check Agent")


def oidc_token(request: Request) -> str | None:
    # Vercel passes the deployment's OIDC token on every function request.
    return request.headers.get("x-vercel-oidc-token")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sample_trap.pdf")
def sample_pdf():
    return FileResponse(STATIC / "sample_trap.pdf", media_type="application/pdf", filename="sample_trap.pdf")


@app.get("/api/health")
def health():
    return {"ok": True, "search_model": SEARCH_MODEL, "judge_model": JUDGE_MODEL, "max_claims": MAX_CLAIMS}


@app.post("/api/extract")
async def extract(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is over 4 MB. Try a smaller PDF.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That does not look like a PDF file.")

    try:
        text, pages = extract_text(data)
    except PdfError as exc:
        raise HTTPException(422, str(exc))

    try:
        claims = await extract_claims(text, oidc_token(request))
    except (LLMError, ValueError) as exc:
        raise HTTPException(502, f"Claim extraction failed: {exc}")

    return {"filename": file.filename, "pages": pages, "chars": len(text), "claims": claims}


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
        raise HTTPException(502, f"Check failed: {exc}")
