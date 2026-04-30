"""FastAPI app — Voter Roll OCR + Religion Classifier."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import (
    BackgroundTasks, FastAPI, File, HTTPException, UploadFile, WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .job_manager import get_manager
from .pipeline.cost_tracker import (
    COST_LOG_PATH,
    get_global_cost_summary,
    get_pdf_total_cost,
)
import json as _json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("voter-ocr")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = get_manager()
    await manager.resume_pending()
    yield


app = FastAPI(title="Voter Roll OCR + Religion Classifier", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def _safe_name(name: str) -> str:
    name = Path(name).name  # strip directories
    name = _FILENAME_SAFE.sub("_", name)
    return name or "upload.pdf"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "no files uploaded")

    manager = get_manager()
    created = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"only PDFs allowed (got {f.filename})")
        safe = _safe_name(f.filename)
        target = config.UPLOAD_DIR / f"{Path(safe).stem}_{int(asyncio.get_event_loop().time()*1000)}.pdf"
        size = 0
        with open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.MAX_UPLOAD_SIZE_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(400, f"file {safe} exceeds {config.MAX_UPLOAD_SIZE_MB}MB")
                out.write(chunk)
        job = manager.create_job(pdf_name=safe, pdf_path=target)
        manager.start_job(job.job_id)
        created.append({
            "job_id": job.job_id,
            "pdf_name": job.pdf_name,
            "total_pages": job.total_pages,
            "status": job.status,
        })

    return {"jobs": created, "count": len(created)}


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
async def list_jobs():
    manager = get_manager()
    jobs = manager.list_jobs()
    return {"jobs": [manager._snapshot(j) for j in jobs]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    manager = get_manager()
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return manager._snapshot(job)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    manager = get_manager()
    if not manager.delete_job(job_id):
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    manager = get_manager()
    if not manager.cancel_job(job_id):
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/result")
async def get_result(job_id: str, format: str = "json"):
    manager = get_manager()
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if format == "json":
        path = job.output_json
        media = "application/json"
    elif format in ("xlsx", "excel"):
        path = job.output_xlsx
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "summary":
        path = job.output_summary
        media = "application/json"
    else:
        raise HTTPException(400, "format must be json | xlsx | summary")
    if not path or not Path(path).exists():
        raise HTTPException(404, "output not yet ready")
    return FileResponse(
        path,
        media_type=media,
        filename=Path(path).name,
    )


@app.get("/api/stats")
async def stats():
    return get_manager().stats()


@app.get("/api/cost/debug/{pdf_name}")
async def cost_debug(pdf_name: str):
    """Per-PDF cost breakdown with raw log entries. Compare against Google Cloud
    Console billing for the day to verify accuracy."""
    pdf_stem = pdf_name.replace(".pdf", "")
    summary = get_pdf_total_cost(pdf_stem)
    entries: list[dict] = []
    if COST_LOG_PATH.exists():
        with open(COST_LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = _json.loads(line)
                    if e.get("pdf") == pdf_stem:
                        entries.append(e)
                except _json.JSONDecodeError:
                    continue
    return {
        "summary": summary,
        "individual_calls": entries,
        "verification_note": "Compare total_usd with Google Cloud Console billing.",
    }


@app.get("/api/cost/total")
async def cost_total():
    """Aggregate across every PDF processed (token-counted, actual)."""
    return get_global_cost_summary()


# ---------------------------------------------------------------------------
# WebSocket — live progress per job
# ---------------------------------------------------------------------------

@app.websocket("/ws/jobs/{job_id}")
async def ws_job(ws: WebSocket, job_id: str):
    manager = get_manager()
    await ws.accept()
    job = manager.get_job(job_id)
    if not job:
        await ws.send_json({"error": "job not found"})
        await ws.close()
        return
    queue = manager.subscribe(job_id)
    try:
        # Send initial state
        await ws.send_json(manager._snapshot(job))
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                await ws.send_json(payload)
                if payload.get("status") in ("completed", "failed", "cancelled"):
                    # Keep socket alive briefly so client can read final state
                    await asyncio.sleep(0.1)
            except asyncio.TimeoutError:
                # Heartbeat ping — keeps reverse proxies happy
                await ws.send_json({"heartbeat": True})
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(job_id, queue)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_html = FRONTEND_DIR / "index.html"
    if not index_html.exists():
        return HTMLResponse("<h1>Frontend not found</h1>", status_code=500)
    return HTMLResponse(index_html.read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz():
    return {"ok": True}
