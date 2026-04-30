"""Multimodal chain that extracts booth-level metadata from page 1 of an ECI roll."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda

from ..llm.provider_factory import get_vision_llm
from ..schemas.voter import BoothMetadata
from .voter_extraction_chain import encode_image_to_base64


METADATA_PROMPT = """This is page 1 of an Indian Election Commission voter list PDF.

Extract the booth-level metadata from the page. The page shows fields like:
- विधान सभा निर्वाचन क्षेत्र की संख्या व नाम (constituency number + name)
- भाग संख्या (booth number)
- मतदान स्थल की संख्या और नाम (polling station name)
- अनुभाग नाम (section names — list them all)
- पिन कोड
- A summary table showing: पुरुष (male), महिला (female), तृतीय लिंग (third gender), कुल मतदाता (total voters)

The voter totals row may be preceded by columns like प्रारम्भिक क्रम / अंतिम क्रम — those are NOT the totals. Use only the four numbers under पुरुष / महिला / तृतीय लिंग / कुल.

The total voter count is the ground truth used to validate downstream extraction. Do not estimate it — read it directly from the page.
"""


def _build_messages(inputs: dict):
    image_b64 = encode_image_to_base64(inputs["image"])
    return [
        HumanMessage(
            content=[
                {"type": "text", "text": METADATA_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ]
        )
    ]


def build_metadata_extraction_chain():
    """LangChain runnable: {"image": PIL.Image} -> BoothMetadata."""
    llm = get_vision_llm()
    structured = llm.with_structured_output(BoothMetadata)
    return RunnableLambda(_build_messages) | structured
