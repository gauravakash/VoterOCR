import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # development | production

# LLM provider selection
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

# Hybrid routing
PRIMARY_VISION_MODEL = os.getenv("PRIMARY_VISION_MODEL", "gemini-2.5-flash")
FALLBACK_VISION_MODEL = os.getenv("FALLBACK_VISION_MODEL", "gemini-2.5-pro")
VISION_MODEL = os.getenv("VISION_MODEL", PRIMARY_VISION_MODEL)
TEXT_MODEL = os.getenv("TEXT_MODEL", "gemini-2.5-flash")

FALLBACK_ENABLED = os.getenv("FALLBACK_ENABLED", "true").lower() == "true"
MIN_VOTERS_PER_PAGE = int(os.getenv("MIN_VOTERS_PER_PAGE", "15"))
MAX_VOTERS_PER_PAGE = int(os.getenv("MAX_VOTERS_PER_PAGE", "40"))
FALLBACK_RETRY_DELAY = int(os.getenv("FALLBACK_RETRY_DELAY", "2"))
ENABLE_FALLBACK_LOGGING = os.getenv("ENABLE_FALLBACK_LOGGING", "true").lower() == "true"

# Sarvam (used only when LLM_PROVIDER=sarvam)
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_BASE_URL = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
SARVAM_LANGUAGE = os.getenv("SARVAM_LANGUAGE", "hi-IN")
SARVAM_OUTPUT_FORMAT = os.getenv("SARVAM_OUTPUT_FORMAT", "md")
SARVAM_PAGES_PER_CHUNK = int(os.getenv("SARVAM_PAGES_PER_CHUNK", "10"))

# Concurrency
MAX_CONCURRENT_PDFS = int(os.getenv("MAX_CONCURRENT_PDFS", "3"))
MAX_CONCURRENT_PAGES_PER_PDF = int(os.getenv("MAX_CONCURRENT_PAGES_PER_PDF", "5"))
MAX_CONCURRENT_CHUNKS_PER_PDF = int(os.getenv("MAX_CONCURRENT_CHUNKS_PER_PDF", "3"))
DPI = int(os.getenv("DPI", "200"))

# Paths
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(ROOT_DIR / "data" / "uploads"))).resolve()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(ROOT_DIR / "data" / "outputs"))).resolve()
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT_DIR / "data" / "jobs.db"))).resolve()

PORT = int(os.getenv("PORT", "8000"))

COST_PER_PAGE_INR = float(os.getenv("COST_PER_PAGE_INR", "0.50"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Production settings
DEBUG = ENVIRONMENT != "production"
