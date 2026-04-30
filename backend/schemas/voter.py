"""Pydantic schemas for the LangChain extraction + downstream pipeline.

Two-tier design:
  - `RawVoter` / `RawVotersOnPage` → fields extracted by the vision LLM only.
    Religion/caste are intentionally NOT in this schema so the LLM doesn't
    try to populate them.
  - `Voter` / `BoothOutput` → the full enriched record after rule-based
    classification. Religion/caste/booth fields are filled by classifiers
    in `backend/classifiers/`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LLM extraction layer
# ---------------------------------------------------------------------------

class RawVoter(BaseModel):
    """Voter card fields the vision LLM is asked to extract."""

    serial_no: int = Field(
        description="Serial number printed at the top-left of the card (1, 2, 3, ...)"
    )
    voter_id: str = Field(
        description="EPIC voter ID, e.g. XFF1234567 / FPP1234567 / UP/20/100/0001234"
    )
    name: str = Field(description="Voter's own name in Devanagari")
    relation_type: Literal["पिता", "पति", "माता", "पुत्र", "पुत्री", "अन्य"] = Field(
        description="Relation word only — no 'का'"
    )
    relative_name: str = Field(description="Name of father / husband / mother in Devanagari")
    house_no: Optional[str] = Field(default=None, description="House number / मकान संख्या")
    age: int = Field(ge=18, le=120, description="Age in years (integer)")
    gender: Literal["Male", "Female", "Other"] = Field(
        description="Voter gender. पुरुष → Male, महिला → Female"
    )


class RawVotersOnPage(BaseModel):
    """Output of the vision LLM for a single PDF page."""

    voters: list[RawVoter] = Field(
        default_factory=list,
        description="Every voter card visible on the page. Skip partial / unreadable cards.",
    )


# ---------------------------------------------------------------------------
# Enriched (post-classification) voter
# ---------------------------------------------------------------------------

class Voter(BaseModel):
    """Voter record enriched with booth context + rule-based religion/caste."""

    # Extracted by the LLM
    serial_no: int
    voter_id: str
    name: str
    relation_type: str
    relative_name: str
    house_no: Optional[str] = None
    age: int
    gender: str

    # Pipeline-attached context
    page_no: Optional[int] = None
    booth_number: Optional[str] = None
    booth_name: Optional[str] = None

    # Filled by rule-based classifiers (no LLM cost)
    religion: Optional[Literal["Hindu", "Muslim", "Sikh", "Other", "Unknown"]] = None
    religion_confidence: Optional[Literal["High", "Medium", "Low"]] = None
    caste: Optional[str] = None
    caste_category: Optional[Literal["UC", "OBC", "SC", "ST", "Unknown"]] = None
    caste_confidence: Optional[Literal["High", "Medium", "Low"]] = None
    surname_matched: Optional[str] = None

    # Routing provenance — which model produced this record
    extraction_model: Optional[str] = None


# Backwards-compat alias — older callers imported `VotersOnPage` for the LLM
# output. Anything constructed from the LLM should now use RawVotersOnPage.
VotersOnPage = RawVotersOnPage


# ---------------------------------------------------------------------------
# Booth-level metadata + validation
# ---------------------------------------------------------------------------

class BoothMetadata(BaseModel):
    constituency: str = Field(default="")
    booth_number: str = Field(default="")
    booth_name: str = Field(default="")
    section_names: list[str] = Field(default_factory=list)
    pin_code: Optional[str] = None
    expected_voter_count: int = 0
    expected_male: int = 0
    expected_female: int = 0
    expected_third: int = 0


class ValidationReport(BaseModel):
    expected_total: int = 0
    extracted_total: int = 0
    difference: int = 0
    match: bool = False
    expected_male: int = 0
    extracted_male: int = 0
    expected_female: int = 0
    extracted_female: int = 0
    missing_serial_numbers: list[int] = Field(default_factory=list)
    missing_serial_count: int = 0
    duplicate_serials: list[int] = Field(default_factory=list)
    duplicate_serial_count: int = 0
    duplicate_voter_ids: list[str] = Field(default_factory=list)
    duplicate_voter_id_count: int = 0
    duplicates_removed: int = 0
    status: Literal["OK", "MINOR_MISMATCH", "MAJOR_MISMATCH", "FAILED", "UNKNOWN"] = "UNKNOWN"


class RoutingStats(BaseModel):
    flash_pages: int = 0
    pro_fallback_pages: int = 0
    failed_pages: int = 0
    fallback_rate_pct: float = 0.0
    # Cost from ACTUAL Gemini token counts (not estimates).
    cost_inr: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    # Legacy alias kept for backwards compat with prior outputs.
    estimated_cost_inr: float = 0.0
    page_metadata: list[dict] = Field(default_factory=list)


class BoothOutput(BaseModel):
    metadata: BoothMetadata
    validation: ValidationReport
    voters: list[Voter] = Field(default_factory=list)
    processed_at: datetime
    pdf_source: str
    total_pages: int = 0
    llm_provider: str = ""
    llm_model: str = ""
    routing_stats: Optional[RoutingStats] = None
