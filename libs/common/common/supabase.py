"""
common/supabase.py
Lazy Supabase client — one per process, created on first use.
"""

import os
import threading
from typing import Optional

from supabase import Client, create_client

_client: Optional[Client] = None
_lock = threading.Lock()


def is_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))


def get_client() -> Client:
    """Return the shared client. Raises if SUPABASE_URL / SUPABASE_KEY are unset."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                if not is_configured():
                    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
                _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client
