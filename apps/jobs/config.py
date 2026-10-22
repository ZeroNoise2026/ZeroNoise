"""
config.py
Environment for apps/jobs. Importing this module loads the repo-root .env.

Credentials and model settings are read from the environment by the libs
(common, research, marketdata); the ingestion pipeline has its own
pipeline/config.py. Only settings the report/briefing jobs read live here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# The single repo-root .env: the nearest .env above this file. Real environment
# variables win; in a container there is no file and this is a no-op.
load_dotenv(next((d / ".env" for d in Path(__file__).resolve().parents if (d / ".env").is_file()), None))

# Research reports written by `python -m reports.run`.
OUTPUT_DIR = Path(os.getenv("REPORT_OUTPUT_DIR", Path(__file__).parent / "output"))

# How long after 08:00 local time a user's briefing may still be generated.
BRIEFING_WINDOW_MINUTES = int(os.getenv("BRIEFING_WINDOW_MINUTES", "15"))
