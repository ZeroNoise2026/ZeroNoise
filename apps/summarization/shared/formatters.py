"""
shared/formatters.py
Data formatting functions — shared by summary and question services.
Extracted from: summary/fetcher.py (Person B)
"""


def format_news(docs: list[dict]) -> str:
    """Format a list of news documents into human-readable text."""
    if not docs:
        return ""
    lines = []
    for d in docs:
        date = d.get("date", "N/A")
        title = d.get("title") or ""
        content = d.get("content", "")
        header = f"[{date}] {title}".strip() if title else f"[{date}]"
        lines.append(f"{header}\n{content}")
    return "\n\n---\n\n".join(lines)


def format_regulatory(docs: list[dict]) -> str:
    """Format regulatory / compliance news documents into human-readable text."""
    if not docs:
        return ""
    lines = []
    for d in docs:
        date = d.get("date", "N/A")
        title = d.get("title") or ""
        source = d.get("source", "")
        content = d.get("content", "")
        header = f"[{date}] {title}".strip() if title else f"[{date}]"
        if source:
            header += f" (via {source})"
        lines.append(f"{header}\n{content}")
    return "\n\n---\n\n".join(lines)


def format_filings(docs: list[dict]) -> str:
    """Format SEC filings (10-K/10-Q) documents into human-readable text."""
    if not docs:
        return ""
    lines = []
    for d in docs:
        date = d.get("date", "N/A")
        doc_type = d.get("doc_type", "filing")
        source = d.get("source", "")
        section = d.get("section") or ""
        content = d.get("content", "")
        header = f"[{date}] {doc_type} ({source})"
        if section:
            header += f" - {section}"
        lines.append(f"{header}\n{content}")
    return "\n\n---\n\n".join(lines)


def format_earnings(rows: list[dict]) -> str:
    """Format earnings table data into a Markdown table."""
    if not rows:
        return ""
    lines = ["Quarter | Date | EPS | Revenue | Net Income | Guidance"]
    lines.append("---|---|---|---|---|---")
    for r in rows:
        eps = f"{r['eps']:.2f}" if r.get("eps") is not None else "N/A"
        rev = f"${r['revenue']:,}" if r.get("revenue") is not None else "N/A"
        ni = f"${r['net_income']:,}" if r.get("net_income") is not None else "N/A"
        guidance = r.get("guidance") or "N/A"
        lines.append(f"{r['quarter']} | {r.get('date', 'N/A')} | {eps} | {rev} | {ni} | {guidance}")
    return "\n".join(lines)


def format_prices(rows: list[dict]) -> str:
    """Format price_snapshot table data into a Markdown table."""
    if not rows:
        return ""
    lines = ["Date | Close | P/E | Market Cap"]
    lines.append("---|---|---|---")
    for r in rows:
        close = f"${r['close_price']:.2f}" if r.get("close_price") is not None else "N/A"
        pe = f"{r['pe_ratio']:.1f}" if r.get("pe_ratio") is not None else "N/A"
        mc = f"${r['market_cap']:,}" if r.get("market_cap") is not None else "N/A"
        lines.append(f"{r.get('date', 'N/A')} | {close} | {pe} | {mc}")
    return "\n".join(lines)
