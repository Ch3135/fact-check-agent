import io

from pypdf import PdfReader

# Long PDFs get cut here. The claim extractor only needs the first chunk of
# a marketing doc, and it keeps the request small.
MAX_CHARS = 40_000


class PdfError(Exception):
    pass


def extract_text(data: bytes) -> tuple[str, int, bool]:
    """Return (text, page_count, truncated) for a PDF given as bytes."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a few different types
        raise PdfError(f"Could not read this PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise PdfError("This PDF is password protected.")
        except Exception:
            raise PdfError("This PDF is password protected.")

    pages = []
    for number, page in enumerate(reader.pages[:100], start=1):
        try:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Page {number}]\n{text}")
        except Exception:
            continue

    text = "\n\n".join(p.strip() for p in pages).strip()
    if not text:
        raise PdfError(
            "No text found. The PDF is probably a scan or images only, "
            "which this tool does not OCR."
        )
    return text[:MAX_CHARS], len(reader.pages), len(text) > MAX_CHARS or len(reader.pages) > 100
