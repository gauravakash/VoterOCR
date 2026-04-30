"""Rule-based religion classifier (Hindu / Muslim / Sikh / Unknown).

Priority order:
  1. Sikh — कौर is unambiguous; SIKH_STRONG token in name → High.
  2. Muslim — strong token in name (excl. AMBIGUOUS_TOKENS), or compound
     substring like मोहम्मद / अल्लाह / उद्दीन → High.
  3. Hindu — strong token in name (excl. AMBIGUOUS), or compound substring
     like कुमार / देवी / प्रसाद / चंद्र → High.
  4. Tiebreak from relative_name when own name has no signal.
  5. Unknown otherwise.

The substring-pattern step catches names that aren't tokenized cleanly
(e.g. "मोहम्मदसलमान" with no space). This is what cuts the Unknown rate
on real ECI rolls.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .dictionaries.religion_tokens import (
    AMBIGUOUS_TOKENS,
    HINDU_GENERAL_TOKENS,
    HINDU_WEAK,
    MUSLIM_STRONG,
    MUSLIM_WEAK,
    SIKH_STRONG,
)


_TOKEN_SPLIT = re.compile(r"[\s,;/()\[\]\-–—।॥‌‍]+")

_MUSLIM_SUBSTRINGS = (
    "मोहम्मद", "मुहम्मद", "मु0", "मु.", "मो0", "मो.",
    "अल्लाह", "अल्ला",
    "उद्दीन", "उद्दिन", "द्दीन",
    "अब्दुल", "अब्दुर", "अब्दुस",
)

_HINDU_SUBSTRINGS = (
    "कुमार", "कुमारी",
    "देवी",
    "प्रसाद", "प्रसाद",
    "चन्द्र", "चंद्र",
    "नाथ",
    "नारायण", "नारायन",
)


def tokenize(text: Optional[str]) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for raw in _TOKEN_SPLIT.split(text.strip()):
        t = raw.strip()
        if not t:
            continue
        out.append(t)
        stripped = t.rstrip(".।॥०0o")
        if stripped and stripped != t:
            out.append(stripped)
    return out


def _has_substring(text: str, needles: tuple[str, ...]) -> Optional[str]:
    if not text:
        return None
    for n in needles:
        if n in text:
            return n
    return None


def _strong_match(tokens: Iterable[str], dictionary: set[str]) -> Optional[str]:
    """First strong-dict hit that is NOT in AMBIGUOUS_TOKENS."""
    for t in tokens:
        if t in dictionary and t not in AMBIGUOUS_TOKENS:
            return t
    return None


def _weak_match(tokens: Iterable[str], dictionary: set[str]) -> Optional[str]:
    for t in tokens:
        if t in dictionary:
            return t
    return None


def classify_religion(
    name: Optional[str],
    relative_name: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """Returns (religion, confidence, matched_token)."""
    if not name:
        return "Unknown", "Low", None

    name_clean = name.strip()
    name_tokens = tokenize(name_clean)
    rel_clean = (relative_name or "").strip()
    rel_tokens = tokenize(rel_clean)

    # 1. Sikh — कौर is the most reliable single marker; check token + substring.
    if "कौर" in name_clean:
        return "Sikh", "High", "कौर"
    sikh_in_name = _strong_match(name_tokens, SIKH_STRONG)
    if sikh_in_name:
        return "Sikh", "High", sikh_in_name
    sikh_in_rel = _strong_match(rel_tokens, SIKH_STRONG)
    if sikh_in_rel:
        return "Sikh", "Medium", f"{sikh_in_rel} (relative)"

    # 2. Muslim — strong token, then compound substring.
    muslim_in_name = _strong_match(name_tokens, MUSLIM_STRONG)
    if muslim_in_name:
        return "Muslim", "High", muslim_in_name
    muslim_sub = _has_substring(name_clean, _MUSLIM_SUBSTRINGS)
    if muslim_sub:
        return "Muslim", "High", f"{muslim_sub} (substring)"

    # 3. Hindu — strong token, then compound substring.
    hindu_in_name = _strong_match(name_tokens, HINDU_GENERAL_TOKENS)
    if hindu_in_name:
        return "Hindu", "High", hindu_in_name
    hindu_sub = _has_substring(name_clean, _HINDU_SUBSTRINGS)
    if hindu_sub:
        return "Hindu", "High", f"{hindu_sub} (substring)"

    # 4. Tiebreak via relative name. Score both sides; the side with a clear
    # signal wins. If both sides have signals, mark Medium.
    if rel_clean:
        muslim_in_rel = _strong_match(rel_tokens, MUSLIM_STRONG)
        muslim_rel_sub = _has_substring(rel_clean, _MUSLIM_SUBSTRINGS)
        hindu_in_rel = _strong_match(rel_tokens, HINDU_GENERAL_TOKENS)
        hindu_rel_sub = _has_substring(rel_clean, _HINDU_SUBSTRINGS)

        rel_muslim = muslim_in_rel or muslim_rel_sub
        rel_hindu = hindu_in_rel or hindu_rel_sub

        if rel_muslim and not rel_hindu:
            tag = muslim_in_rel or f"{muslim_rel_sub} (substring)"
            return "Muslim", "Medium", f"{tag} (relative)"
        if rel_hindu and not rel_muslim:
            tag = hindu_in_rel or f"{hindu_rel_sub} (substring)"
            return "Hindu", "Medium", f"{tag} (relative)"

    # 5. Last-ditch: weak markers (only if absolutely nothing else fired).
    weak_muslim = _weak_match(name_tokens, MUSLIM_WEAK)
    weak_hindu = _weak_match(name_tokens, HINDU_WEAK)
    if weak_muslim and not weak_hindu:
        return "Muslim", "Low", weak_muslim
    if weak_hindu and not weak_muslim:
        return "Hindu", "Low", weak_hindu

    return "Unknown", "Low", None
