"""Async client for Sarvam Document Intelligence API.

Workflow per chunk (max 10 pages):
  1. POST /doc-digitization/job/v1                             → create job
  2. POST /doc-digitization/job/v1/upload-files                → presigned URL
  3. PUT  <presigned_url>                                       → upload file
  4. POST /doc-digitization/job/v1/{job_id}/start              → start
  5. GET  /doc-digitization/job/v1/{job_id}/status             → poll
  6. POST /doc-digitization/job/v1/{job_id}/download-files     → presigned URL
  7. GET  <download_url>                                        → fetch ZIP

Reference: https://docs.sarvam.ai/api-reference-docs/document-intelligence/
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional

import httpx

from . import config

log = logging.getLogger(__name__)


class SarvamError(Exception):
    pass


class SarvamClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or config.SARVAM_API_KEY
        if not self.api_key:
            raise SarvamError("SARVAM_API_KEY is not set")
        self.base_url = (base_url or config.SARVAM_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=30.0),
            headers={"api-subscription-key": self.api_key},
        )

    async def aclose(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        retry_on: tuple[int, ...] = (429, 500, 502, 503, 504),
        **kwargs,
    ) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
                if resp.status_code in retry_on and attempt < max_retries:
                    delay = (2 ** attempt) + random.random()
                    log.warning(
                        "Sarvam %s %s → %s (retry %d/%d in %.1fs)",
                        method, url, resp.status_code, attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt >= max_retries:
                    raise
                delay = (2 ** attempt) + random.random()
                log.warning(
                    "Sarvam %s %s → %s (retry %d/%d in %.1fs)",
                    method, url, type(e).__name__, attempt + 1, max_retries, delay,
                )
                await asyncio.sleep(delay)
        if last_exc:
            raise last_exc
        raise SarvamError("Unreachable retry path")

    # ------------------------------------------------------------------
    # 1. Create job
    # ------------------------------------------------------------------
    async def create_job(
        self,
        language: str = config.SARVAM_LANGUAGE,
        output_format: str = config.SARVAM_OUTPUT_FORMAT,
    ) -> str:
        url = f"{self.base_url}/doc-digitization/job/v1"
        payload = {
            "job_parameters": {
                "language": language,
                "output_format": output_format,
            }
        }
        resp = await self._request("POST", url, json=payload)
        if resp.status_code not in (200, 202):
            raise SarvamError(f"create_job failed: {resp.status_code} {resp.text}")
        data = resp.json()
        job_id = data.get("job_id")
        if not job_id:
            raise SarvamError(f"create_job missing job_id: {data}")
        return job_id

    # ------------------------------------------------------------------
    # 2. Get presigned upload URL
    # ------------------------------------------------------------------
    async def get_upload_url(self, sarvam_job_id: str, filename: str) -> str:
        url = f"{self.base_url}/doc-digitization/job/v1/upload-files"
        payload = {"job_id": sarvam_job_id, "files": [filename]}
        resp = await self._request("POST", url, json=payload)
        if resp.status_code not in (200, 202):
            raise SarvamError(f"get_upload_url failed: {resp.status_code} {resp.text}")
        data = resp.json()
        upload_urls = data.get("upload_urls") or {}
        # Response may map filename → string url, or filename → {file_url: ...}
        entry = upload_urls.get(filename)
        if isinstance(entry, dict):
            url_val = entry.get("file_url") or entry.get("url")
        else:
            url_val = entry
        if not url_val:
            raise SarvamError(f"upload URL missing in response: {data}")
        return url_val

    # ------------------------------------------------------------------
    # 3. PUT to presigned URL
    # ------------------------------------------------------------------
    async def upload_file(self, presigned_url: str, file_path: Path) -> None:
        # Use a clean client without our auth headers for the storage upload.
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as raw:
            with open(file_path, "rb") as fh:
                data = fh.read()
            for attempt in range(4):
                try:
                    resp = await raw.put(
                        presigned_url,
                        content=data,
                        headers={"Content-Type": "application/pdf",
                                 "x-ms-blob-type": "BlockBlob"},
                    )
                    if 200 <= resp.status_code < 300:
                        return
                    if attempt < 3 and resp.status_code in (429, 500, 502, 503, 504):
                        await asyncio.sleep(2 ** attempt + random.random())
                        continue
                    raise SarvamError(
                        f"upload_file failed: {resp.status_code} {resp.text[:300]}"
                    )
                except (httpx.ConnectError, httpx.ReadTimeout) as e:
                    if attempt >= 3:
                        raise
                    await asyncio.sleep(2 ** attempt + random.random())

    # ------------------------------------------------------------------
    # 4. Start job
    # ------------------------------------------------------------------
    async def start_job(self, sarvam_job_id: str) -> dict:
        url = f"{self.base_url}/doc-digitization/job/v1/{sarvam_job_id}/start"
        resp = await self._request("POST", url, json={})
        if resp.status_code not in (200, 202):
            raise SarvamError(f"start_job failed: {resp.status_code} {resp.text}")
        return resp.json()

    # ------------------------------------------------------------------
    # 5. Poll status
    # ------------------------------------------------------------------
    async def get_status(self, sarvam_job_id: str) -> dict:
        url = f"{self.base_url}/doc-digitization/job/v1/{sarvam_job_id}/status"
        resp = await self._request("GET", url)
        if resp.status_code != 200:
            raise SarvamError(f"get_status failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def wait_until_complete(
        self,
        sarvam_job_id: str,
        poll_every: float = 5.0,
        max_wait: float = 1800.0,
        cancel_check=None,
    ) -> dict:
        elapsed = 0.0
        while elapsed < max_wait:
            if cancel_check and cancel_check():
                raise SarvamError("cancelled")
            data = await self.get_status(sarvam_job_id)
            state = data.get("job_state", "")
            if state in ("Completed", "PartiallyCompleted", "Failed"):
                return data
            await asyncio.sleep(poll_every)
            elapsed += poll_every
        raise SarvamError(f"timeout waiting for sarvam job {sarvam_job_id}")

    # ------------------------------------------------------------------
    # 6. Get download URLs
    # ------------------------------------------------------------------
    async def get_download_urls(self, sarvam_job_id: str) -> dict:
        url = f"{self.base_url}/doc-digitization/job/v1/{sarvam_job_id}/download-files"
        resp = await self._request("POST", url, json={})
        if resp.status_code not in (200, 202):
            raise SarvamError(f"get_download_urls failed: {resp.status_code} {resp.text}")
        return resp.json()

    # ------------------------------------------------------------------
    # 7. Download
    # ------------------------------------------------------------------
    async def download(self, presigned_url: str) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as raw:
            for attempt in range(4):
                try:
                    resp = await raw.get(presigned_url)
                    if 200 <= resp.status_code < 300:
                        return resp.content
                    if attempt < 3 and resp.status_code in (429, 500, 502, 503, 504):
                        await asyncio.sleep(2 ** attempt + random.random())
                        continue
                    raise SarvamError(
                        f"download failed: {resp.status_code} {resp.text[:300]}"
                    )
                except (httpx.ConnectError, httpx.ReadTimeout) as e:
                    if attempt >= 3:
                        raise
                    await asyncio.sleep(2 ** attempt + random.random())
        raise SarvamError("unreachable download path")

    # ------------------------------------------------------------------
    # High-level: process one PDF chunk end-to-end
    # ------------------------------------------------------------------
    async def process_pdf_chunk(
        self,
        pdf_path: Path,
        cancel_check=None,
    ) -> tuple[str, dict, bytes]:
        """Submit a PDF chunk and return (sarvam_job_id, status, zip_bytes)."""
        sarvam_job_id = await self.create_job()
        upload_url = await self.get_upload_url(sarvam_job_id, pdf_path.name)
        await self.upload_file(upload_url, pdf_path)
        await self.start_job(sarvam_job_id)
        status = await self.wait_until_complete(sarvam_job_id, cancel_check=cancel_check)
        if status.get("job_state") == "Failed":
            raise SarvamError(f"sarvam job failed: {status}")
        download_info = await self.get_download_urls(sarvam_job_id)
        download_urls = download_info.get("download_urls") or {}
        if not download_urls:
            raise SarvamError(f"no download URLs returned: {download_info}")
        # Take first available file URL
        first = next(iter(download_urls.values()))
        if isinstance(first, dict):
            file_url = first.get("file_url") or first.get("url")
        else:
            file_url = first
        if not file_url:
            raise SarvamError(f"download URL malformed: {download_info}")
        zip_bytes = await self.download(file_url)
        return sarvam_job_id, status, zip_bytes
