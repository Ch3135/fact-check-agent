import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import SAMPLE_BYTES, app
from factcheck.llm import LLMError
from factcheck.pdf_text import extract_text


class ExtractionApiTests(unittest.TestCase):
    def test_sample_extracts_without_model_and_quotes_match_pdf(self):
        with patch("app.extract_claims", AsyncMock(side_effect=LLMError("unavailable"))) as extract:
            response = TestClient(app).post(
                "/api/extract", files={"file": ("renamed.pdf", SAMPLE_BYTES, "application/pdf")}
            )
        self.assertEqual(response.status_code, 200)
        extract.assert_not_awaited()
        report = response.json()
        self.assertEqual(report["extraction"], "preset")
        self.assertTrue(report["claims"])
        text = " ".join(extract_text(SAMPLE_BYTES)[0].split())
        for claim in report["claims"]:
            self.assertEqual(claim["page"], 1)
            self.assertIn(claim["quote"], text)

    def test_other_pdf_with_sample_name_uses_live_extraction(self):
        with patch("app.extract_claims", AsyncMock(return_value=[])) as extract:
            response = TestClient(app).post(
                "/api/extract",
                files={"file": ("sample_trap.pdf", SAMPLE_BYTES + b"\n% different PDF", "application/pdf")},
            )
        self.assertEqual(response.status_code, 200)
        extract.assert_awaited_once()
        self.assertEqual(response.json()["extraction"], "live")
