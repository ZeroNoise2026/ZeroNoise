"""
question/schemas/price_query.py
PRICE_QUERY — zero-LLM path. All data is pulled from live_fetcher + DB; the template renders directly.

No SCHEMA / SYSTEM_PROMPT / FEW_SHOT needed — no LLM involvement.
"""
from __future__ import annotations

# PRICE_QUERY template — all fields are filled with "N/A" or strings in build_context, so no {% if %} needed.
TEMPLATE = """## {{ ticker }}
**{{ name }}**  
**Price:** {{ price_str }}  
**Change:** {{ change_str }}  
**Day Range:** {{ day_range_str }}  
**52W Range:** {{ year_range_str }}  
**P/E Ratio:** {{ pe_str }}  
**Market Cap:** {{ market_cap_str }}  
**Volume:** {{ volume_str }}  
**50D Avg:** {{ avg50_str }}  
**200D Avg:** {{ avg200_str }}  

{{ earnings_section }}"""


def _fmt_price(p):
    if p is None:
        return "N/A"
    try:
        return f"${float(p):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_change(c):
    if c is None:
        return "N/A"
    try:
        v = float(c)
        arrow = "🟢" if v >= 0 else "🔴"
        return f"{arrow} {v:+.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_range(lo, hi):
    if lo is None or hi is None:
        return "N/A"
    try:
        return f"${float(lo):,.2f} – ${float(hi):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pe(pe):
    if pe is None:
        return "N/A"
    try:
        return f"{float(pe):.1f}"
    except (TypeError, ValueError):
        return "N/A"


def build_context(
    *,
    ticker: str,
    live: dict | None,
    earnings: list[dict] | None,
    snapshots: list[dict] | None,
) -> dict:
    """Build the context for render(). All potentially-missing fields are filled with placeholders to ensure template rendering does not fail.

    Args:
        ticker: uppercase symbol
        live: live quote dict (FMP response structure) or None
        earnings: result of get_earnings(ticker), newest first
        snapshots: get_price_snapshots(ticker) fallback

    Returns:
        dict fed directly into templates.render(TEMPLATE, ctx)
    """
    from question.templates.functions import render_table

    live = live or {}
    earnings = earnings or []
    snapshots = snapshots or []

    # 1. price / name / basic fields
    price = live.get("price")
    name = live.get("name", ticker)

    # 2. TTM P/E — if live didn't provide it, compute from the most recent 4 quarters of EPS
    pe = live.get("pe")
    if pe is None and price and earnings:
        eps_vals = [float(e["eps"]) for e in earnings[:4] if e.get("eps") is not None]
        if eps_vals and sum(eps_vals) > 0:
            pe = float(price) / sum(eps_vals)

    # 3. If live is missing price, fallback to the DB snapshot
    if price is None and snapshots:
        s = snapshots[0]
        price = s.get("close_price")
        if pe is None:
            pe = s.get("pe_ratio")

    # 4. earnings table (markdown)
    earnings_section = ""
    if earnings:
        tbl = render_table(
            earnings,
            [
                ("Quarter", "quarter", "str"),
                ("EPS", "eps", "money_2"),
                ("Revenue", "revenue", "money"),
                ("Net Income", "net_income", "money"),
            ],
            limit=4,
        )
        earnings_section = f"### Recent Earnings\n{tbl}"

    return {
        "ticker": ticker,
        "name": name,
        "price_str": _fmt_price(price),
        "change_str": _fmt_change(live.get("changePercentage")),
        "day_range_str": _fmt_range(live.get("dayLow"), live.get("dayHigh")),
        "year_range_str": _fmt_range(live.get("yearLow"), live.get("yearHigh")),
        "pe_str": _fmt_pe(pe),
        "market_cap_str": _fmt_money_or_na(live.get("marketCap")),
        "volume_str": _fmt_compact_or_na(live.get("volume")),
        "avg50_str": _fmt_price(live.get("priceAvg50")) if live.get("priceAvg50") is not None else "N/A",
        "avg200_str": _fmt_price(live.get("priceAvg200")) if live.get("priceAvg200") is not None else "N/A",
        "earnings_section": earnings_section,
    }


def _fmt_money_or_na(v):
    from question.templates.filters import money
    return money(v) if v is not None else "N/A"


def _fmt_compact_or_na(v):
    from question.templates.filters import compact
    return compact(v) if v is not None else "N/A"
