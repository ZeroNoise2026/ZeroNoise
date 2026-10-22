"""
common/audit.py
Fire-and-forget audit logging to the Supabase api_audit_log table.

Usage:
    from common.audit import log_api_call

    log_api_call(
        service="question",
        api_type="claude_llm",
        endpoint="messages",
        model="claude-haiku-5-5",
        tokens_in=120, tokens_out=50, tokens_total=170,
        latency_ms=530,
    )

    # Multiple APIs in one operation:
    log_api_call(
        service="question",
        api_type="finnhub,fmp",
        endpoint="fetch_tier2_context/AAPL",
        latency_ms=1200,
    )
"""

import logging
from typing import Any, Optional

from common import supabase

logger = logging.getLogger(__name__)


def log_api_call(
    service: str,
    api_type: str,
    endpoint: Optional[str] = None,
    model: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    tokens_total: Optional[int] = None,
    latency_ms: Optional[int] = None,
    status: str = "ok",
    error_msg: Optional[str] = None,
) -> None:
    """Write one audit row to api_audit_log. Fire-and-forget, never raises."""
    try:
        if not supabase.is_configured():
            return
        client = supabase.get_client()

        row: dict[str, Any] = {
            "service": service,
            "api_type": api_type,
            "status": status,
        }
        if endpoint is not None:
            row["endpoint"] = endpoint
        if model is not None:
            row["model"] = model
        if tokens_in is not None:
            row["tokens_in"] = tokens_in
        if tokens_out is not None:
            row["tokens_out"] = tokens_out
        if tokens_total is not None:
            row["tokens_total"] = tokens_total
        if latency_ms is not None:
            row["latency_ms"] = latency_ms
        if error_msg is not None:
            row["error_msg"] = error_msg[:500]  # cap error text

        client.table("api_audit_log").insert(row).execute()

    except Exception as e:
        # Audit must never break the main flow
        logger.debug(f"Audit log write failed (non-fatal): {e}")
