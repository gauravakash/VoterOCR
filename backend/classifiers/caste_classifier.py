"""Rule-based Hindu caste classifier.

Should ONLY be called for voters already classified as Hindu.

Strategy (in order of confidence):
1. Last token of voter's name == known caste surname → High
2. Any token in voter's name == known caste surname → High
3. Last token of relative's name == known caste surname → Medium
4. Any token in relative's name == known caste surname → Medium
5. Pure "सिंह" → low-confidence Thakur/Rajput (UP default)
6. No match → return None (manual review)
"""

from __future__ import annotations

from typing import Optional

from .dictionaries.caste_tokens import AMBIGUOUS_TOKENS, TOKEN_TO_CASTE
from .religion_classifier import tokenize


def _lookup(token: str) -> Optional[tuple[str, str]]:
    if token in AMBIGUOUS_TOKENS:
        return None
    return TOKEN_TO_CASTE.get(token)


def classify_caste(
    name: Optional[str],
    relative_name: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], str, Optional[str]]:
    """Returns (caste, category, confidence, matched_token).

    None caste means "Unknown — needs manual review".
    """
    name_tokens = tokenize(name)
    rel_tokens = tokenize(relative_name)

    # Strategy 1 & 2 — voter's name (last token first, then any token)
    if name_tokens:
        last = name_tokens[-1]
        hit = _lookup(last)
        if hit:
            return hit[0], hit[1], "High", last
        for tok in name_tokens:
            hit = _lookup(tok)
            if hit:
                return hit[0], hit[1], "High", tok

    # Strategy 3 & 4 — relative's name
    if rel_tokens:
        last = rel_tokens[-1]
        hit = _lookup(last)
        if hit:
            return hit[0], hit[1], "Medium", f"{last} (relative)"
        for tok in rel_tokens:
            hit = _lookup(tok)
            if hit:
                return hit[0], hit[1], "Medium", f"{tok} (relative)"

    # Strategy 5 — pure "सिंह" defaults to Thakur/Rajput in UP, low confidence
    if "सिंह" in name_tokens or "सिंह" in rel_tokens:
        return "Thakur/Rajput", "UC", "Low", "सिंह (ambiguous)"

    return None, None, "Low", None
