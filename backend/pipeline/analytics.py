"""Booth-level + cross-booth analytics on classified voter data."""

from __future__ import annotations

from collections import Counter
from typing import Iterable


def _voter_dict(v) -> dict:
    """Accept either a Voter Pydantic model or a plain dict."""
    if hasattr(v, "model_dump"):
        return v.model_dump()
    return dict(v)


def compute_booth_analytics(voters: Iterable) -> dict:
    voters = [_voter_dict(v) for v in voters]
    total = len(voters)
    if total == 0:
        return {
            "total_voters": 0,
            "religion": {},
            "religion_percentages": {},
            "hindu_castes": {},
            "hindu_caste_categories": {},
            "top_10_surnames": [],
            "rare_surnames": [],
            "gender": {},
            "age_distribution": {},
            "unknown_caste_count": 0,
        }

    religion_counts = Counter(v.get("religion") or "Unknown" for v in voters)
    religion_percentages = {k: round(c / total * 100, 2) for k, c in religion_counts.items()}

    hindu_voters = [v for v in voters if v.get("religion") == "Hindu"]
    caste_counts = Counter(v.get("caste") or "Unknown" for v in hindu_voters)
    category_counts = Counter(v.get("caste_category") or "Unknown" for v in hindu_voters)

    surnames: list[str] = []
    for v in voters:
        for field in ("name", "relative_name"):
            text = (v.get(field) or "").strip()
            if not text:
                continue
            tokens = text.split()
            if tokens:
                surnames.append(tokens[-1])
    surname_counts = Counter(surnames)
    top_10 = [{"surname": s, "count": c} for s, c in surname_counts.most_common(10)]
    rare = sorted(
        [(s, c) for s, c in surname_counts.items() if c <= 2],
        key=lambda x: -x[1],
    )

    gender_counts = Counter(v.get("gender") or "Unknown" for v in voters)

    ages = [v.get("age") for v in voters if isinstance(v.get("age"), int)]
    age_buckets = {
        "18-25": sum(1 for a in ages if 18 <= a <= 25),
        "26-35": sum(1 for a in ages if 26 <= a <= 35),
        "36-45": sum(1 for a in ages if 36 <= a <= 45),
        "46-55": sum(1 for a in ages if 46 <= a <= 55),
        "56-65": sum(1 for a in ages if 56 <= a <= 65),
        "65+":   sum(1 for a in ages if a > 65),
    }

    return {
        "total_voters": total,
        "religion": dict(religion_counts),
        "religion_percentages": religion_percentages,
        "hindu_castes": dict(caste_counts),
        "hindu_caste_categories": dict(category_counts),
        "top_10_surnames": top_10,
        "rare_surnames": [{"surname": s, "count": c} for s, c in rare],
        "gender": dict(gender_counts),
        "age_distribution": age_buckets,
        "unknown_caste_count": sum(1 for v in hindu_voters if not v.get("caste")),
    }


def compute_aggregate_analytics(all_booth_payloads: Iterable[dict]) -> dict:
    all_voters: list[dict] = []
    booth_summaries: list[dict] = []
    for payload in all_booth_payloads:
        voters = payload.get("voters", []) or []
        all_voters.extend(_voter_dict(v) for v in voters)
        summary = compute_booth_analytics(voters)
        meta = payload.get("metadata") or {}
        summary["booth_number"] = meta.get("booth_number")
        summary["booth_name"] = meta.get("booth_name")
        summary["pdf_source"] = payload.get("pdf_source") or meta.get("pdf_name")
        booth_summaries.append(summary)

    aggregate = compute_booth_analytics(all_voters)
    aggregate["total_booths"] = len(booth_summaries)
    aggregate["booth_summaries"] = booth_summaries
    return aggregate


def get_unknown_caste_voters(voters: Iterable) -> list[dict]:
    """Hindu voters whose caste couldn't be determined — for manual review."""
    return [
        _voter_dict(v) for v in voters
        if (_voter_dict(v).get("religion") == "Hindu" and not _voter_dict(v).get("caste"))
    ]
