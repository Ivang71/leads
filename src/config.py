import os, logging
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

LOG_LEVEL = getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)

TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200
MS_TIMEOUT_SEC = int(os.getenv("MS_TIMEOUT_SEC") or "36")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN") or ""
TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET") or ""
TG_WEBHOOK_URL = os.getenv("TG_WEBHOOK_URL") or ""
ALICE_URL = (os.getenv("ALICE_URL") or "").strip()

STATS_PATH = os.path.join(PROJECT_ROOT, "stat.jsonl")
GREETED_PATH = os.path.join(PROJECT_ROOT, "greeted.json")


