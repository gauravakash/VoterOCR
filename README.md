# 🗳️ Voter Roll OCR + Religion Classifier

A FastAPI app that ingests scanned ECI Final Roll PDFs, runs them through
**Sarvam AI's Document Intelligence API**, parses voter cards (नाम / पिता का
नाम / आयु / लिंग / Voter ID), and classifies each voter as Hindu / Muslim /
Sikh / Unknown using a rule-based Devanagari token classifier. Built for
constituency 169 — बक्शी का तालाब (Lucknow, UP).

- **Backend:** FastAPI + asyncio + SQLite
- **OCR:** Sarvam Document Intelligence (`hi-IN`, markdown output)
- **Frontend:** Vanilla JS, single page, WebSocket live progress
- **Outputs:** Per-PDF JSON + multi-sheet Excel + summary JSON

---

## Quick start

```bash
# 1. System deps (poppler is no longer required — we use pypdf for splitting)
brew install poppler        # mac (only if you also want PDF→image rendering)
sudo apt install poppler-utils  # linux

# 2. Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env and set SARVAM_API_KEY

# 4. Run
uvicorn backend.main:app --reload --port 8000

# 5. Open
open http://localhost:8000
```

Drop one or more booth PDFs onto the page. They will queue, process in
parallel, and produce JSON + Excel outputs in `data/outputs/`.

---

## Architecture

```
PDF upload  ─▶  data/uploads/<job>.pdf
                  │
                  ▼
        Job created in SQLite
                  │
                  ▼
        Split into 10-page chunks ──┐
                  │                  │
          For each chunk:            │ (parallel: MAX_CONCURRENT_CHUNKS_PER_PDF)
                  │                  │
                  ▼                  │
        Sarvam Doc Intelligence ◀────┘
        (create job → upload → start → poll → download)
                  │
                  ▼
        Parse voter cards from markdown
                  │
                  ▼
        Religion classifier (rule-based Devanagari tokens)
                  │
                  ▼
        Save chunk result in SQLite (resumable)
                  │
                  ▼
        Aggregate → JSON + Excel + summary JSON
```

Each chunk is independently checkpointed in the `job_chunks` table. If the
server crashes mid-job, on restart `lifespan()` calls `resume_pending()` which
re-launches every job whose status is `queued` or `processing`. Only chunks
that were not yet `completed` are re-submitted to Sarvam.

### File map

```
backend/
  main.py                FastAPI app, REST endpoints, WebSocket
  sarvam_client.py       Async client for Sarvam Doc Intelligence API
  pdf_processor.py       Split PDF into chunks, parse markdown → voters
  religion_classifier.py Rule-based Devanagari token classifier
  job_manager.py         SQLite job + chunk tracking, async orchestration
  output_generator.py    JSON / Excel / summary writers
  models.py              SQLModel + Pydantic schemas
  config.py              Env var loading

frontend/
  index.html             Single-page UI
  style.css              Modern minimal styling
  app.js                 Drag/drop, live progress via WebSocket

data/
  uploads/               Uploaded PDFs + per-job chunk dirs
  outputs/               Generated JSON + Excel
  jobs.db                SQLite checkpoint store
```

---

## REST API

| Method | Path                         | Purpose                                   |
| ------ | ---------------------------- | ----------------------------------------- |
| POST   | `/api/upload`                | Upload one or more PDFs                   |
| GET    | `/api/jobs`                  | List all jobs                             |
| GET    | `/api/jobs/{id}`             | Get a job snapshot                        |
| POST   | `/api/jobs/{id}/cancel`      | Cancel a running job                      |
| DELETE | `/api/jobs/{id}`             | Delete job + outputs                      |
| GET    | `/api/jobs/{id}/result`      | Download result (`?format=json|xlsx|summary`) |
| GET    | `/api/stats`                 | Aggregate stats across all jobs           |
| WS     | `/ws/jobs/{id}`              | Live per-job progress stream              |

Sample upload:

```bash
curl -X POST http://localhost:8000/api/upload \
  -F "files=@booth_001.pdf" \
  -F "files=@booth_002.pdf"
```

---

## Religion Classifier

`backend/religion_classifier.py` defines four Devanagari token sets:

- `MUSLIM_STRONG` — ~280 strong markers (मोहम्मद, अली, खान, बानो, खातून, …)
- `HINDU_STRONG`  — ~330 strong markers (देवी, कुमारी, सिंह, राम, शर्मा, …)
- `SIKH_STRONG`   — ~50 strong markers (कौर, गिल, ढिल्लों, …)
- `*_WEAK` — ambiguous tokens that only count when paired with another signal

Algorithm:

1. Tokenize voter name + relative name (handle `मो०`, `मो.`, ZWJ, danda).
2. Score each token: strong markers = 3 points, weak = 1 point.
3. Highest score wins. Confidence:
   - **High** — clear unambiguous winner from the voter's own name.
   - **Medium** — winner came from the relative's name, or two religions
     scored ≥3 in the same name.
   - **Low** — only weak matches.
4. Return `Unknown` only if no scored token in either name.

To extend, just append more tokens to the existing sets.

---

## Sarvam API notes

The Document Intelligence API is async and chunked:

| Step | Method | Path                                                     |
| ---- | ------ | -------------------------------------------------------- |
| 1    | POST   | `/doc-digitization/job/v1`                               |
| 2    | POST   | `/doc-digitization/job/v1/upload-files`                  |
| 3    | PUT    | `<presigned URL>` (with `x-ms-blob-type: BlockBlob`)     |
| 4    | POST   | `/doc-digitization/job/v1/{job_id}/start`                |
| 5    | GET    | `/doc-digitization/job/v1/{job_id}/status`               |
| 6    | POST   | `/doc-digitization/job/v1/{job_id}/download-files`       |
| 7    | GET    | `<presigned URL>` → ZIP (markdown / json inside)         |

Auth header: `api-subscription-key: <key>`. PDFs ≤ 10 pages per job, ≤ 200 MB.

We split each booth PDF (~30-40 pages) into 10-page chunks, run them in
parallel (`MAX_CONCURRENT_CHUNKS_PER_PDF`, default 3), and merge the results.

---

## Configuration

All settings live in `.env` (see `.env.example`):

| Var                              | Default | Notes                                              |
| -------------------------------- | ------- | -------------------------------------------------- |
| `SARVAM_API_KEY`                 | —       | **Required**                                       |
| `SARVAM_BASE_URL`                | `https://api.sarvam.ai` |                                |
| `SARVAM_LANGUAGE`                | `hi-IN` | BCP-47 language hint for OCR                       |
| `SARVAM_OUTPUT_FORMAT`           | `md`    | `md`, `html`, or `json`                            |
| `SARVAM_PAGES_PER_CHUNK`         | `10`    | Sarvam hard limit                                  |
| `MAX_CONCURRENT_PDFS`            | `3`     | Booths in flight at once                           |
| `MAX_CONCURRENT_CHUNKS_PER_PDF`  | `3`     | Chunk-level parallelism                            |
| `COST_PER_PAGE_INR`              | `0.50`  | For UI cost estimate only                          |
| `MAX_UPLOAD_SIZE_MB`             | `50`    | Per-file cap                                       |
| `PORT`                           | `8000`  |                                                    |

---

## Production tips

- Run with multiple workers only if you change SQLite for Postgres — the
  current setup assumes a single uvicorn process owning the DB.
- For 545 PDFs, set `MAX_CONCURRENT_PDFS=5` and `MAX_CONCURRENT_CHUNKS_PER_PDF=4`
  if your Sarvam plan allows it. Watch for 429s in the logs.
- Outputs land in `data/outputs/`. Back them up periodically.
- The classifier is rule-based and language-specific — check the
  `Unknown` sheet of each Excel for misses, then add tokens to
  `religion_classifier.py` as you find patterns.
