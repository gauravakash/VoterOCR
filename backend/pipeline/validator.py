"""Deduplication + count-based validation of LangChain extraction output."""

from __future__ import annotations

from ..schemas.voter import BoothMetadata, ValidationReport, Voter


def deduplicate_voters(voters: list[Voter]) -> tuple[list[Voter], list[Voter]]:
    """Collapse duplicates using (voter_id, name) as the key.

    Same voter emitted twice (e.g. due to multi-page batching overlap)
    collapses; same voter_id paired with two different names is kept as
    two records since both are real voters even if the LLM mis-paired an
    ID. Returns (uniques, duplicates_removed).
    """
    seen: dict[tuple[str, str], Voter] = {}
    duplicates: list[Voter] = []

    for v in voters:
        if not v.voter_id or not v.name:
            duplicates.append(v)
            continue
        key = (v.voter_id, v.name)
        if key in seen:
            existing = seen[key]
            existing_score = sum(
                1 for x in existing.model_dump().values() if x not in (None, "")
            )
            new_score = sum(
                1 for x in v.model_dump().values() if x not in (None, "")
            )
            if new_score > existing_score:
                duplicates.append(existing)
                seen[key] = v
            else:
                duplicates.append(v)
        else:
            seen[key] = v

    return list(seen.values()), duplicates


def validate_extraction(
    voters: list[Voter],
    metadata: BoothMetadata,
    duplicates_removed: int = 0,
) -> ValidationReport:
    """Compare extraction against page-1 declared totals."""
    expected = metadata.expected_voter_count or 0
    actual = len(voters)
    diff = actual - expected

    male = sum(1 for v in voters if v.gender == "Male")
    female = sum(1 for v in voters if v.gender == "Female")

    serials = [v.serial_no for v in voters if v.serial_no]
    serial_counts: dict[int, int] = {}
    for s in serials:
        serial_counts[s] = serial_counts.get(s, 0) + 1

    if expected > 0:
        missing = sorted(set(range(1, expected + 1)) - set(serials))
    else:
        missing = []
    duplicate_serials = sorted(s for s, c in serial_counts.items() if c > 1)

    vid_counts: dict[str, int] = {}
    for v in voters:
        if v.voter_id:
            vid_counts[v.voter_id] = vid_counts.get(v.voter_id, 0) + 1
    duplicate_voter_ids = sorted(vid for vid, c in vid_counts.items() if c > 1)

    if expected <= 0:
        status = "UNKNOWN"
    elif diff == 0:
        status = "OK"
    elif abs(diff) / expected <= 0.02:
        status = "MINOR_MISMATCH"
    elif abs(diff) / expected <= 0.10:
        status = "MAJOR_MISMATCH"
    else:
        status = "FAILED"

    return ValidationReport(
        expected_total=expected,
        extracted_total=actual,
        difference=diff,
        match=(diff == 0),
        expected_male=metadata.expected_male,
        extracted_male=male,
        expected_female=metadata.expected_female,
        extracted_female=female,
        missing_serial_numbers=missing[:50],
        missing_serial_count=len(missing),
        duplicate_serials=duplicate_serials[:50],
        duplicate_serial_count=len(duplicate_serials),
        duplicate_voter_ids=duplicate_voter_ids[:50],
        duplicate_voter_id_count=len(duplicate_voter_ids),
        duplicates_removed=duplicates_removed,
        status=status,
    )
