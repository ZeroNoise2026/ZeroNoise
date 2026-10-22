"""
question/schemas/earnings_analysis.py
EARNINGS_ANALYSIS — LLM-involved (narrative only); all numbers computed deterministically by the backend.

Flow:
  1. build_context(ticker, earnings) — compute from DB earnings rows:
       revenue_yoy / revenue_qoq / eps_yoy / eps_qoq / earnings_table / latest_*
  2. structured.generate_json(SYSTEM_PROMPT, user_query, context_summary, SCHEMA)
     → produces {"headline": str, "narrative": str, "key_drivers": [str,...]}
  3. render(TEMPLATE, {...context, **llm_output}) — final Markdown
"""
from __future__ import annotations
from typing import Any

# ─── 1. JSON Schema for LLM output ───────────────────────────────────────
SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["headline", "narrative"],
    "additionalProperties": False,
    "properties": {
        "headline": {
            "type": "string",
            "minLength": 5,
            "maxLength": 200,
            "description": "One-sentence summary of the latest quarter's performance. NO SPECIFIC NUMBERS — refer to metrics qualitatively ('strong revenue growth', 'margin contraction').",
        },
        "narrative": {
            "type": "string",
            "minLength": 30,
            "maxLength": 1500,
            "description": "2-4 paragraph analysis of earnings trends, drivers, and outlook. You may reference directional signals but DO NOT quote specific dollar amounts or percentages — those are rendered separately.",
        },
        "key_drivers": {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "minItems": 0,
            "maxItems": 5,
            "description": "Optional list of 2-5 business drivers behind the results (e.g. 'AI chip demand', 'operating leverage').",
        },
    },
}

# ─── 2. System prompt for the LLM ─────────────────────────────────────
SYSTEM_PROMPT = """You are a financial analyst. Produce a STRUCTURED JSON response analyzing a company's recent earnings.

CRITICAL RULES:
1. Output ONLY valid JSON matching the schema — no prose before/after.
2. DO NOT include specific dollar amounts or percentages in `headline` / `narrative` / `key_drivers`. All numerical figures (revenue, EPS, YoY/QoQ growth, etc.) are rendered by the backend from verified database data. If you include numbers, they will conflict with the authoritative figures.
3. Refer to metrics directionally: "strong growth", "margin pressure", "accelerating", "flat sequentially", "year-over-year decline", etc.
4. Ground every claim in the CONTEXT SUMMARY provided. Do NOT fabricate new facts.
5. Use a professional, concise tone. Avoid hype words ("breathtaking", "stunning")."""

# ─── 3. Few-shot examples ───────────────────────────────────────────────
FEW_SHOT: list[dict[str, str]] = [
    {
        "role": "user",
        "content": """User question: How did NVDA's latest earnings look?

CONTEXT SUMMARY (verified facts, use these qualitatively):
- Latest quarter: Q3 2025
- Revenue direction: YoY strongly positive, QoQ modestly positive
- EPS direction: YoY strongly positive, QoQ modestly positive
- Gross margin: stable and high
- Recent trend (4 quarters): accelerating

Respond in JSON matching the schema.""",
    },
    {
        "role": "assistant",
        "content": """{
  "headline": "NVDA delivered another quarter of strong year-over-year revenue and earnings growth, driven by sustained data-center demand.",
  "narrative": "NVIDIA's latest quarter extended the accelerating growth trajectory the company has shown through 2025. Year-over-year gains in both revenue and earnings remain substantial, while sequential growth, though more modest, confirms that demand has not plateaued. Gross margin held at elevated levels, indicating pricing power on the flagship GPU stack has not eroded meaningfully.\\n\\nThe quarter points to continued execution on the AI infrastructure thesis. Absent a sharp pullback in hyperscaler capex or a supply-side shock, the growth mix looks durable into the next few quarters.",
  "key_drivers": ["data-center GPU demand", "pricing power on flagship SKUs", "operating leverage"]
}""",
    },
]

# ─── 4. Final render template ────────────────────────────────────────────────
TEMPLATE = """## {{ ticker }} — Earnings Analysis

{{ quarter_warning }}**{{ headline }}**

**Latest Quarter ({{ latest_quarter }})**  
**Revenue:** {{ latest_revenue_str }} ({{ revenue_yoy_str }} YoY · {{ revenue_qoq_str }} QoQ)  
**EPS:** {{ latest_eps_str }} ({{ eps_yoy_str }} YoY · {{ eps_qoq_str }} QoQ)  
**Net Income:** {{ latest_ni_str }}  

{{ narrative }}

### Recent Earnings History
{{ earnings_table }}

{{ key_drivers_section }}"""


# ─── 5. Context builder ─────────────────────────────────────────────
def build_context(
    *,
    ticker: str,
    earnings: list[dict],
    llm_output: dict,
    target_quarter: str | None = None,
) -> dict:
    """Merge DB-derived fields with LLM narrative. All numbers come from DB.

    If `target_quarter` (e.g. 'Q3 2025') is provided, pin analysis to that row
    (using yoy_at/qoq_at for same-quarter YoY). Falls back to latest row with a
    warning note if the target quarter is missing.
    """
    from question.templates.functions import yoy, qoq, yoy_at, qoq_at, render_table
    from question.templates.filters import money, pct

    earnings = earnings or []

    # Resolve which row to analyze
    chosen_row = earnings[0] if earnings else {}
    quarter_warning = ""
    used_target = False
    if target_quarter and earnings:
        tnorm = target_quarter.upper().replace(" ", "")
        for r in earnings:
            q = str(r.get("quarter") or "").upper().replace(" ", "")
            if q == tnorm:
                chosen_row = r
                used_target = True
                break
        if not used_target:
            quarter_warning = (
                f"> ⚠️ {target_quarter} not available for {ticker}; "
                f"showing {chosen_row.get('quarter','latest')} instead.\n\n"
            )

    if used_target:
        rev_yoy = yoy_at(earnings, "revenue", target_quarter)  # type: ignore[arg-type]
        rev_qoq = qoq_at(earnings, "revenue", target_quarter)  # type: ignore[arg-type]
        eps_yoy = yoy_at(earnings, "eps", target_quarter)      # type: ignore[arg-type]
        eps_qoq = qoq_at(earnings, "eps", target_quarter)      # type: ignore[arg-type]
    else:
        rev_yoy = yoy(earnings, "revenue")
        rev_qoq = qoq(earnings, "revenue")
        eps_yoy = yoy(earnings, "eps")
        eps_qoq = qoq(earnings, "eps")

    # EPS uses 2-decimal formatting
    eps_val = chosen_row.get("eps")
    try:
        latest_eps_str = f"${float(eps_val):.2f}" if eps_val is not None else "N/A"
    except (TypeError, ValueError):
        latest_eps_str = "N/A"

    table = render_table(
        earnings,
        [
            ("Quarter", "quarter", "str"),
            ("EPS", "eps", "money_2"),
            ("Revenue", "revenue", "money"),
            ("Net Income", "net_income", "money"),
        ],
        limit=6,
    )

    drivers = llm_output.get("key_drivers") or []
    if drivers:
        driver_lines = "\n".join(f"- {d}" for d in drivers if isinstance(d, str))
        key_drivers_section = f"### Key Drivers\n{driver_lines}"
    else:
        key_drivers_section = ""

    return {
        "ticker": ticker,
        "headline": llm_output.get("headline", ""),
        "narrative": llm_output.get("narrative", ""),
        "key_drivers_section": key_drivers_section,
        "latest_quarter": chosen_row.get("quarter", "N/A"),
        "latest_revenue_str": money(chosen_row.get("revenue")),
        "latest_eps_str": latest_eps_str,
        "latest_ni_str": money(chosen_row.get("net_income")),
        "revenue_yoy_str": pct(rev_yoy),
        "revenue_qoq_str": pct(rev_qoq),
        "eps_yoy_str": pct(eps_yoy),
        "eps_qoq_str": pct(eps_qoq),
        "earnings_table": table,
        "quarter_warning": quarter_warning,
    }


# ─── 6. Context summary for the LLM prompt ─────────────────────────
def summarize_for_llm(earnings: list[dict], target_quarter: str | None = None) -> str:
    """Turn raw earnings rows into a qualitative bullet summary the LLM uses.

    If `target_quarter` is provided, anchor the summary to that row (with
    same-quarter YoY) so the narrative matches the numbers the template renders.
    """
    from question.templates.functions import yoy, qoq, yoy_at, qoq_at

    if not earnings:
        return "- No earnings data available."

    anchor = earnings[0]
    used_target = False
    if target_quarter:
        tnorm = target_quarter.upper().replace(" ", "")
        for r in earnings:
            q = str(r.get("quarter") or "").upper().replace(" ", "")
            if q == tnorm:
                anchor = r
                used_target = True
                break

    if used_target:
        rev_yoy = yoy_at(earnings, "revenue", target_quarter)  # type: ignore[arg-type]
        rev_qoq = qoq_at(earnings, "revenue", target_quarter)  # type: ignore[arg-type]
        eps_yoy = yoy_at(earnings, "eps", target_quarter)      # type: ignore[arg-type]
        eps_qoq = qoq_at(earnings, "eps", target_quarter)      # type: ignore[arg-type]
    else:
        rev_yoy = yoy(earnings, "revenue")
        rev_qoq = qoq(earnings, "revenue")
        eps_yoy = yoy(earnings, "eps")
        eps_qoq = qoq(earnings, "eps")

    def _direction(v: float | None, strong: float = 0.10) -> str:
        if v is None:
            return "data unavailable"
        if v > strong:
            return "strongly positive"
        if v > 0.02:
            return "modestly positive"
        if v >= -0.02:
            return "roughly flat"
        if v >= -strong:
            return "modestly negative"
        return "strongly negative"

    if used_target:
        focus_note = f"- Focus quarter: {anchor.get('quarter','N/A')} (explicitly requested by user)"
    elif target_quarter:
        focus_note = (
            f"- Focus quarter: {anchor.get('quarter','N/A')} "
            f"(user asked about {target_quarter} but it is NOT in the data; narrative MUST mention this mismatch)"
        )
    else:
        focus_note = f"- Focus quarter: {anchor.get('quarter','N/A')} (latest available)"

    lines = [
        focus_note,
        f"- Revenue direction: YoY {_direction(rev_yoy)}, QoQ {_direction(rev_qoq)}",
        f"- EPS direction: YoY {_direction(eps_yoy)}, QoQ {_direction(eps_qoq)}",
        f"- Number of quarters available: {len(earnings)}",
    ]
    return "\n".join(lines)
