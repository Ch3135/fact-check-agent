"""Builds samples/sample_trap.pdf, a fake marketing brief with planted errors.

Run: python samples/make_trap_pdf.py

Planted errors (what the app should flag):
  - ChatGPT launch year (said 2020, real: Nov 2022)
  - ChatGPT weekly users (said 100M, outdated; OpenAI reported 800M in Oct 2025)
  - India population (said 1.21B, that is the 2011 census)
  - US GDP (said $21T, that is 2019; it passed $29T in 2024)
  - Amazon founder (said Bill Gates)
  - Tesla 2023 deliveries (said 5M, real about 1.81M)
  - Google CEO (said Larry Page, real Sundar Pichai since 2015)
  - Great Wall visible from the Moon (myth)
  - Netflix 500M paid subscribers (real about 302M at end of 2024)
True on purpose: Eiffel Tower 1889, Everest 8,849 m, Apple founded 1976,
first iPhone 2007.
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).parent / "sample_trap.pdf"

styles = getSampleStyleSheet()
body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=11, leading=16, spaceAfter=8)
h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=22, spaceAfter=4)
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=14, spaceBefore=10)
small = ParagraphStyle("s", parent=body, fontSize=9, textColor="#666666")

story = [
    Paragraph("The Digital Growth Brief 2026", h1),
    Paragraph("Prepared for brand and marketing teams. Internal draft.", small),
    Spacer(1, 10),
    Paragraph("1. AI is changing how people search", h2),
    Paragraph(
        "OpenAI launched ChatGPT in November 2020 and it quickly became the fastest growing "
        "consumer app in history. Today ChatGPT has around 100 million weekly active users, "
        "which is why every brand needs a plan for AI answers, not just Google rankings.",
        body,
    ),
    Paragraph(
        "Google is still the biggest gateway to the web. Under CEO Larry Page, the company keeps "
        "adding AI Overviews to more search results every quarter.",
        body,
    ),
    Paragraph("2. Market size and the big players", h2),
    Paragraph(
        "India is now the world's most populous country with a population of 1.21 billion people, "
        "and its online audience is growing faster than any other market. The United States "
        "remains the largest economy, with a GDP of about 21 trillion US dollars.",
        body,
    ),
    Paragraph(
        "Ecommerce keeps setting the pace. Amazon, founded by Bill Gates in 1994, still sets "
        "customer expectations for delivery speed. Apple, founded in 1976, showed the power of "
        "product launches when the first iPhone came out in 2007.",
        body,
    ),
    Paragraph(
        "Electric vehicles are a good example of brand-led growth: Tesla delivered 5 million "
        "vehicles in 2023 alone. In streaming, Netflix now has more than 500 million paid "
        "subscribers worldwide.",
        body,
    ),
    Paragraph("3. Why landmark facts work in content", h2),
    Paragraph(
        "Travel brands do well with simple, memorable facts. The Eiffel Tower was completed in "
        "1889, Mount Everest stands 8,849 metres tall, and the Great Wall of China is the only "
        "man-made structure visible from the Moon with the naked eye.",
        body,
    ),
    Spacer(1, 12),
    Paragraph("Figures compiled by the content team. Please verify before publishing.", small),
]

SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
                  topMargin=2 * cm, bottomMargin=2 * cm, title="The Digital Growth Brief 2026").build(story)
print("wrote", OUT)
