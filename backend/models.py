from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class Voter(BaseModel):
    serial_no: Optional[int] = None
    voter_id: Optional[str] = None
    name: Optional[str] = None
    relation_type: Optional[str] = None
    relative_name: Optional[str] = None
    house_no: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    page_no: Optional[int] = None


class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ValidationStatus:
    PENDING = "PENDING"
    OK = "OK"
    WARN = "WARN"
    MINOR_MISMATCH = "MINOR_MISMATCH"
    MAJOR_MISMATCH = "MAJOR_MISMATCH"
    MISMATCH = "MISMATCH"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class JobRecord(SQLModel, table=True):
    __tablename__ = "jobs"

    job_id: str = Field(primary_key=True)
    pdf_name: str
    pdf_path: str
    total_pages: int = 0
    processed_pages: int = 0
    total_voters: int = 0
    progress_pct: float = 0.0
    status: str = JobStatus.QUEUED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cost_estimate: float = 0.0
    error: Optional[str] = None
    # Religion columns retained for back-compat with existing DB rows; unused.
    hindu_count: int = 0
    muslim_count: int = 0
    sikh_count: int = 0
    unknown_count: int = 0
    output_json: Optional[str] = None
    output_xlsx: Optional[str] = None
    output_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Booth metadata + validation against page-1 ground truth.
    expected_voter_count: int = 0
    expected_male: int = 0
    expected_female: int = 0
    validation_status: str = ValidationStatus.PENDING
    metadata_json: Optional[str] = None
    validation_json: Optional[str] = None


class JobChunk(SQLModel, table=True):
    """Tracks each Sarvam sub-job for a PDF chunk so we can resume after crash."""
    __tablename__ = "job_chunks"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    chunk_index: int
    page_start: int
    page_end: int
    sarvam_job_id: Optional[str] = None
    status: str = JobStatus.QUEUED
    voters_json: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
