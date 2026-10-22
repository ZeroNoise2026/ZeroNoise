"""
config.py
Environment for apps/backend. Importing this module loads the repo-root .env.

SUPABASE_KEY and ANTHROPIC_API_KEY are read from the environment by
libs/common; MAX_CONTEXT_CHARS by libs/research. Only settings the backend
itself reads are listed here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables (Cloud Run) win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

SUPABASE_URL = os.getenv("SUPABASE_URL")
# JWT secret for verifying Supabase Auth access tokens (legacy HS256).
# Found in Dashboard → Project Settings → API → JWT Settings → JWT Secret.
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
# "low" | "medium" | "high" — thinking depth for summaries and briefings
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")

QUESTION_SERVICE_URL = os.getenv("QUESTION_SERVICE_URL", "http://localhost:8003")

BRIEFING_WINDOW_MINUTES = int(os.getenv("BRIEFING_WINDOW_MINUTES", "15"))
