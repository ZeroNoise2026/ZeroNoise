"""
question/schemas/comparison.py
COMPARISON — multi-ticker financial comparison. Plan 4 Path A.

Input: 2-4 tickers, each with earnings rows.
Backend computes: each ticker's latest Q rev/eps + FY revenue + YoY + QoQ.
LLM produces: headline, narrative, per-ticker bullet positioning (qualitative only).
Template rendering: multiple tables (latest quarter, FY view) + narrative.
"""
from __future__ import annotations
from typing import Any

# ─── JSON Schema ─────────────────────────────────────────────
SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["headline", "narrative", "per_ticker_takeaways"],
    "additionalProperties": False,
    "properties": {
        "headline": {
            "type": "string",
            "minLength": 10,
            "maxLength": 250,
            "description": "One-sentence comparative verdict. NO numbers.",
        },
        "narrative": {
            "type": "string",
            "minLength": 50,
            "maxLength": 2000,
            "description": "2-4 paragraph comparative analysis. Reference tickers by symbol. NO specific numbers — the backend renders tables and YoY figures. Focus on relative positioning, trends, competitive dynamics.",
        },
        "per_ticker_takeaways": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["ticker", "takeaway"],
                "additionalProperties": False,
                "properties": {
                    "ticker": {"type": "string", "pattern": "^[A-Z]{1,5}$"},
                    "takeaway": {"type": "string", "minLength": 10, "maxLength": 300,
                                 "description": "One-line qualitative positioning for this ticker. NO specific numbers."},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are a financial analyst producing a STRUCTURED JSON comparison of multiple companies.

CRITICAL RULES:
1. Output ONLY a single valid JSON object matching the schema — no prose before/after.
2. DO NOT include specific dollar amounts or percentages anywhere. All numerical tables (revenue, EPS, YoY growth, QoQ growth) are rendered separately by the backend from verified DB data. Any number you write will conflict with the authoritative figures.
3. Refer to comparisons directionally: "grew faster than", "lower margin profile", "accelerating vs. decelerating", "larger absolute scale", "higher relative growth rate".
4. Ground every claim in the CONTEXT SUMMARY. Do NOT fabricate facts.
5. `per_ticker_takeaways` MUST include one entry per ticker provided, in the same order.
6. Tone: concise, professional, no hype."""

FEW_SHOT: list[dict[str, str]] = [
    {
        "role": "user",
        "content": """User question: Compare GOOGL and META's latest performance.

CONTEXT SUMMARY (verified facts, use directionally):
Tickers in scope: GOOGL, META

GOOGL:
- Latest quarter: Q3 2025
- Revenue: YoY strongly positive, QoQ modestly positive
- EPS: YoY strongly positive, QoQ modestly positive
- Scale: larger (multi-100B FY revenue range)

META:
- Latest quarter: Q3 2025
- Revenue: YoY strongly positive, QoQ modestly positive
- EPS: YoY strongly positive, QoQ strongly positive
- Scale: smaller absolute but higher relative growth

Respond with ONLY a JSON object matching the required schema.""",
    },
    {
        "role": "assistant",
        "content": """{
  "headline": "Both GOOGL and META posted strong Q3 growth, but META's relative acceleration outpaced GOOGL's steadier scale-driven expansion.",
  "narrative": "Alphabet and Meta both delivered another quarter of robust top-line and earnings growth, reaffirming the health of digital advertising and adjacent AI-adjacent revenue streams. GOOGL continues to lead on absolute scale, while META's smaller base shows a higher relative growth rate, particularly on EPS where operating leverage is compounding.\\n\\nOn a sequential basis, META's quarter-over-quarter momentum was noticeably stronger than GOOGL's, suggesting demand acceleration in its core Family of Apps segment. For a quality-growth tilt, both remain credible names; for relative momentum, META is the stronger setup entering Q4.",
  "per_ticker_takeaways": [
    {"ticker": "GOOGL", "takeaway": "Scale leader with consistent year-over-year growth; sequential pace is steady rather than accelerating."},
    {"ticker": "META", "takeaway": "Smaller absolute base but stronger relative growth and sequential acceleration, particularly on EPS."}
  ]
}""",
    },
]

# ─── Template ────────────────────────────────────────────────────
TEMPLATE = """## Comparison: {{ tickers_joined }}

{{ quarter_warning }}**{{ headline }}**

### Latest Quarter Snapshot
{{ latest_quarter_table }}

{{ fy_section }}

{{ narrative }}

### Per-Ticker Takeaways
{{ takeaways_section }}"""


# ─── Context builder ────────────────────────────────────────
def build_context(
    *,
    tickers: list[str],
    per_ticker_earnings: dict[str, list[dict]],
    llm_output: dict,
    fy_year: int | None = None,
    target_quarter: str | None = None,
) -> dict:
    """Build comparison render context.

    Args:
        tickers: ordered list of symbols
        per_ticker_earnings: {ticker -> earnings rows (latest first)}
        llm_output: validated LLM JSON
        fy_year: which FY to aggregate (default: year of latest quarter across tickers)
        target_quarter: explicit quarter to compare (e.g. "Q3 2025"); if None,
                        use each ticker's latest row.
    """
    from question.templates.functions import (
        yoy, qoq, yoy_at, qoq_at, sum_fy_info, render_table,
    )
    from question.templates.filters import money, pct
    import re as _re

    # ─── Resolve target quarter ─────────────────────────────
    # If user asked about a specific quarter, pin to that row per ticker.
    # If that quarter is missing for a ticker, fall back to latest + warn via note.
    quarter_warnings: list[str] = []

    def _row_for(rows: list[dict], qlabel: str | None) -> tuple[dict, str | None]:
        """Return (chosen_row, warning_or_None). warning is set when target quarter
        was requested but not found for this ticker."""
        if not rows:
            return {}, None
        if qlabel:
            target_norm = qlabel.upper().replace(" ", "")
            for r in rows:
                q = str(r.get("quarter") or "").upper().replace(" ", "")
                if q == target_norm:
                    return r, None
            # requested quarter not available — fall through to latest with warning
            return rows[0], f"{qlabel} not available; showing {rows[0].get('quarter','latest')}"
        return rows[0], None

    # Infer fy_year if not given: prefer year in target_quarter, else latest row year
    if fy_year is None:
        if target_quarter:
            m = _re.search(r"(\d{4})", target_quarter)
            if m:
                fy_year = int(m.group(1))
        if fy_year is None:
            for t in tickers:
                rows = per_ticker_earnings.get(t) or []
                if rows:
                    q = str(rows[0].get("quarter", ""))
                    m = _re.search(r"(20\d{2})", q)
                    if m:
                        fy_year = int(m.group(1))
                        break
        fy_year = fy_year or 2025

    # ─── Latest quarter table ───────────────────────────────
    latest_rows = []
    for t in tickers:
        rows = per_ticker_earnings.get(t) or []
        latest, warn = _row_for(rows, target_quarter)
        if warn:
            quarter_warnings.append(f"**{t}**: {warn}")
        try:
            eps = latest.get("eps")
            eps_str = f"${float(eps):.2f}" if eps is not None else "N/A"
        except (TypeError, ValueError):
            eps_str = "N/A"
        # Use per-quarter YoY/QoQ when we have an explicit target & it was found
        # (warn == None). Otherwise use positional yoy()/qoq() based on rows[0].
        if target_quarter and not warn:
            rev_yoy_v = yoy_at(rows, "revenue", target_quarter)
            rev_qoq_v = qoq_at(rows, "revenue", target_quarter)
            eps_yoy_v = yoy_at(rows, "eps", target_quarter)
        else:
            rev_yoy_v = yoy(rows, "revenue")
            rev_qoq_v = qoq(rows, "revenue")
            eps_yoy_v = yoy(rows, "eps")
        latest_rows.append({
            "ticker": t,
            "quarter": latest.get("quarter", "N/A"),
            "revenue_str": money(latest.get("revenue")),
            "rev_yoy_str": pct(rev_yoy_v),
            "rev_qoq_str": pct(rev_qoq_v),
            "eps_str": eps_str,
            "eps_yoy_str": pct(eps_yoy_v),
        })
    latest_quarter_table = render_table(
        latest_rows,
        [
            ("Ticker", "ticker", "str"),
            ("Quarter", "quarter", "str"),
            ("Revenue", "revenue_str", "raw"),
            ("Rev YoY", "rev_yoy_str", "raw"),
            ("Rev QoQ", "rev_qoq_str", "raw"),
            ("EPS", "eps_str", "raw"),
            ("EPS YoY", "eps_yoy_str", "raw"),
        ],
    )

    # ─── Full-year / YTD table (P0-B: gate on quarter-count match) ──
    # Only show the FY row when current and prior period have the SAME number
    # of quarters aggregated. Otherwise the "growth" number is meaningless
    # (e.g. Q1-Q3 2025 sum vs full FY 2024 → fake +317% decline reversed).
    fy_rows: list[dict] = []
    any_fy_shown = False
    for t in tickers:
        rows = per_ticker_earnings.get(t) or []
        cur_info = sum_fy_info(rows, "revenue", fy_year)
        prev_info = sum_fy_info(rows, "revenue", fy_year - 1)
        n = cur_info["n_quarters"]
        # Gate: need current >=1 quarter AND prior matches exactly the same # of quarters.
        # Also require the quarter LABELS to match (Q1-Q3 2025 vs Q1-Q3 2024, not Q2-Q4).
        if n == 0 or prev_info["n_quarters"] != n:
            # No reliable FY comparison possible — emit placeholder row w/ N/A
            fy_rows.append({
                "ticker": t,
                "fy_label": f"FY {fy_year}" if n == 4 else f"Q1-Q{n} {fy_year}" if n else f"FY {fy_year}",
                "fy_rev_str": money(cur_info["value"]),
                "prev_fy_label": "—",
                "prev_fy_rev_str": "N/A",
                "fy_growth_str": "N/A",
            })
            continue
        # Same # of quarters — verify label set matches
        if set(cur_info["quarters"]) != set(prev_info["quarters"]):
            fy_rows.append({
                "ticker": t,
                "fy_label": f"Q1-Q{n} {fy_year}" if n < 4 else f"FY {fy_year}",
                "fy_rev_str": money(cur_info["value"]),
                "prev_fy_label": "—",
                "prev_fy_rev_str": "N/A",
                "fy_growth_str": "N/A",
            })
            continue
        any_fy_shown = True
        cur_rev = cur_info["value"]
        prev_rev = prev_info["value"]
        if n == 4:
            period_label = f"FY {fy_year}"
            prev_label = f"FY {fy_year - 1}"
        else:
            sorted_qs = sorted(cur_info["quarters"])
            span = f"{sorted_qs[0]}-{sorted_qs[-1]}" if n > 1 else sorted_qs[0]
            period_label = f"{span} {fy_year}"
            prev_label = f"{span} {fy_year - 1}"
        fy_growth = None
        if cur_rev is not None and prev_rev and prev_rev != 0:
            fy_growth = (cur_rev - prev_rev) / abs(prev_rev)
        fy_rows.append({
            "ticker": t,
            "fy_label": period_label,
            "fy_rev_str": money(cur_rev),
            "prev_fy_label": prev_label,
            "prev_fy_rev_str": money(prev_rev),
            "fy_growth_str": pct(fy_growth),
        })

    # If no ticker has a reliable FY comparison, suppress the whole block
    if any_fy_shown:
        fy_table = render_table(
            fy_rows,
            [
                ("Ticker", "ticker", "str"),
                ("Period", "fy_label", "str"),
                ("Revenue", "fy_rev_str", "raw"),
                ("Prior-Period", "prev_fy_label", "str"),
                ("Prior Revenue", "prev_fy_rev_str", "raw"),
                ("Growth YoY", "fy_growth_str", "raw"),
            ],
        )
        fy_section = f"### Full-Year / YTD Revenue & Growth\n{fy_table}"
    else:
        fy_section = "_Full-year YoY comparison unavailable — insufficient historical earnings data._"

    # ─── Per-ticker takeaways (LLM-generated, qualitative) ──
    takeaways = llm_output.get("per_ticker_takeaways") or []
    takeaway_lines: list[str] = []
    for item in takeaways:
        if not isinstance(item, dict):
            continue
        t = item.get("ticker", "?")
        txt = item.get("takeaway", "")
        takeaway_lines.append(f"- **{t}** — {txt}")
    takeaways_section = "\n".join(takeaway_lines) if takeaway_lines else "_No takeaways provided._"

    return {
        "tickers_joined": " vs. ".join(tickers),
        "headline": llm_output.get("headline", ""),
        "narrative": llm_output.get("narrative", ""),
        "latest_quarter_table": latest_quarter_table,
        "fy_section": fy_section,
        "takeaways_section": takeaways_section,
        "quarter_warning": (
            "> ⚠️ " + " · ".join(quarter_warnings) + "\n"
            if quarter_warnings else ""
        ),
    }


def summarize_for_llm(
    tickers: list[str],
    per_ticker_earnings: dict[str, list[dict]],
    target_quarter: str | None = None,
) -> str:
    """Qualitative summary per ticker fed into the LLM prompt.

    If `target_quarter` is provided, anchor each ticker's summary to that row
    (with same-quarter YoY) so the narrative matches the numerical table.
    """
    from question.templates.functions import yoy, qoq, yoy_at, qoq_at

    def _direction(v, strong=0.10):
        if v is None:
            return "data unavailable"
        if v > strong: return "strongly positive"
        if v > 0.02:   return "modestly positive"
        if v >= -0.02: return "roughly flat"
        if v >= -strong: return "modestly negative"
        return "strongly negative"

    def _scale(latest_rev):
        if latest_rev is None:
            return "unknown scale"
        v = abs(float(latest_rev))
        if v >= 80e9:   return "very large quarterly revenue scale"
        if v >= 20e9:   return "large quarterly revenue scale"
        if v >= 5e9:    return "mid quarterly revenue scale"
        return "smaller quarterly revenue scale"

    parts = [f"Tickers in scope: {', '.join(tickers)}"]
    if target_quarter:
        parts.append(f"Focus quarter (requested by user): {target_quarter}")
    parts.append("")
    for t in tickers:
        rows = per_ticker_earnings.get(t) or []
        # Resolve per-ticker anchor
        anchor = rows[0] if rows else {}
        used_target = False
        if target_quarter and rows:
            tnorm = target_quarter.upper().replace(" ", "")
            for r in rows:
                q = str(r.get("quarter") or "").upper().replace(" ", "")
                if q == tnorm:
                    anchor = r
                    used_target = True
                    break

        if used_target:
            rev_yoy_v = yoy_at(rows, "revenue", target_quarter)  # type: ignore[arg-type]
            rev_qoq_v = qoq_at(rows, "revenue", target_quarter)  # type: ignore[arg-type]
            eps_yoy_v = yoy_at(rows, "eps", target_quarter)      # type: ignore[arg-type]
            eps_qoq_v = qoq_at(rows, "eps", target_quarter)      # type: ignore[arg-type]
        else:
            rev_yoy_v = yoy(rows, "revenue")
            rev_qoq_v = qoq(rows, "revenue")
            eps_yoy_v = yoy(rows, "eps")
            eps_qoq_v = qoq(rows, "eps")

        parts.append(f"{t}:")
        anchor_note = "requested" if used_target else (
            "requested quarter missing, using latest" if target_quarter else "latest available"
        )
        parts.append(f"- Focus quarter: {anchor.get('quarter','N/A')} ({anchor_note})")
        parts.append(f"- Revenue: YoY {_direction(rev_yoy_v)}, QoQ {_direction(rev_qoq_v)}")
        parts.append(f"- EPS: YoY {_direction(eps_yoy_v)}, QoQ {_direction(eps_qoq_v)}")
        parts.append(f"- Scale: {_scale(anchor.get('revenue'))}")
        parts.append("")
    return "\n".join(parts)
