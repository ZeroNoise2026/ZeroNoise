"""
question/templates/functions.py
Jinja2 global functions — backend deterministic computation (YoY/QoQ/table rendering), avoids having the LLM do arithmetic.

WARNING: input convention — all list[dict] parameters are treated as **newest first** (date DESC).
   shared.db.get_earnings() / get_price_snapshots() default to this ordering.
"""
from __future__ import annotations
from typing import Any, Iterable
from .filters import money, pct


def _safe_get(row: dict, key: str) -> float | None:
    if not isinstance(row, dict):
        return None
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 1. YoY (year-over-year) ──────────────────────────────────────────────
def yoy(rows: list[dict], field: str) -> float | None:
    """YoY = (latest - 4 quarters ago) / |4 quarters ago|. rows newest first, at least 5 entries.

    Returns a decimal (e.g. 0.1537); on failure returns None (in templates None is rendered as 'N/A' by the pct filter).
    """
    if not rows or len(rows) < 5:
        return None
    cur = _safe_get(rows[0], field)
    prev = _safe_get(rows[4], field)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


# ── 2. QoQ (quarter-over-quarter) ──────────────────────────────────────────────
def qoq(rows: list[dict], field: str) -> float | None:
    """QoQ = (latest - previous quarter) / |previous quarter|. rows newest first, at least 2 entries."""
    if not rows or len(rows) < 2:
        return None
    cur = _safe_get(rows[0], field)
    prev = _safe_get(rows[1], field)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


# ── 3. Fiscal year total ────────────────────────────────────────────
def sum_fy(rows: list[dict], field: str, year: int | str) -> float | None:
    """Sum by fiscal year (quarter formatted like "Q3 2025" or "2025Q3"). Returns None if no rows for that year are found."""
    year_s = str(year)
    total = 0.0
    found = False
    for r in rows or []:
        q = r.get("quarter") or r.get("period") or ""
        if year_s in str(q):
            v = _safe_get(r, field)
            if v is not None:
                total += v
                found = True
    return total if found else None


def sum_fy_info(rows: list[dict], field: str, year: int | str) -> dict:
    """Same as sum_fy, but additionally returns the count of quarters aggregated and the quarter labels (used to gate whether the FY view is complete).

    Returns:
        {"value": float | None, "n_quarters": int, "quarters": [str,...]}

    The caller should:
      - If the prior year's n_quarters does not match the current year → do not show the FY comparison (avoids partial-year passing as full-year)
      - If n_quarters < 4 and the user asked about FY → label as YTD
    """
    import re as _re
    year_s = str(year)
    matched: list[tuple[int, dict]] = []
    for r in rows or []:
        q = str(r.get("quarter") or r.get("period") or "")
        if year_s not in q:
            continue
        m = _re.search(r"Q([1-4])", q, _re.IGNORECASE)
        if m:
            matched.append((int(m.group(1)), r))
    matched.sort(key=lambda x: x[0])

    total = 0.0
    found = False
    quarters: list[str] = []
    for q_num, r in matched:
        v = _safe_get(r, field)
        if v is not None:
            total += v
            found = True
            quarters.append(f"Q{q_num}")
    return {
        "value": total if found else None,
        "n_quarters": len(quarters),
        "quarters": quarters,
    }


# ── 3b. YoY / QoQ for a specific quarter (P0-A: when user asks "Q3 2025", no longer default to rows[0]) ──
def _find_row_for_quarter(rows: list[dict], quarter_label: str) -> int | None:
    """Find the index in rows where quarter == 'Q3 2025'. Returns None if not found."""
    target = str(quarter_label).upper().replace(" ", "")
    for i, r in enumerate(rows or []):
        q = str(r.get("quarter") or r.get("period") or "").upper().replace(" ", "")
        if q == target:
            return i
    return None


def yoy_at(rows: list[dict], field: str, quarter_label: str) -> float | None:
    """YoY for a specific quarter = (this quarter - same quarter last year) / |same quarter last year|.

    Different from yoy(): yoy() hard-codes rows[0] and rows[4], assuming the data is sorted by time and continuous.
    yoy_at() matches by exact quarter label, avoiding the error of the user asking about Q3 2025 when rows[0] is Q4 2025.
    """
    import re as _re
    i = _find_row_for_quarter(rows, quarter_label)
    if i is None:
        return None
    m = _re.match(r"Q([1-4])\s+(\d{4})", quarter_label.strip(), _re.IGNORECASE)
    if not m:
        return None
    q_num = m.group(1)
    prev_year = int(m.group(2)) - 1
    prev_label = f"Q{q_num} {prev_year}"
    j = _find_row_for_quarter(rows, prev_label)
    if j is None:
        return None
    cur = _safe_get(rows[i], field)
    prev = _safe_get(rows[j], field)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def qoq_at(rows: list[dict], field: str, quarter_label: str) -> float | None:
    """QoQ for a specific quarter. Previous quarter = Q(n-1) in the same year, or Q4 of the previous year (if n=1)."""
    import re as _re
    i = _find_row_for_quarter(rows, quarter_label)
    if i is None:
        return None
    m = _re.match(r"Q([1-4])\s+(\d{4})", quarter_label.strip(), _re.IGNORECASE)
    if not m:
        return None
    q_num = int(m.group(1))
    year = int(m.group(2))
    if q_num == 1:
        prev_label = f"Q4 {year - 1}"
    else:
        prev_label = f"Q{q_num - 1} {year}"
    j = _find_row_for_quarter(rows, prev_label)
    if j is None:
        return None
    cur = _safe_get(rows[i], field)
    prev = _safe_get(rows[j], field)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


# ── 4. Pick max/min ─────────────────────────────────────────
def pick_max(rows: list[dict], field: str) -> dict | None:
    best: dict | None = None
    best_v: float | None = None
    for r in rows or []:
        v = _safe_get(r, field)
        if v is None:
            continue
        if best_v is None or v > best_v:
            best_v = v
            best = r
    return best


# ── 5. Table rendering (backend pre-generates markdown, LLM side only uses {{tables.xxx}}) ──
def render_table(
    rows: list[dict],
    columns: list[tuple[str, str, str]],
    *,
    limit: int | None = None,
) -> str:
    """Render list[dict] as a Markdown table.

    columns: [(display_name, field_name, formatter)] — formatter ∈
      "money" / "pct" / "compact" / "str" / "int" / "raw"

    Example:
        render_table(earnings, [
            ("Quarter", "quarter", "str"),
            ("EPS",     "eps",     "money_2"),
            ("Revenue", "revenue", "money"),
            ("Net Income", "net_income", "money"),
        ], limit=4)
    """
    if not rows:
        return "_No data available._"
    cols = columns
    use_rows = rows[:limit] if limit else rows

    def _fmt(val: Any, fm: str) -> str:
        if val is None:
            return "N/A"
        if fm == "money":
            return money(val)
        if fm == "money_2":  # EPS-specific: keep 2 decimals
            try:
                return f"${float(val):,.2f}"
            except (TypeError, ValueError):
                return "N/A"
        if fm == "pct":
            return pct(val)
        if fm == "compact":
            from .filters import compact
            return compact(val)
        if fm == "int":
            try:
                return f"{int(float(val)):,}"
            except (TypeError, ValueError):
                return "N/A"
        if fm == "raw":
            return str(val)
        return str(val)

    header = "| " + " | ".join(c[0] for c in cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    body_lines = []
    for r in use_rows:
        cells = [_fmt(r.get(c[1]), c[2]) for c in cols]
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + body_lines)


ALL_FUNCTIONS = {
    "yoy": yoy,
    "qoq": qoq,
    "yoy_at": yoy_at,
    "qoq_at": qoq_at,
    "sum_fy": sum_fy,
    "sum_fy_info": sum_fy_info,
    "pick_max": pick_max,
    "render_table": render_table,
}
