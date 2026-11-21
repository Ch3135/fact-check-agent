import io

from pypdf import PdfReader

# Long PDFs get cut here. The claim extractor only needs the first chunk of
# a marketing doc, and it keeps the request small.
MAX_CHARS = 40_000


class PdfError(Exception):
    pass


def extract_text(data: bytes) -> tuple[str, int]:
    """Return (text, page_count) for a PDF given as bytes."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a few different types
        raise PdfError(f"Could not read this PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise PdfError("This PDF is password protected.")

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")

    text = "\n\n".join(p.strip() for p in pages).strip()
    if not text:
        raise PdfError(
            "No text found. The PDF is probably a scan or images only, "
            "which this tool does not OCR."
        )
    return text[:MAX_CHARS], len(reader.pages)
