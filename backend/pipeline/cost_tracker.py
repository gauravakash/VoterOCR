"""Centralized cost calculation — single source of truth.

All cost displays (frontend tile, Excel sheets, JSON output, per-job snapshot)
read from this module. Token counts come from `TokenUsageCallback` and are
the *actual* numbers Google billed for, not estimates.

Pricing reference: https://ai.google.dev/gemini-api/docs/pricing
Update PRICING_USD_PER_1M_TOKENS if Google changes rates. The previous
estimate was using gemini-1.5-flash pricing for gemini-2.5-flash, which
undercounted cost ~4x.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

from .. import config


# USD per 1M tokens. Updated April 2026 — re-verify monthly.
PRICING_USD_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {
        "input":         0.30,
        "output":        2.50,
        "cached_input":  0.075,
    },
    "gemini-2.5-pro": {
        "input":         1.25,    # ≤200k context
        "input_long":    2.50,    # >200k context
        "output":       10.00,    # ≤200k context
        "output_long":  15.00,    # >200k context
        "cached_input":  0.31,
    },
    "gemini-2.5-flash-lite": {
        "input":         0.10,
        "output":        0.40,
        "cached_input":  0.025,
    },
}

USD_TO_INR = float(os.getenv("USD_TO_INR_RATE", "84.0"))

COST_LOG_PATH: Path = config.ROOT_DIR / "data" / "cost_log.jsonl"
FALLBACK_LOG_PATH: Path = config.ROOT_DIR / "data" / "fallback_log.jsonl"

_lock = Lock()


def _normalize_model(model: str) -> str:
    m = (model or "").lower().strip()
    if m in PRICING_USD_PER_1M_TOKENS:
        return m
    if "flash-lite" in m:
        return "gemini-2.5-flash-lite"
    if "flash" in m:
        return "gemini-2.5-flash"
    if "pro" in m:
        return "gemini-2.5-pro"
    return m


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> dict:
    """Returns {input_cost_usd, output_cost_usd, cached_cost_usd, total_usd, total_inr, breakdown}."""
    key = _normalize_model(model)
    pricing = PRICING_USD_PER_1M_TOKENS.get(key)
    if not pricing:
        return {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "cached_cost_usd": 0.0,
            "total_usd": 0.0,
            "total_inr": 0.0,
            "usd_to_inr_rate": USD_TO_INR,
            "breakdown": f"unknown_model: {model}",
            "error": True,
        }

    if key == "gemini-2.5-pro" and input_tokens > 200_000:
        input_rate = pricing["input_long"]
        output_rate = pricing["output_long"]
    else:
        input_rate = pricing["input"]
        output_rate = pricing["output"]

    regular_input = max(0, input_tokens - cached_tokens)
    input_cost = (regular_input / 1_000_000) * input_rate
    output_cost = (output_tokens / 1_000_000) * output_rate
    cached_cost = (cached_tokens / 1_000_000) * pricing.get("cached_input", 0.0)

    total_usd = input_cost + output_cost + cached_cost
    total_inr = total_usd * USD_TO_INR

    return {
        "model": key,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "input_cost_usd": round(input_cost, 6),
        "output_cost_usd": round(output_cost, 6),
        "cached_cost_usd": round(cached_cost, 6),
        "total_usd": round(total_usd, 4),
        "total_inr": round(total_inr, 2),
        "usd_to_inr_rate": USD_TO_INR,
        "breakdown": (
            f"{key}: in={input_tokens:,}t × ${input_rate}/M = ${input_cost:.4f}, "
            f"out={output_tokens:,}t × ${output_rate}/M = ${output_cost:.4f}, "
            f"total = ${total_usd:.4f} = ₹{total_inr:.2f}"
        ),
    }


def estimate_tokens_fallback(image_bytes: int, voter_count: int) -> tuple[int, int]:
    """Conservative token estimate when usage_metadata didn't surface.

    Gemini 2.5 charges ~258 tokens per 768x768 image patch. A typical
    1600x2200 ECI roll page is ~6 patches ≈ 1550 image tokens. Add ~500
    for the prompt. For output, a Devanagari voter JSON is ~150-220 tokens
    per voter (structured field names + values).

    These numbers err on the high side so we never undercount.
    """
    image_kb = max(1, image_bytes // 1024)
    # 768px patches: 6 patches @ 258 = 1550. Scale gently with KB.
    image_tokens = max(1500, int(image_kb * 2.0))
    prompt_tokens = 600
    input_total = image_tokens + prompt_tokens

    if voter_count > 0:
        output_total = voter_count * 200
    else:
        output_total = 300
    return input_total, output_total


def log_cost_entry(
    pdf_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    page: int | None = None,
    voters: int = 0,
    duration_ms: float | None = None,
    image_bytes: int = 0,
    estimated: bool = False,
) -> dict:
    """Append-only JSONL log of one API call. Returns the cost dict.

    If `input_tokens` and `output_tokens` are both zero (callback didn't
    fire), substitute a fallback estimate from image_bytes + voter_count
    so cost is never ₹0 for a successful call.
    """
    if not estimated and input_tokens == 0 and output_tokens == 0 and (image_bytes > 0 or voters > 0):
        input_tokens, output_tokens = estimate_tokens_fallback(image_bytes, voters)
        estimated = True

    cost = calculate_cost(model, input_tokens, output_tokens, cached_tokens)
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "pdf": pdf_name,
        "page": page,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "voters": voters,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "estimated": estimated,
        "cost_usd": cost["total_usd"],
        "cost_inr": cost["total_inr"],
    }
    with _lock:
        with open(COST_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return cost


def log_fallback(pdf_name: str, page_num: int, reasons: list[str], score: float) -> None:
    if not config.ENABLE_FALLBACK_LOGGING:
        return
    FALLBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "pdf": pdf_name,
        "page": page_num,
        "flash_score": score,
        "failure_reasons": reasons,
    }
    with _lock:
        with open(FALLBACK_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_log_entries() -> list[dict]:
    if not COST_LOG_PATH.exists():
        return []
    out: list[dict] = []
    with open(COST_LOG_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def get_pdf_total_cost(pdf_name: str) -> dict:
    """Sum up actual cost for one PDF across all its page calls (incl. fallbacks)."""
    total_inr = total_usd = 0.0
    total_in = total_out = 0
    by_model: dict[str, dict] = {}
    page_count = 0
    for e in _read_log_entries():
        if e.get("pdf") != pdf_name:
            continue
        page_count += 1
        total_inr += float(e.get("cost_inr", 0) or 0)
        total_usd += float(e.get("cost_usd", 0) or 0)
        total_in += int(e.get("input_tokens", 0) or 0)
        total_out += int(e.get("output_tokens", 0) or 0)
        m = e.get("model", "unknown")
        b = by_model.setdefault(m, {"pages": 0, "cost_inr": 0.0, "cost_usd": 0.0})
        b["pages"] += 1
        b["cost_inr"] += float(e.get("cost_inr", 0) or 0)
        b["cost_usd"] += float(e.get("cost_usd", 0) or 0)
    return {
        "pdf": pdf_name,
        "total_inr": round(total_inr, 2),
        "total_usd": round(total_usd, 4),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "calls_logged": page_count,
        "by_model": {
            k: {**v, "cost_inr": round(v["cost_inr"], 2), "cost_usd": round(v["cost_usd"], 4)}
            for k, v in by_model.items()
        },
    }


def get_global_cost_summary() -> dict:
    total_inr = total_usd = 0.0
    pdfs: set[str] = set()
    by_model: dict[str, dict] = {}
    durations: list[float] = []
    flash_pages = pro_pages = 0
    for e in _read_log_entries():
        total_inr += float(e.get("cost_inr", 0) or 0)
        total_usd += float(e.get("cost_usd", 0) or 0)
        pdfs.add(e.get("pdf"))
        m = (e.get("model") or "unknown").lower()
        b = by_model.setdefault(m, {"pages": 0, "cost_inr": 0.0})
        b["pages"] += 1
        b["cost_inr"] += float(e.get("cost_inr", 0) or 0)
        if "flash" in m:
            flash_pages += 1
        elif "pro" in m:
            pro_pages += 1
        d = e.get("duration_ms")
        if isinstance(d, (int, float)) and d > 0:
            durations.append(float(d))
    avg_ms = round(sum(durations) / len(durations), 1) if durations else 0.0
    return {
        "total_inr": round(total_inr, 2),
        "total_usd": round(total_usd, 4),
        "total_pdfs": len({p for p in pdfs if p}),
        "flash_pages": flash_pages,
        "pro_pages": pro_pages,
        "total_pages": flash_pages + pro_pages,
        "avg_page_seconds": round(avg_ms / 1000, 2),
        "by_model": {k: {**v, "cost_inr": round(v["cost_inr"], 2)} for k, v in by_model.items()},
        "usd_to_inr_rate": USD_TO_INR,
    }


# ---------------------------------------------------------------------------
# Backwards-compat shims for callers that still import old names
# ---------------------------------------------------------------------------

def estimate_page_cost_inr(model: str, image_kb: float, output_voters: int = 30) -> float:
    """Deprecated — kept so old call sites still link. Returns 0; real cost
    comes from `log_cost_entry()` which uses actual token counts."""
    return 0.0


def get_cost_summary() -> dict:
    return get_global_cost_summary()


def get_routing_stats() -> dict:
    if not FALLBACK_LOG_PATH.exists():
        return {"total_fallbacks": 0, "fallback_reasons": {}, "recent_fallbacks": []}
    fbs: list[dict] = []
    with open(FALLBACK_LOG_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                fbs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    reasons: dict[str, int] = {}
    for fb in fbs:
        for r in fb.get("failure_reasons", []):
            base = r.split(" (")[0]
            reasons[base] = reasons.get(base, 0) + 1
    return {
        "total_fallbacks": len(fbs),
        "fallback_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])),
        "recent_fallbacks": fbs[-20:],
    }


def log_page_cost(*args, **kwargs) -> None:
    """Deprecated shim — kept so existing `pdf_processor` import doesn't break.
    Real per-page cost is recorded via `log_cost_entry`. No-op."""
    return None
