"""Multimodal voter extraction chain.

Two builders:
  - `build_primary_voter_chain()`  → Flash (or whatever PRIMARY_VISION_MODEL is)
  - `build_fallback_voter_chain()` → Pro (or FALLBACK_VISION_MODEL)

The legacy `build_voter_extraction_chain()` is kept as an alias for the
primary chain so existing call sites keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda

from ..llm.provider_factory import get_fallback_vision_llm, get_primary_vision_llm
from ..schemas.voter import RawVotersOnPage
from ..utils.image_utils import encode_image_optimized

if TYPE_CHECKING:
    from PIL.Image import Image


VOTER_EXTRACTION_PROMPT = """You extract structured data from Indian Election Commission voter list PDF pages.

This image shows ONE page of an official voter list. Voter cards are arranged in a grid (typically 3 columns x 10 rows = ~30 cards per page).

Each visible card has these fields:
- Serial number — integer at the top-LEFT of the card (e.g. 1, 2, 3, ...)
- EPIC voter ID — alphanumeric at the top-RIGHT (formats: XFF1234567 / FPP1234567 / UP/20/100/0001234)
- नाम (Name) — the voter's OWN name
- पिता / पति / माता का नाम — the voter's relative
- मकान संख्या (House number)
- आयु (Age) — integer
- लिंग (Gender) — पुरुष or महिला

EXTRACTION RULES (read carefully):
1. Each visible card = exactly ONE voter record. Do NOT create a separate row for the relative.
2. `relation_type` must be ONE word from: पिता, पति, माता, पुत्र, पुत्री, अन्य. Never include "का".
3. `gender` must be in English: "Male" / "Female" / "Other". पुरुष → Male, महिला → Female.
4. Capture every visible serial_no and voter_id exactly as printed.
5. Skip cards that are partially cut off or unreadable. Do NOT guess.
6. Preserve all Devanagari characters exactly as shown. Do not transliterate.
7. Voter IDs follow strict patterns — extract only valid IDs (XFF + 7 digits / FPP + 7 digits / UP/dd/ddd/ddddddd).
8. If a header / footer / metadata box appears (e.g. भाग संख्या, मतदान स्थल), ignore it — those are NOT voter cards.
"""


def encode_image_to_base64(image: "Image") -> str:
    """Backwards-compatible PNG encoder (kept for any external callers)."""
    b64, _ = encode_image_optimized(image)
    return b64


def _build_messages(inputs: dict):
    image_b64, media_type = encode_image_optimized(inputs["image"])
    return [
        HumanMessage(
            content=[
                {"type": "text", "text": VOTER_EXTRACTION_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                },
            ]
        )
    ]


def build_primary_voter_chain():
    llm = get_primary_vision_llm()
    structured = llm.with_structured_output(RawVotersOnPage)
    return RunnableLambda(_build_messages) | structured


def build_fallback_voter_chain():
    llm = get_fallback_vision_llm()
    structured = llm.with_structured_output(RawVotersOnPage)
    return RunnableLambda(_build_messages) | structured


# Legacy single-chain alias — points at the primary (Flash) by default
def build_voter_extraction_chain():
    return build_primary_voter_chain()
