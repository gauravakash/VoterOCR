"""PDF chunking + Sarvam OCR output → voter-record parsing.

Pipeline per booth PDF:
  1. Split source PDF into N-page chunks (Sarvam limit = 10 pages)
  2. For each chunk, submit to Sarvam, receive ZIP of markdown
  3. Scrub HTML/table artifacts from the markdown
  4. Anchor on voter IDs (one per card) to split into per-card blocks
  5. Parse each card block field-by-field with bounded regex
  6. Deduplicate by voter_id (keeping most-complete record)
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from html import unescape
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter

from . import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF chunking
# ---------------------------------------------------------------------------

def get_page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def split_pdf(
    pdf_path: Path,
    output_dir: Path,
    pages_per_chunk: int = config.SARVAM_PAGES_PER_CHUNK,
) -> list[tuple[Path, int, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    stem = pdf_path.stem
    chunks: list[tuple[Path, int, int]] = []
    for i in range(0, total, pages_per_chunk):
        page_start = i + 1
        page_end = min(i + pages_per_chunk, total)
        writer = PdfWriter()
        for p in range(i, page_end):
            writer.add_page(reader.pages[p])
        chunk_path = output_dir / f"{stem}_p{page_start:03d}-{page_end:03d}.pdf"
        with open(chunk_path, "wb") as fh:
            writer.write(fh)
        chunks.append((chunk_path, page_start, page_end))
    return chunks


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def extract_markdown_from_zip(zip_bytes: bytes) -> str:
    """Sarvam returns a ZIP. Concatenate markdown / json / html files inside."""
    out_parts: list[str] = []
    bio = io.BytesIO(zip_bytes)
    try:
        with zipfile.ZipFile(bio) as zf:
            names = sorted(zf.namelist())
            md_names = [n for n in names if n.lower().endswith(".md")]
            txt_names = [n for n in names if n.lower().endswith(".txt")]
            json_names = [n for n in names if n.lower().endswith(".json")]
            html_names = [n for n in names if n.lower().endswith((".html", ".htm"))]
            target = md_names or txt_names or html_names or json_names or names
            for name in target:
                if name.endswith("/"):
                    continue
                try:
                    raw = zf.read(name)
                    out_parts.append(raw.decode("utf-8", errors="replace"))
                except Exception as e:
                    log.warning("could not decode %s in zip: %s", name, e)
    except zipfile.BadZipFile:
        return zip_bytes.decode("utf-8", errors="replace")
    return "\n\n".join(out_parts)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def clean_ocr_text(raw_text: Optional[str]) -> str:
    """Strip HTML/table artifacts and collapse whitespace.

    Sarvam's `md` output sometimes embeds raw HTML tables and <br/> tags.
    Aggressively strip everything tag-shaped before parsing.
    """
    if not raw_text:
        return ""
    text = unescape(raw_text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Voter card parsing
# ---------------------------------------------------------------------------

# Voter ID formats observed in ECI rolls
#   XFF3617750, FPP6422620, UP/20/100/0000168
_VOTER_ID_RE = re.compile(r"\b([A-Z]{3}\d{6,10}|UP/\d+/\d+/\d+)\b")

_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

def _to_ascii_digits(s: str) -> str:
    return s.translate(_DEV_DIGITS)


_FIELD_NAME_RE = re.compile(
    r"नाम\s*[:：]\s*(.+?)\s*"
    r"(?=पिता|पति|माता|पुत्र|पुत्री|भाई|बहन|अन्य|मकान|आयु|लिंग|फोटो|$)"
)
_FIELD_RELATION_RE = re.compile(
    r"(पिता|पति|माता|पुत्र|पुत्री|भाई|बहन|अन्य)\s*(?:का\s*नाम)?\s*[:：]\s*(.+?)\s*"
    r"(?=मकान|आयु|लिंग|फोटो|$)"
)
_FIELD_HOUSE_RE = re.compile(
    r"मकान\s*(?:संख्या|न(?:ं|\.)?)?\s*[:：]\s*(.+?)\s*"
    r"(?=आयु|लिंग|फोटो|पिता|पति|माता|$)"
)
_FIELD_AGE_RE = re.compile(r"आयु\s*[:：]\s*([०-९0-9]{1,3})")
_FIELD_GENDER_RE = re.compile(r"लिंग\s*[:：]\s*(\S+)")

_GENDER_MAP = (
    ("पुरुष", "Male"),
    ("महिला", "Female"),
    ("स्त्री", "Female"),
    ("थर्ड", "Other"),
    ("अन्य", "Other"),
)


def _normalize_gender(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    for needle, label in _GENDER_MAP:
        if needle in raw:
            return label
    return None


def _trim(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().strip("।").strip("|").strip(":：").strip()
    # Strip trailing punctuation/dashes that leak from layout
    v = re.sub(r"[|:\-–—]+$", "", v).strip()
    return v or None


_VALID_RELATIONS = {"पिता", "पति", "माता", "पुत्र", "पुत्री", "भाई", "बहन", "अन्य"}


def _clean_relation(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    # Defensive: strip trailing "का" if Sarvam's output omitted "नाम"
    cleaned = re.sub(r"\s*का\s*$", "", raw).strip()
    return cleaned if cleaned in _VALID_RELATIONS else None


def parse_voter_card(voter_id: str, pre_text: str, post_text: str) -> dict:
    """Parse one voter card into structured fields.

    Each card on an ECI roll has its voter ID in the header. Content before
    the ID holds the serial number; content after holds the name/relation/
    house/age/gender block. Parsing each side independently prevents the
    next card's "नाम" from leaking in.
    """
    pre = clean_ocr_text(pre_text)
    post = clean_ocr_text(post_text)

    # Serial number: rightmost short int in pre-VID text
    serial_no: Optional[int] = None
    pre_ascii = _to_ascii_digits(pre)
    candidates = re.findall(r"\b(\d{1,4})\b", pre_ascii)
    if candidates:
        try:
            serial_no = int(candidates[-1])
        except ValueError:
            serial_no = None

    name_match = _FIELD_NAME_RE.search(post)
    name = _trim(name_match.group(1)) if name_match else None

    relation_match = _FIELD_RELATION_RE.search(post)
    relation_type = _clean_relation(relation_match.group(1)) if relation_match else None
    relative_name = _trim(relation_match.group(2)) if relation_match else None

    house_match = _FIELD_HOUSE_RE.search(post)
    house_no = _trim(house_match.group(1)) if house_match else None

    age: Optional[int] = None
    age_match = _FIELD_AGE_RE.search(post)
    if age_match:
        try:
            parsed = int(_to_ascii_digits(age_match.group(1)))
            # Sanity: voters are 18-120
            if 18 <= parsed <= 120:
                age = parsed
        except ValueError:
            age = None

    gender_match = _FIELD_GENDER_RE.search(post)
    gender = _normalize_gender(gender_match.group(1)) if gender_match else None

    return {
        "serial_no": serial_no,
        "voter_id": voter_id,
        "name": name,
        "relation_type": relation_type,
        "relative_name": relative_name,
        "house_no": house_no,
        "age": age,
        "gender": gender,
    }


_RELATION_KA_RE = re.compile(
    r"(पिता|पति|माता|पुत्र|पुत्री|भाई|बहन|अन्य)\s*का\s*$"
)


def parse_voters_from_markdown(
    markdown: str,
    chunk_page_start: int = 1,
) -> list[dict]:
    """Parse voter records from Sarvam-produced markdown.

    Anchors on the voter's own "नाम :" field (one per card), excluding the
    "X का नाम :" relation field. This is more reliable than voter-ID
    anchoring because Sarvam OCR sometimes emits the same voter ID multiple
    times in HTML table output, but every card has exactly one own-name
    field. Voter ID and serial are recovered from the text immediately
    preceding each anchor (where they sit in the card layout).
    """
    if not markdown:
        return []

    # Clean and unescape once; all subsequent offsets are in `text`.
    text = clean_ocr_text(markdown)

    # Find voter-name-field anchors. A "नाम :" preceded by "<RELATION> का"
    # is the relative's name field — skip it.
    anchors: list[int] = []
    for m in re.finditer(r"नाम\s*[:：]", text):
        before = text[max(0, m.start() - 25):m.start()]
        if _RELATION_KA_RE.search(before):
            continue
        anchors.append(m.start())

    if not anchors:
        return []

    voters: list[dict] = []
    for i, anchor in enumerate(anchors):
        end = anchors[i + 1] if i + 1 < len(anchors) else len(text)
        # Body starts at "नाम :" — contains this card's name, relation,
        # house, age, gender, then the next card's serial + VID before the
        # next anchor. We slice up to the next anchor and parse the body
        # for fields; VID/serial come from the *previous* slice's tail.
        body = text[anchor:end]

        prev = anchors[i - 1] if i > 0 else 0
        pre_window = text[prev:anchor]

        # Voter ID — last VID in the pre-window (closest to this anchor).
        voter_id: Optional[str] = None
        vid_iter = list(_VOTER_ID_RE.finditer(pre_window))
        if vid_iter:
            voter_id = vid_iter[-1].group(0)
            vid_pos = vid_iter[-1].start()
            serial_search_text = pre_window[max(0, vid_pos - 60):vid_pos]
        else:
            serial_search_text = pre_window[-200:]

        # Serial number — rightmost short int near the VID.
        serial_no: Optional[int] = None
        ascii_text = _to_ascii_digits(serial_search_text)
        cands = re.findall(r"\b(\d{1,4})\b", ascii_text)
        if cands:
            try:
                serial_no = int(cands[-1])
            except ValueError:
                serial_no = None

        # Parse fields from this card's body.
        name_match = _FIELD_NAME_RE.search(body)
        name = _trim(name_match.group(1)) if name_match else None

        relation_match = _FIELD_RELATION_RE.search(body)
        relation_type = _clean_relation(relation_match.group(1)) if relation_match else None
        relative_name = _trim(relation_match.group(2)) if relation_match else None

        house_match = _FIELD_HOUSE_RE.search(body)
        house_no = _trim(house_match.group(1)) if house_match else None

        age: Optional[int] = None
        age_match = _FIELD_AGE_RE.search(body)
        if age_match:
            try:
                parsed = int(_to_ascii_digits(age_match.group(1)))
                if 18 <= parsed <= 120:
                    age = parsed
            except ValueError:
                age = None

        gender_match = _FIELD_GENDER_RE.search(body)
        gender = _normalize_gender(gender_match.group(1)) if gender_match else None

        # Page tracking: 30 cards per page on ECI rolls (3x10 grid).
        page_no = chunk_page_start + (i // 30)

        voters.append({
            "serial_no": serial_no,
            "voter_id": voter_id,
            "name": name,
            "relation_type": relation_type,
            "relative_name": relative_name,
            "house_no": house_no,
            "age": age,
            "gender": gender,
            "page_no": page_no,
        })

    return voters


# ---------------------------------------------------------------------------
# Page offset heuristics (best-effort — Sarvam doesn't always emit markers)
# ---------------------------------------------------------------------------

_PAGE_BREAK_RE = re.compile(
    r"(?:^|\n)\s*(?:#+\s*Page\s*\d+|---+\s*\n|\f)", re.IGNORECASE
)


def _build_page_offsets(text: str) -> list[int]:
    offsets = [0]
    for m in _PAGE_BREAK_RE.finditer(text):
        offsets.append(m.start())
    return offsets


def _page_for_offset(
    offset: int,
    page_offsets: list[int],
    chunk_page_start: int,
) -> Optional[int]:
    if offset < 0 or len(page_offsets) <= 1:
        return chunk_page_start
    for i, o in enumerate(page_offsets):
        if offset < o:
            return chunk_page_start + max(0, i - 1)
    return chunk_page_start + max(0, len(page_offsets) - 1)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_voters(voters: list[dict]) -> tuple[list[dict], list[dict]]:
    """Collapse duplicates while preserving distinct voters that share an ID.

    Sarvam OCR sometimes emits the same voter ID multiple times within a
    chunk's markdown (a table-rendering quirk). Deduping by voter_id alone
    discards real voters that happened to be paired with an OCR-repeated
    ID. We dedup by (voter_id, name) instead — same person collapses; same
    ID with different name is kept (the ID assignment may be wrong, but
    the voter is real).

    Records lacking voter_id or name are dropped as noise.
    Returns (unique_voters, dropped_records) for audit.
    """
    seen: dict[tuple[str, str], dict] = {}
    dropped: list[dict] = []
    for v in voters:
        vid = v.get("voter_id")
        name = v.get("name")
        if not vid or not name:
            dropped.append(v)
            continue
        key = (vid, name)
        if key in seen:
            existing = seen[key]
            existing_score = sum(1 for x in existing.values() if x not in (None, ""))
            new_score = sum(1 for x in v.values() if x not in (None, ""))
            if new_score > existing_score:
                dropped.append(existing)
                seen[key] = v
            else:
                dropped.append(v)
        else:
            seen[key] = v
    return list(seen.values()), dropped


# ---------------------------------------------------------------------------
# Booth metadata (page 1 of every ECI roll)
# ---------------------------------------------------------------------------

def extract_booth_metadata(markdown: str) -> dict:
    """Parse page-1 metadata grid from chunk-0 markdown.

    Page 1 of every ECI roll declares:
      - Constituency (विधानसभा निर्वाचन क्षेत्र)
      - Booth number (भाग संख्या)
      - Booth name + address (मतदान स्थल)
      - Pin code
      - Voter totals (पुरुष | महिला | तृतीय लिंग | कुल)

    The totals are the ground truth we validate against.
    """
    if not markdown:
        return _empty_metadata()

    cleaned = clean_ocr_text(markdown)
    cleaned_ascii = _to_ascii_digits(cleaned)

    metadata = _empty_metadata()

    m = re.search(
        r"विधानसभा\s*निर्वाचन\s*क्षेत्र[^:]*?:\s*([^|<]+?)\s*"
        r"(?=भाग|संसदीय|पुनरीक्षण|$)",
        cleaned,
    )
    if m:
        metadata["constituency"] = m.group(1).strip().rstrip("|").strip()

    m = re.search(r"भाग\s*संख्या\s*:?\s*:?\s*(\d{1,4})", cleaned_ascii)
    if m:
        metadata["booth_number"] = m.group(1)

    m = re.search(
        r"मतदान\s*स्थल\s*की\s*संख्या\s*और\s*नाम\s*:?\s*([^|]+?)\s*"
        r"(?=मतदान\s*स्थल\s*का\s*पता|मतदान\s*स्थल\s*के\s*प्रकार|इस\s*भाग|$)",
        cleaned,
    )
    if m:
        metadata["booth_name"] = m.group(1).strip().rstrip("|").strip()

    m = re.search(r"पिन\s*कोड\s*:?\s*(\d{6})", cleaned_ascii)
    if m:
        metadata["pin_code"] = m.group(1)

    # Voter totals row. The page-1 table has a header row
    #     पुरुष | महिला | तृतीय लिंग | कुल
    # followed by a data row that may include preceding columns like
    # प्रारम्भिक क्रम / अंतिम क्रम before the four counts. Find the four
    # values using the structural invariant: male + female + third == total.
    header_match = re.search(
        r"पुरुष[^\d]*महिला[^\d]*तृतीय[^\d]*कुल", cleaned_ascii
    )
    if header_match:
        tail = cleaned_ascii[header_match.end():header_match.end() + 600]
        nums = [int(x) for x in re.findall(r"\b\d{1,7}\b", tail)]
        for i in range(len(nums) - 3):
            m_, f_, t_, total_ = nums[i:i + 4]
            if total_ > 0 and m_ + f_ + t_ == total_:
                metadata["expected_male"] = m_
                metadata["expected_female"] = f_
                metadata["expected_third"] = t_
                metadata["expected_voter_count"] = total_
                break

    return metadata


def _empty_metadata() -> dict:
    return {
        "constituency": None,
        "booth_number": None,
        "booth_name": None,
        "pin_code": None,
        "expected_male": 0,
        "expected_female": 0,
        "expected_third": 0,
        "expected_voter_count": 0,
    }


# ---------------------------------------------------------------------------
# Validation against page-1 ground truth
# ---------------------------------------------------------------------------

def validate_extraction(voters: list[dict], metadata: dict) -> dict:
    """Compare extracted voters against the page-1 declared totals.

    Returns a structured report — total counts, gender splits, missing
    serial numbers, duplicate serials, and an OK / WARN / MISMATCH status.
    """
    expected = metadata.get("expected_voter_count", 0) or 0
    actual = len(voters)

    male = sum(1 for v in voters if v.get("gender") == "Male")
    female = sum(1 for v in voters if v.get("gender") == "Female")
    other = sum(1 for v in voters if v.get("gender") == "Other")

    serials_raw = [v.get("serial_no") for v in voters if v.get("serial_no")]
    serial_counts: dict[int, int] = {}
    for s in serials_raw:
        serial_counts[s] = serial_counts.get(s, 0) + 1

    if expected > 0:
        missing = sorted(set(range(1, expected + 1)) - set(serials_raw))
    else:
        missing = []
    duplicate_serials = sorted(s for s, c in serial_counts.items() if c > 1)

    # Duplicate voter_ids surface OCR mis-assignments (same ID paired with
    # different names). User should audit these manually.
    vid_counts: dict[str, int] = {}
    for v in voters:
        vid = v.get("voter_id")
        if vid:
            vid_counts[vid] = vid_counts.get(vid, 0) + 1
    duplicate_voter_ids = sorted(vid for vid, c in vid_counts.items() if c > 1)

    diff = actual - expected
    if expected <= 0:
        status = "UNKNOWN"
    elif actual == expected:
        status = "OK"
    elif abs(diff) <= max(5, expected * 0.05):
        status = "WARN"
    else:
        status = "MISMATCH"

    return {
        "expected_total": expected,
        "extracted_total": actual,
        "difference": diff,
        "match": actual == expected,
        "expected_male": metadata.get("expected_male", 0) or 0,
        "extracted_male": male,
        "expected_female": metadata.get("expected_female", 0) or 0,
        "extracted_female": female,
        "extracted_other": other,
        "missing_serial_numbers": missing[:50],
        "missing_serial_count": len(missing),
        "duplicate_serials": duplicate_serials[:50],
        "duplicate_serial_count": len(duplicate_serials),
        "duplicate_voter_ids": duplicate_voter_ids[:50],
        "duplicate_voter_id_count": len(duplicate_voter_ids),
        "status": status,
    }
