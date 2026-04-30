"""Background job manager with SQLite-backed checkpointing.

A single PDF is broken into chunks (≤10 pages each, Sarvam limit). Each chunk
has its own row in `job_chunks` so a crash mid-processing can resume. Once all
chunks are done, voter records are aggregated and the final JSON/Excel are
written to disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, SQLModel, create_engine, select

from . import config, pdf_processor, output_generator
from .models import JobChunk, JobRecord, JobStatus, ValidationStatus
from .pipeline import process_pdf as langchain_process_pdf, save_outputs as langchain_save_outputs
from .sarvam_client import SarvamClient, SarvamError

log = logging.getLogger(__name__)

_engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
)
SQLModel.metadata.create_all(_engine)


def _migrate_schema() -> None:
    """ALTER TABLE for any new columns missing in an existing DB.

    SQLModel.metadata.create_all only creates tables that don't exist; it
    won't add columns to an already-present table. Handle that explicitly.
    """
    additions = {
        "jobs": [
            ("expected_voter_count", "INTEGER DEFAULT 0"),
            ("expected_male", "INTEGER DEFAULT 0"),
            ("expected_female", "INTEGER DEFAULT 0"),
            ("validation_status", "VARCHAR DEFAULT 'PENDING'"),
            ("metadata_json", "TEXT"),
            ("validation_json", "TEXT"),
        ],
    }
    with _engine.connect() as conn:
        for table, cols in additions.items():
            result = conn.exec_driver_sql(f"PRAGMA table_info({table})")
            existing = {row[1] for row in result}
            for col, ddl in cols:
                if col not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"
                    )
        conn.commit()


_migrate_schema()


class JobManager:
    def __init__(self):
        self._cancel_flags: dict[str, bool] = {}
        self._listeners: dict[str, set[asyncio.Queue]] = {}
        self._pdf_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PDFS)
        self._chunk_semaphore = asyncio.Semaphore(
            config.MAX_CONCURRENT_PDFS * config.MAX_CONCURRENT_CHUNKS_PER_PDF
        )
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Listener pub/sub for WebSocket
    # ------------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        if job_id in self._listeners:
            self._listeners[job_id].discard(q)

    def _broadcast(self, job_id: str, payload: dict) -> None:
        for q in list(self._listeners.get(job_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    def _session(self) -> Session:
        return Session(_engine)

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._session() as s:
            return s.get(JobRecord, job_id)

    def list_jobs(self) -> list[JobRecord]:
        with self._session() as s:
            return list(s.exec(select(JobRecord).order_by(JobRecord.created_at.desc())).all())

    def list_chunks(self, job_id: str) -> list[JobChunk]:
        with self._session() as s:
            stmt = select(JobChunk).where(JobChunk.job_id == job_id).order_by(JobChunk.chunk_index)
            return list(s.exec(stmt).all())

    def _save_job(self, job: JobRecord) -> None:
        with self._session() as s:
            s.merge(job)
            s.commit()

    def _save_chunk(self, chunk: JobChunk) -> None:
        with self._session() as s:
            chunk.updated_at = datetime.utcnow()
            s.merge(chunk)
            s.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create_job(self, pdf_name: str, pdf_path: Path) -> JobRecord:
        job_id = str(uuid.uuid4())
        try:
            total_pages = pdf_processor.get_page_count(pdf_path)
        except Exception as e:
            log.exception("could not read PDF %s", pdf_path)
            total_pages = 0

        job = JobRecord(
            job_id=job_id,
            pdf_name=pdf_name,
            pdf_path=str(pdf_path),
            total_pages=total_pages,
            status=JobStatus.QUEUED,
        )
        self._save_job(job)

        # Pre-create chunks so list_chunks works even before run_job starts
        if total_pages > 0:
            n = config.SARVAM_PAGES_PER_CHUNK
            for i, start in enumerate(range(1, total_pages + 1, n)):
                end = min(start + n - 1, total_pages)
                self._save_chunk(JobChunk(
                    job_id=job_id, chunk_index=i,
                    page_start=start, page_end=end,
                    status=JobStatus.QUEUED,
                ))
        return job

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        self._cancel_flags[job_id] = True
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            job.status = JobStatus.CANCELLED
            self._save_job(job)
            self._broadcast(job_id, self._snapshot(job))
        return True

    def delete_job(self, job_id: str) -> bool:
        self.cancel_job(job_id)
        with self._session() as s:
            job = s.get(JobRecord, job_id)
            if not job:
                return False
            chunks = list(s.exec(select(JobChunk).where(JobChunk.job_id == job_id)).all())
            for c in chunks:
                s.delete(c)
            s.delete(job)
            s.commit()
        # Best-effort filesystem cleanup
        try:
            if job and job.pdf_path:
                p = Path(job.pdf_path)
                if p.exists():
                    p.unlink()
        except Exception:
            pass
        return True

    def start_job(self, job_id: str) -> None:
        """Kick off the background task."""
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run_job(job_id))

    async def resume_pending(self) -> None:
        """On startup, restart any unfinished jobs."""
        with self._session() as s:
            pending = list(s.exec(
                select(JobRecord).where(
                    JobRecord.status.in_([JobStatus.QUEUED, JobStatus.PROCESSING])
                )
            ).all())
        for job in pending:
            log.info("resuming job %s (%s)", job.job_id, job.pdf_name)
            self.start_job(job.job_id)

    # ------------------------------------------------------------------
    # Snapshot for API responses + broadcasts
    # ------------------------------------------------------------------
    def _snapshot(self, job: JobRecord) -> dict:
        return {
            "job_id": job.job_id,
            "pdf_name": job.pdf_name,
            "total_pages": job.total_pages,
            "processed_pages": job.processed_pages,
            "total_voters": job.total_voters,
            "progress_pct": job.progress_pct,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "cost_estimate": round(job.cost_estimate, 2),
            "error": job.error,
            "expected_voter_count": job.expected_voter_count,
            "expected_male": job.expected_male,
            "expected_female": job.expected_female,
            "validation_status": job.validation_status,
            "has_output": bool(job.output_json) and Path(job.output_json or "").exists(),
        }

    # ------------------------------------------------------------------
    # Core processing loop
    # ------------------------------------------------------------------
    async def _run_job(self, job_id: str) -> None:
        async with self._pdf_semaphore:
            await self._run_job_inner(job_id)

    async def _run_job_inner(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            log.warning("run_job: %s not found", job_id)
            return
        if self._cancel_flags.get(job_id):
            return
        try:
            job.status = JobStatus.PROCESSING
            if not job.started_at:
                job.started_at = datetime.utcnow()
            self._save_job(job)
            self._broadcast(job_id, self._snapshot(job))

            if config.LLM_PROVIDER == "sarvam":
                await self._run_legacy_sarvam(job_id)
            else:
                await self._run_langchain(job_id)

        except Exception as e:
            log.exception("job %s failed", job_id)
            job = self.get_job(job_id) or job
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.utcnow()
            self._save_job(job)
            self._broadcast(job_id, self._snapshot(job))

    # ------------------------------------------------------------------
    # Provider routing
    # ------------------------------------------------------------------
    async def _run_legacy_sarvam(self, job_id: str) -> None:
        """Original Sarvam Document Intelligence pipeline (chunked async OCR)."""
        job = self.get_job(job_id)
        if not job:
            return

        chunks_dir = config.UPLOAD_DIR / f"{job_id}_chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        existing_chunks = self.list_chunks(job_id)
        if not existing_chunks:
            pdf_path = Path(job.pdf_path)
            total = pdf_processor.get_page_count(pdf_path)
            n = config.SARVAM_PAGES_PER_CHUNK
            for i, start in enumerate(range(1, total + 1, n)):
                end = min(start + n - 1, total)
                self._save_chunk(JobChunk(
                    job_id=job_id, chunk_index=i,
                    page_start=start, page_end=end,
                    status=JobStatus.QUEUED,
                ))
            existing_chunks = self.list_chunks(job_id)

        split_paths = self._ensure_chunks_split(job, existing_chunks, chunks_dir)

        chunk_sem = asyncio.Semaphore(config.MAX_CONCURRENT_CHUNKS_PER_PDF)
        tasks = []
        async with SarvamClient() as client:
            for chunk in existing_chunks:
                if chunk.status == JobStatus.COMPLETED:
                    continue
                if self._cancel_flags.get(job_id):
                    break
                tasks.append(asyncio.create_task(
                    self._process_chunk(client, chunk_sem, job_id, chunk,
                                        split_paths[chunk.chunk_index])
                ))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        if self._cancel_flags.get(job_id):
            job = self.get_job(job_id)
            if job:
                job.status = JobStatus.CANCELLED
                self._save_job(job)
                self._broadcast(job_id, self._snapshot(job))
            return

        await self._finalize(job_id)

    async def _run_langchain(self, job_id: str) -> None:
        """LangChain-based pipeline — Gemini / Claude / GPT-4 vision LLMs."""
        job = self.get_job(job_id)
        if not job:
            return

        async def progress_cb(**kwargs):
            if self._cancel_flags.get(job_id):
                return
            current = self.get_job(job_id)
            if not current:
                return
            stage = kwargs.get("stage")
            if stage == "metadata_done":
                meta = kwargs.get("metadata") or {}
                current.metadata_json = json.dumps(meta, ensure_ascii=False)
                current.expected_voter_count = int(meta.get("expected_voter_count") or 0)
                current.expected_male = int(meta.get("expected_male") or 0)
                current.expected_female = int(meta.get("expected_female") or 0)
            processed = kwargs.get("processed_pages") or current.processed_pages
            total = kwargs.get("total_pages") or current.total_pages or 1
            current.processed_pages = processed
            current.progress_pct = round((processed / max(1, total)) * 100, 2)
            current.cost_estimate = round(processed * config.COST_PER_PAGE_INR, 2)
            self._save_job(current)
            self._broadcast(job_id, self._snapshot(current))

        try:
            output = await langchain_process_pdf(job.pdf_path, progress_callback=progress_cb)
        except Exception as e:
            log.exception("LangChain pipeline failed for %s", job.pdf_name)
            current = self.get_job(job_id) or job
            current.status = JobStatus.FAILED
            current.error = f"langchain pipeline: {e}"
            current.completed_at = datetime.utcnow()
            self._save_job(current)
            self._broadcast(job_id, self._snapshot(current))
            return

        if self._cancel_flags.get(job_id):
            current = self.get_job(job_id)
            if current:
                current.status = JobStatus.CANCELLED
                self._save_job(current)
                self._broadcast(job_id, self._snapshot(current))
            return

        json_path, xlsx_path, summary_path = langchain_save_outputs(output)

        current = self.get_job(job_id) or job
        current.output_json = str(json_path)
        current.output_xlsx = str(xlsx_path)
        current.output_summary = str(summary_path)
        current.total_voters = len(output.voters)
        current.processed_pages = output.total_pages
        current.progress_pct = 100.0
        # Real cost from token-counted log (cost_tracker), not heuristic.
        if output.routing_stats and output.routing_stats.cost_inr > 0:
            current.cost_estimate = round(output.routing_stats.cost_inr, 2)
        else:
            current.cost_estimate = round(output.total_pages * config.COST_PER_PAGE_INR, 2)
        current.completed_at = datetime.utcnow()
        current.metadata_json = json.dumps(output.metadata.model_dump(), ensure_ascii=False)
        current.validation_json = json.dumps(output.validation.model_dump(), ensure_ascii=False)
        current.expected_voter_count = output.metadata.expected_voter_count
        current.expected_male = output.metadata.expected_male
        current.expected_female = output.metadata.expected_female
        current.validation_status = output.validation.status
        current.status = JobStatus.COMPLETED
        current.error = None
        self._save_job(current)
        self._broadcast(job_id, self._snapshot(current))

    def _ensure_chunks_split(
        self,
        job: JobRecord,
        chunks: list[JobChunk],
        chunks_dir: Path,
    ) -> dict[int, Path]:
        """Return chunk_index → chunk_pdf_path, splitting if needed."""
        stem = Path(job.pdf_name).stem
        out: dict[int, Path] = {}
        need_split = False
        for c in chunks:
            p = chunks_dir / f"{stem}_p{c.page_start:03d}-{c.page_end:03d}.pdf"
            out[c.chunk_index] = p
            if c.status != JobStatus.COMPLETED and not p.exists():
                need_split = True
        if need_split:
            pdf_path = Path(job.pdf_path)
            split_results = pdf_processor.split_pdf(pdf_path, chunks_dir,
                                                     config.SARVAM_PAGES_PER_CHUNK)
            for i, (path, _, _) in enumerate(split_results):
                out[i] = path
        return out

    async def _process_chunk(
        self,
        client: SarvamClient,
        sem: asyncio.Semaphore,
        job_id: str,
        chunk: JobChunk,
        chunk_pdf: Path,
    ) -> None:
        async with sem:
            if self._cancel_flags.get(job_id):
                return
            chunk.status = JobStatus.PROCESSING
            self._save_chunk(chunk)
            try:
                cancel_check = lambda: self._cancel_flags.get(job_id, False)
                sarvam_id, status, zip_bytes = await client.process_pdf_chunk(
                    chunk_pdf, cancel_check=cancel_check
                )
                chunk.sarvam_job_id = sarvam_id
                markdown = pdf_processor.extract_markdown_from_zip(zip_bytes)
                voters = pdf_processor.parse_voters_from_markdown(
                    markdown, chunk_page_start=chunk.page_start
                )
                # Page 1 is in chunk 0 — parse booth metadata once for the job.
                if chunk.chunk_index == 0 and chunk.page_start == 1:
                    metadata = pdf_processor.extract_booth_metadata(markdown)
                    self._save_metadata(job_id, metadata)
                chunk.voters_json = json.dumps(voters, ensure_ascii=False)
                chunk.status = JobStatus.COMPLETED
                chunk.error = None
                self._save_chunk(chunk)
                self._update_progress(job_id)
            except Exception as e:
                log.exception("chunk %d (job %s) failed", chunk.chunk_index, job_id)
                chunk.status = JobStatus.FAILED
                chunk.error = str(e)
                self._save_chunk(chunk)
                self._update_progress(job_id)

    def _save_metadata(self, job_id: str, metadata: dict) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        job.expected_voter_count = int(metadata.get("expected_voter_count") or 0)
        job.expected_male = int(metadata.get("expected_male") or 0)
        job.expected_female = int(metadata.get("expected_female") or 0)
        self._save_job(job)
        self._broadcast(job_id, self._snapshot(job))

    def _update_progress(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        chunks = self.list_chunks(job_id)
        processed_pages = 0
        voters_total = 0
        for c in chunks:
            if c.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                processed_pages += (c.page_end - c.page_start + 1)
            if c.voters_json:
                try:
                    voters_total += len(json.loads(c.voters_json))
                except json.JSONDecodeError:
                    pass
        job.processed_pages = processed_pages
        job.total_voters = voters_total
        # Religion classification removed from pipeline; counts stay at 0.
        job.hindu_count = 0
        job.muslim_count = 0
        job.sikh_count = 0
        job.unknown_count = 0
        job.progress_pct = round(
            (processed_pages / max(1, job.total_pages)) * 100, 2
        )
        job.cost_estimate = round(processed_pages * config.COST_PER_PAGE_INR, 2)
        self._save_job(job)
        self._broadcast(job_id, self._snapshot(job))

    async def _finalize(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        chunks = self.list_chunks(job_id)
        all_voters: list[dict] = []
        any_failed = False
        for c in chunks:
            if c.status == JobStatus.FAILED:
                any_failed = True
                continue
            if c.voters_json:
                try:
                    all_voters.extend(json.loads(c.voters_json))
                except json.JSONDecodeError:
                    any_failed = True

        # Deduplicate by voter_id and drop noise (no voter_id or no name).
        all_voters, dropped = pdf_processor.deduplicate_voters(all_voters)

        # Sort by serial number for clean output (fallback to page).
        all_voters.sort(key=lambda v: (
            v.get("serial_no") if v.get("serial_no") is not None else 1_000_000,
            v.get("page_no") or 0,
        ))

        # Validate against page-1 declared totals.
        metadata = json.loads(job.metadata_json) if job.metadata_json else {}
        validation = pdf_processor.validate_extraction(all_voters, metadata)
        validation["dropped_count"] = len(dropped)
        job.validation_json = json.dumps(validation, ensure_ascii=False)
        job.validation_status = validation.get("status", ValidationStatus.UNKNOWN)

        json_path, xlsx_path, summary_path = output_generator.write_outputs(
            pdf_name=job.pdf_name,
            voters=all_voters,
            total_pages=job.total_pages,
            cost_estimate=job.cost_estimate,
            metadata=metadata,
            validation=validation,
            dropped=dropped,
        )
        job.output_json = str(json_path)
        job.output_xlsx = str(xlsx_path)
        job.output_summary = str(summary_path)
        job.total_voters = len(all_voters)
        job.processed_pages = job.total_pages
        job.progress_pct = 100.0
        job.completed_at = datetime.utcnow()
        if any_failed and any(c.status == JobStatus.COMPLETED for c in chunks):
            job.status = JobStatus.COMPLETED
            job.error = "some chunks failed - partial output"
        elif any_failed:
            job.status = JobStatus.FAILED
            job.error = "all chunks failed"
        else:
            job.status = JobStatus.COMPLETED

        # Religion classification removed from pipeline; counts stay at 0.
        job.hindu_count = 0
        job.muslim_count = 0
        job.sikh_count = 0
        job.unknown_count = 0
        self._save_job(job)
        self._broadcast(job_id, self._snapshot(job))

        # Cleanup chunk PDFs (keep originals)
        chunks_dir = config.UPLOAD_DIR / f"{job_id}_chunks"
        if chunks_dir.exists():
            shutil.rmtree(chunks_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Aggregate stats
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        with self._session() as s:
            jobs = list(s.exec(select(JobRecord)).all())
        total = len(jobs)
        completed = sum(1 for j in jobs if j.status == JobStatus.COMPLETED)
        processing = sum(1 for j in jobs if j.status == JobStatus.PROCESSING)
        failed = sum(1 for j in jobs if j.status == JobStatus.FAILED)
        ok = sum(1 for j in jobs if j.validation_status == ValidationStatus.OK)
        warn = sum(
            1 for j in jobs
            if j.validation_status in (ValidationStatus.WARN, ValidationStatus.MINOR_MISMATCH)
        )
        mismatch = sum(
            1 for j in jobs
            if j.validation_status in (
                ValidationStatus.MISMATCH,
                ValidationStatus.MAJOR_MISMATCH,
                ValidationStatus.FAILED,
            )
        )
        return {
            "total_jobs": total,
            "completed_jobs": completed,
            "processing_jobs": processing,
            "failed_jobs": failed,
            "total_pages": sum(j.processed_pages for j in jobs),
            "total_voters": sum(j.total_voters for j in jobs),
            "expected_total": sum(j.expected_voter_count for j in jobs),
            "validation_ok": ok,
            "validation_warn": warn,
            "validation_mismatch": mismatch,
            "total_cost": round(sum(j.cost_estimate for j in jobs), 2),
        }


_manager: Optional[JobManager] = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
