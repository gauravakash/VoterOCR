"""Per-page extraction quality validator — decides whether to fall back to Pro."""

from __future__ import annotations

import re

from .. import config
from ..schemas.voter import RawVotersOnPage


VALID_VOTER_ID_RE = re.compile(r"^(XFF\d{6,8}|FPP\d{6,8}|UP/\d+/\d+/\d+)$")


def validate_page_extraction(result: RawVotersOnPage) -> dict:
    """Returns {is_valid, reasons, score, voter_count}.

    is_valid is True only when score >= 0.7 AND no validation reasons fired.
    """
    if not result or not result.voters:
        return {
            "is_valid": False,
            "reasons": ["empty_extraction"],
            "score": 0.0,
            "voter_count": 0,
        }

    voters = result.voters
    reasons: list[str] = []
    score = 1.0

    n = len(voters)
    if n < config.MIN_VOTERS_PER_PAGE:
        reasons.append(f"too_few_voters ({n} < {config.MIN_VOTERS_PER_PAGE})")
        score -= 0.4
    elif n > config.MAX_VOTERS_PER_PAGE:
        reasons.append(f"too_many_voters ({n} > {config.MAX_VOTERS_PER_PAGE})")
        score -= 0.3

    invalid_ids = [v for v in voters if not VALID_VOTER_ID_RE.match(v.voter_id or "")]
    if invalid_ids:
        ratio = len(invalid_ids) / n
        if ratio > 0.10:
            reasons.append(f"invalid_voter_ids ({len(invalid_ids)}/{n})")
            score -= 0.5

    missing = sum(1 for v in voters if not v.name or not v.voter_id or v.age is None)
    if missing:
        ratio = missing / n
        if ratio > 0.05:
            reasons.append(f"missing_required_fields ({missing}/{n})")
            score -= 0.3

    voter_ids = [v.voter_id for v in voters if v.voter_id]
    duplicates = len(voter_ids) - len(set(voter_ids))
    if duplicates > 0:
        reasons.append(f"duplicate_ids_in_page ({duplicates})")
        score -= 0.2

    invalid_ages = [v for v in voters if v.age and (v.age < 18 or v.age > 120)]
    if len(invalid_ages) > 2:
        reasons.append(f"invalid_ages ({len(invalid_ages)})")
        score -= 0.15

    invalid_genders = [v for v in voters if v.gender not in ("Male", "Female", "Other", None)]
    if invalid_genders:
        reasons.append(f"invalid_genders ({len(invalid_genders)})")
        score -= 0.1

    score = max(0.0, score)
    return {
        "is_valid": (score >= 0.7 and not reasons),
        "reasons": reasons,
        "score": round(score, 3),
        "voter_count": n,
    }
