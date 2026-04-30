"""End-to-end PDF processing using LangChain + hybrid Flash/Pro routing.

Per voter page:
  1. Render PDF page → JPEG-optimized PIL image (PyMuPDF, no poppler).
  2. Try the PRIMARY model (Flash). Validate the result.
  3. If validation fails, retry with the FALLBACK model (Pro).
  4. Both attempts are logged for cost + quality analytics.

Sarvam Document Intelligence is NOT used here — it has its own legacy path
in `backend.job_manager._run_legacy_sarvam`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import fitz  # PyMuPDF
from PIL import Image

from .. import config
from ..chains import (
    build_fallback_voter_chain,
    build_metadata_extraction_chain,
    build_primary_voter_chain,
)
from ..classifiers import classify_caste, classify_religion
from ..llm.provider_factory import TokenUsageCallback
from ..schemas.voter import (
    BoothMetadata,
    BoothOutput,
    RawVoter,
    RawVotersOnPage,
    RoutingStats,
    Voter,
)
from ..utils.image_utils import encode_image_optimized
from .cost_tracker import (
    get_pdf_total_cost,
    log_cost_entry,
    log_fallback,
)
from .page_validator import validate_page_extraction
from .validator import deduplicate_voters, validate_extraction

log = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]


# ---------------------------------------------------------------------------
# PDF rendering (PyMuPDF — pure Python, no poppler)
# ---------------------------------------------------------------------------

def _render_page(pdf_path: str, page_num_1based: int, dpi: int) -> Optional[Image.Image]:
    doc = fitz.open(pdf_path)
    try:
        if not (1 <= page_num_1based <= doc.page_count):
            return None
        page = doc.load_page(page_num_1based - 1)
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def _page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    try:
        return doc.page_count
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Voter enrichment (rule-based classification, no LLM)
# ---------------------------------------------------------------------------

def _enrich(
    raw: RawVoter,
    page_num: int,
    metadata: BoothMetadata,
    extraction_model: str,
) -> Voter:
    religion, religion_conf, religion_match = classify_religion(raw.name, raw.relative_name)
    caste = caste_cat = caste_conf = caste_match = None
    if religion == "Hindu":
        caste, caste_cat, caste_conf, caste_match = classify_caste(raw.name, raw.relative_name)

    return Voter(
        serial_no=raw.serial_no,
        voter_id=raw.voter_id,
        name=raw.name,
        relation_type=raw.relation_type,
        relative_name=raw.relative_name,
        house_no=raw.house_no,
        age=raw.age,
        gender=raw.gender,
        page_no=page_num,
        booth_number=metadata.booth_number or None,
        booth_name=metadata.booth_name or None,
        religion=religion,
        religion_confidence=religion_conf if religion_conf in ("High", "Medium", "Low") else None,
        caste=caste,
        caste_category=caste_cat if caste_cat in ("UC", "OBC", "SC", "ST", "Unknown") else None,
        caste_confidence=caste_conf if caste_conf in ("High", "Medium", "Low") else None,
        surname_matched=caste_match or religion_match,
        extraction_model=extraction_model,
    )


# ---------------------------------------------------------------------------
# Hybrid routing per page
# ---------------------------------------------------------------------------

async def _process_page_with_fallback(
    pdf_path: str,
    pdf_name: str,
    page_num: int,
    primary_chain,
    fallback_chain,
    metadata: BoothMetadata,
    sem: asyncio.Semaphore,
) -> tuple[list[Voter], dict]:
    """Returns (voters, page_meta).

    page_meta is a sidecar dict with model_used / fallback_triggered /
    validation scores so we can roll up cost + quality stats per PDF.
    """
    async with sem:
        page_meta: dict = {
            "page": page_num,
            "model_used": "flash",
            "fallback_triggered": False,
        }

        try:
            image = await asyncio.to_thread(_render_page, pdf_path, page_num, config.DPI)
        except Exception as e:
            log.exception("page %d render failed for %s", page_num, pdf_name)
            page_meta["model_used"] = "render_failed"
            page_meta["error"] = str(e)
            return [], page_meta

        if image is None:
            page_meta["model_used"] = "no_image"
            return [], page_meta

        # Capture image bytes for fallback cost estimation if usage_metadata
        # doesn't surface from the LLM response.
        try:
            b64, _media = encode_image_optimized(image)
            image_bytes = int(len(b64) * 0.75)
        except Exception:
            image_bytes = 0

        # ------ PRIMARY (Flash) — with real token capture + timing ------
        flash_cb = TokenUsageCallback()
        primary_result: Optional[RawVotersOnPage] = None
        t0 = time.perf_counter()
        try:
            primary_result = await primary_chain.ainvoke(
                {"image": image},
                config={"callbacks": [flash_cb]},
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            validation = validate_page_extraction(primary_result)
            page_meta["flash_validation"] = validation
            voter_count = len(primary_result.voters) if primary_result else 0

            cost = log_cost_entry(
                pdf_name=pdf_name,
                model=config.PRIMARY_VISION_MODEL,
                input_tokens=flash_cb.input_tokens,
                output_tokens=flash_cb.output_tokens,
                cached_tokens=flash_cb.cached_tokens,
                page=page_num,
                voters=voter_count,
                duration_ms=duration_ms,
                image_bytes=image_bytes,
            )
            page_meta["flash_tokens_in"] = flash_cb.input_tokens
            page_meta["flash_tokens_out"] = flash_cb.output_tokens
            page_meta["flash_cost_inr"] = cost["total_inr"]
            page_meta["flash_duration_ms"] = round(duration_ms, 1)

            if validation["is_valid"]:
                voters = [
                    _enrich(rv, page_num, metadata, config.PRIMARY_VISION_MODEL)
                    for rv in primary_result.voters
                ]
                return voters, page_meta

            # Validation failed → fallback path
            page_meta["fallback_triggered"] = True
            page_meta["flash_failure_reasons"] = validation["reasons"]
            log_fallback(pdf_name, page_num, validation["reasons"], validation["score"])

        except Exception as e:
            log.warning("page %d Flash extraction failed for %s: %s", page_num, pdf_name, e)
            page_meta["fallback_triggered"] = True
            page_meta["flash_error"] = str(e)
            log_fallback(pdf_name, page_num, [f"flash_error: {type(e).__name__}"], 0.0)

        # ------ FALLBACK (Pro) — also with real token capture + timing ------
        if not config.FALLBACK_ENABLED:
            page_meta["model_used"] = "flash_failed_no_fallback"
            return [], page_meta

        if config.FALLBACK_RETRY_DELAY > 0:
            await asyncio.sleep(config.FALLBACK_RETRY_DELAY)

        pro_cb = TokenUsageCallback()
        t0 = time.perf_counter()
        try:
            fb_result = await fallback_chain.ainvoke(
                {"image": image},
                config={"callbacks": [pro_cb]},
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            page_meta["pro_validation"] = validate_page_extraction(fb_result)
            page_meta["model_used"] = "pro_fallback"
            voter_count = len(fb_result.voters) if fb_result else 0

            cost = log_cost_entry(
                pdf_name=pdf_name,
                model=config.FALLBACK_VISION_MODEL,
                input_tokens=pro_cb.input_tokens,
                output_tokens=pro_cb.output_tokens,
                cached_tokens=pro_cb.cached_tokens,
                page=page_num,
                voters=voter_count,
                duration_ms=duration_ms,
                image_bytes=image_bytes,
            )
            page_meta["pro_tokens_in"] = pro_cb.input_tokens
            page_meta["pro_tokens_out"] = pro_cb.output_tokens
            page_meta["pro_cost_inr"] = cost["total_inr"]
            page_meta["pro_duration_ms"] = round(duration_ms, 1)

            voters = [
                _enrich(rv, page_num, metadata, config.FALLBACK_VISION_MODEL)
                for rv in fb_result.voters
            ]
            return voters, page_meta
        except Exception as e:
            log.exception("page %d Pro fallback failed for %s", page_num, pdf_name)
            page_meta["model_used"] = "failed"
            page_meta["pro_error"] = str(e)
            return [], page_meta


# ---------------------------------------------------------------------------
# Top-level process_pdf
# ---------------------------------------------------------------------------

async def process_pdf(
    pdf_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> BoothOutput:
    pdf_path_obj = Path(pdf_path)
    pdf_name = pdf_path_obj.stem
    total_pages = _page_count(str(pdf_path_obj))
    dpi = config.DPI

    if progress_callback:
        await progress_callback(stage="metadata", processed_pages=0, total_pages=total_pages)

    metadata = await _extract_metadata(pdf_path_obj, dpi)

    if progress_callback:
        await progress_callback(
            stage="metadata_done",
            processed_pages=1,
            total_pages=total_pages,
            metadata=metadata.model_dump(),
        )

    # Voter pages = everything from page 2 onwards. The LLM ignores
    # non-card pages (returns empty list).
    voter_pages = list(range(2, total_pages + 1))

    primary_chain = build_primary_voter_chain()
    fallback_chain = build_fallback_voter_chain() if config.FALLBACK_ENABLED else None

    sem = asyncio.Semaphore(config.MAX_CONCURRENT_PAGES_PER_PDF)
    processed = {"count": 1}

    async def runner(page_num: int):
        voters, meta = await _process_page_with_fallback(
            str(pdf_path_obj),
            pdf_name,
            page_num,
            primary_chain,
            fallback_chain or primary_chain,
            metadata,
            sem,
        )
        processed["count"] += 1
        if progress_callback:
            await progress_callback(
                stage="voters",
                processed_pages=processed["count"],
                total_pages=total_pages,
                page_num=page_num,
                page_voter_count=len(voters),
                model_used=meta.get("model_used"),
            )
        return voters, meta

    page_results = await asyncio.gather(*(runner(p) for p in voter_pages))

    all_voters: list[Voter] = []
    page_meta_list: list[dict] = []
    for voters, meta in page_results:
        all_voters.extend(voters)
        page_meta_list.append(meta)

    # Dedupe + sort
    unique, duplicates = deduplicate_voters(all_voters)
    unique.sort(key=lambda v: (v.serial_no if v.serial_no is not None else 1_000_000))
    validation = validate_extraction(unique, metadata, duplicates_removed=len(duplicates))

    flash_pages = sum(1 for m in page_meta_list if m.get("model_used") == "flash")
    pro_pages = sum(1 for m in page_meta_list if m.get("model_used") == "pro_fallback")
    failed_pages = sum(
        1 for m in page_meta_list
        if m.get("model_used") in ("failed", "flash_failed_no_fallback", "render_failed", "no_image")
    )
    fallback_rate = round(
        pro_pages / max(1, len(voter_pages)) * 100, 2
    ) if voter_pages else 0.0

    # Pull the ACTUAL cost for this PDF from the cost log (single source of truth).
    real = get_pdf_total_cost(pdf_name)
    routing = RoutingStats(
        flash_pages=flash_pages,
        pro_fallback_pages=pro_pages,
        failed_pages=failed_pages,
        fallback_rate_pct=fallback_rate,
        cost_inr=real["total_inr"],
        cost_usd=real["total_usd"],
        input_tokens=real["total_input_tokens"],
        output_tokens=real["total_output_tokens"],
        estimated_cost_inr=real["total_inr"],  # alias for back-compat
        page_metadata=page_meta_list,
    )

    return BoothOutput(
        metadata=metadata,
        validation=validation,
        voters=unique,
        processed_at=datetime.utcnow(),
        pdf_source=pdf_path_obj.name,
        total_pages=total_pages,
        llm_provider=config.LLM_PROVIDER,
        llm_model=f"hybrid:{config.PRIMARY_VISION_MODEL}+{config.FALLBACK_VISION_MODEL}"
        if config.FALLBACK_ENABLED
        else config.PRIMARY_VISION_MODEL,
        routing_stats=routing,
    )


async def _extract_metadata(pdf_path: Path, dpi: int) -> BoothMetadata:
    image = await asyncio.to_thread(_render_page, str(pdf_path), 1, dpi)
    if image is None:
        return BoothMetadata()
    chain = build_metadata_extraction_chain()
    try:
        return await chain.ainvoke({"image": image})
    except Exception:
        log.exception("metadata extraction failed for %s", pdf_path.name)
        return BoothMetadata()
