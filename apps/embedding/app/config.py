import os
from pathlib import Path

from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables (Cloud Run) win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
PORT = int(os.getenv("PORT", "8002"))
