import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from factcheck.checker import extract_claims, verify_claim
from factcheck.llm import _collect_citations
from factcheck.pdf_text import extract_text


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_evidence_never_becomes_false(self):
        evidence = {"content": "", "citations": [], "engine": "test"}
        with patch("factcheck.checker.research", AsyncMock(return_value=evidence)):
            result = await verify_claim("An unsupported claim")
        self.assertEqual(result["verdict"], "Unsubstantiated")
        self.assertEqual(result["correct_fact"], "")

    async def test_definitive_verdict_requires_a_retrieved_source(self):
        url = "https://example.org/report"
        evidence = {
            "content": "The report records 10 units for 2024.",
            "citations": [{"url": url}],
            "engine": "test",
        }
        finding = {
            "verdict": "Inaccurate", "correct_fact": "The report records 10 units.",
            "enough_evidence": True, "source_urls": ["https://example.org/invented"],
        }
        with patch("factcheck.checker.research", AsyncMock(return_value=evidence)):
            with patch("factcheck.checker.judge", AsyncMock(return_value=finding)):
                self.assertEqual((await verify_claim("There were 20 units in 2024"))["verdict"], "Unsubstantiated")
                finding["source_urls"] = [url]
                self.assertEqual((await verify_claim("There were 20 units in 2024"))["verdict"], "Inaccurate")

    async def test_extract_requires_a_quote_on_an_actual_page(self):
        sample = Path("static/sample_trap.pdf").read_bytes()
        text, pages, truncated = extract_text(sample)
        self.assertEqual(pages, 1)
        self.assertFalse(truncated)
        response = {"content": json.dumps({"claims": [
            {"claim": "Tesla delivered 5 million vehicles in 2023", "quote": "Tesla delivered 5 million vehicles in\n2023 alone"},
            {"claim": "A made-up claim", "quote": "This quote is absent"},
        ]})}
        with patch("factcheck.checker.chat", AsyncMock(return_value=response)):
            claims = await extract_claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["page"], 1)

    def test_model_generated_urls_are_not_search_citations(self):
        self.assertEqual(_collect_citations({}, {"content": "See https://example.org/report"}), [])
