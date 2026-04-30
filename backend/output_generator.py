"""Generate final outputs: JSON, multi-sheet Excel, summary JSON.

Religion classification is intentionally absent — it will be added later as
a separate post-processing step on the clean voter JSON. The output now
embeds page-1 metadata and a validation block so each booth can be audited
against its declared totals.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config
from .pdf_processor import clean_ocr_text


VOTER_COLUMNS = [
    "serial_no", "voter_id", "name", "relation_type",
    "relative_name", "house_no", "age", "gender", "page_no",
]


def _scrub(value):
    if value is None:
        return None
    s = str(value)
    if not s or s.lower() == "none":
        return None
    cleaned = clean_ocr_text(s)
    return cleaned or None


def write_outputs(
    pdf_name: str,
    voters: list[dict],
    total_pages: int,
    cost_estimate: float,
    metadata: Optional[dict] = None,
    validation: Optional[dict] = None,
    dropped: Optional[list[dict]] = None,
    output_dir: Path = config.OUTPUT_DIR,
) -> tuple[Path, Path, Path]:
    """Write JSON, Excel, summary JSON. Returns the three paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf_name).stem

    json_path = output_dir / f"{stem}_voters.json"
    xlsx_path = output_dir / f"{stem}_voters.xlsx"
    summary_path = output_dir / f"{stem}_summary.json"

    metadata = metadata or {}
    validation = validation or {}
    dropped = dropped or []

    generated_at = datetime.utcnow().isoformat() + "Z"

    male = sum(1 for v in voters if v.get("gender") == "Male")
    female = sum(1 for v in voters if v.get("gender") == "Female")
    other = sum(1 for v in voters if v.get("gender") == "Other")
    with_age = sum(1 for v in voters if v.get("age") is not None)
    unique_ids = len({v.get("voter_id") for v in voters if v.get("voter_id")})

    metadata_block = {
        "pdf_name": pdf_name,
        "total_pages": total_pages,
        "constituency": metadata.get("constituency"),
        "booth_number": metadata.get("booth_number"),
        "booth_name": metadata.get("booth_name"),
        "pin_code": metadata.get("pin_code"),
        "expected_voter_count": metadata.get("expected_voter_count", 0),
        "expected_male": metadata.get("expected_male", 0),
        "expected_female": metadata.get("expected_female", 0),
        "expected_third": metadata.get("expected_third", 0),
        "extracted_voter_count": len(voters),
        "extracted_male": male,
        "extracted_female": female,
        "extracted_other": other,
        "with_age": with_age,
        "unique_voter_ids": unique_ids,
        "cost_estimate_inr": round(cost_estimate, 2),
        "generated_at": generated_at,
    }

    json_data = {
        "metadata": metadata_block,
        "validation": validation,
        "voters": voters,
        "dropped_records": dropped,
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(json_data, fh, ensure_ascii=False, indent=2)

    summary_data = dict(metadata_block)
    summary_data["validation"] = validation
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary_data, fh, ensure_ascii=False, indent=2)

    _write_excel(xlsx_path, voters, dropped, metadata_block, validation)

    return json_path, xlsx_path, summary_path


def _write_excel(
    path: Path,
    voters: list[dict],
    dropped: list[dict],
    metadata: dict,
    validation: dict,
) -> None:
    df = pd.DataFrame(voters)
    for c in VOTER_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[VOTER_COLUMNS]

    # Safety net: ensure no HTML survived into the spreadsheet
    for col in ("name", "relative_name", "house_no"):
        df[col] = df[col].map(_scrub)

    val_rows = [
        ("PDF", metadata.get("pdf_name")),
        ("Constituency", metadata.get("constituency") or ""),
        ("Booth Number", metadata.get("booth_number") or ""),
        ("Booth Name", metadata.get("booth_name") or ""),
        ("Pin Code", metadata.get("pin_code") or ""),
        ("", ""),
        ("Expected Voters (PDF page 1)", validation.get("expected_total", 0)),
        ("Extracted Voters", validation.get("extracted_total", 0)),
        ("Difference", validation.get("difference", 0)),
        ("Status", validation.get("status", "PENDING")),
        ("", ""),
        ("Expected Male", validation.get("expected_male", 0)),
        ("Extracted Male", validation.get("extracted_male", 0)),
        ("Expected Female", validation.get("expected_female", 0)),
        ("Extracted Female", validation.get("extracted_female", 0)),
        ("Extracted Other", validation.get("extracted_other", 0)),
        ("", ""),
        ("Missing Serial Numbers (first 50)", str(validation.get("missing_serial_numbers", []))),
        ("Missing Serial Count", validation.get("missing_serial_count", 0)),
        ("Duplicate Serials", str(validation.get("duplicate_serials", []))),
        ("Duplicate Serial Count", validation.get("duplicate_serial_count", 0)),
        ("Dropped Records (no voter_id / no name)", validation.get("dropped_count", len(dropped))),
        ("", ""),
        ("Cost Estimate (INR)", round(metadata.get("cost_estimate_inr", 0), 2)),
        ("Generated", metadata.get("generated_at")),
    ]
    df_validation = pd.DataFrame(val_rows, columns=["Metric", "Value"])

    df_dropped = pd.DataFrame(dropped) if dropped else pd.DataFrame(
        columns=["serial_no", "voter_id", "name"]
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Voters")
        df_validation.to_excel(writer, index=False, sheet_name="Validation")
        if not df_dropped.empty:
            df_dropped.to_excel(writer, index=False, sheet_name="Dropped")
