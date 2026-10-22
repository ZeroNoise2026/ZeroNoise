"""
config.py
Environment for apps/summarization (question service + summary CLI).

Importing this module loads the repo-root .env. Credentials (SUPABASE_URL, SUPABASE_KEY,
ANTHROPIC_API_KEY) and the report settings (LLM_MODEL_SUMMARY,
LLM_EFFORT_SUMMARY, MAX_CONTEXT_CHARS) are read from the environment by
libs/common and libs/research; only app-specific settings live here.

Sections:
  1. Summary CLI
  2. Question-service LLM settings
  3. Upstream service URLs
  4. Vector search parameters
  5. Tier 2 cache parameters
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables (Cloud Run) win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

# ═══════════════════════════════════════════
# 1. Summary CLI
# ═══════════════════════════════════════════
OUTPUT_DIR = Path(__file__).parent / "output"
