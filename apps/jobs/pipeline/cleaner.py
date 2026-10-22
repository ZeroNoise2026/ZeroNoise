"""
pipeline/cleaner.py
Text cleaning: HTML tag removal, whitespace normalization, news and filing formatting.
Input: raw string (may contain HTML)
Output: plain text for RAG chunking
"""

import re
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Optional


# ── Base cleaning functions ─────────────────────────────────────

def strip_html(text: str) -> str:
    """Remove HTML tags, keeping plain text. Convert <br> to newlines."""
    soup = BeautifulSoup(text, "html.parser")
    # Remove unwanted blocks
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    # Convert <br> to newlines
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return soup.get_text(separator=" ")


def normalize_whitespace(text: str) -> str:
    """Merge excess whitespace and newlines, trim leading/trailing whitespace."""
    text = re.sub(r'[ \t]+', ' ', text)          # merge multiple spaces/tabs into one
    text = re.sub(r'\n{3,}', '\n\n', text)       # collapse 3+ newlines to 2
    text = re.sub(r' \n', '\n', text)             # remove spaces before newlines
    return text.strip()


def format_timestamp(unix_ts: Optional[int]) -> str:
    """Convert Unix timestamp to ISO date string for document metadata."""
    if not unix_ts:
        return datetime.now().strftime("%Y-%m-%d")
    return datetime.utcfromtimestamp(unix_ts).strftime("%Y-%m-%d")


# ── Business cleaning functions ─────────────────────────────────────

def clean_news_article(article: dict) -> dict:
    """Clean a Finnhub news item, returning a metadata dict for the pipeline.

    Returns:
        {
            "content": str,    # headline + summary merged as plain text
            "title":   str,    # original headline
            "date":    str,    # ISO date
            "source":  str,    # Finnhub / SeekingAlpha etc.
            "url":     str,
        }
    """
    headline = article.get("headline", "")
    summary  = article.get("summary", "")
    source   = article.get("source", "")
    url      = article.get("url", "")
    ts       = article.get("datetime")

    # Concatenate headline + summary; together they form the raw material for a RAG chunk
    raw = f"{headline}. {summary}" if summary else headline
    content = normalize_whitespace(strip_html(raw))

    return {
        "content": content,
        "title":   headline,
        "date":    format_timestamp(ts),
        "source":  source,
        "url":     url,
    }


def clean_filing_text(raw_html: str) -> str:
    """Clean SEC EDGAR filing text (HTML → plain text).

    Financial filings typically have extensive HTML table tags wrapping numbers.
    BeautifulSoup correctly extracts the table content.
    """
    text = strip_html(raw_html)
    text = normalize_whitespace(text)
    return text


def clean_xbrl_value(value: float, unit: str = "USD") -> str:
    """Convert an XBRL numeric value to a human-readable string (for earnings table storage).

    Examples:
        1_234_567_890 USD  →  "$1.23B"
        23_400_000 USD     →  "$23.4M"
        450_000 USD        →  "$450K"
    """
    if unit != "USD":
        return str(value)
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 1e9:
        return f"{sign}${abs_val/1e9:.2f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val/1e6:.1f}M"
    if abs_val >= 1e3:
        return f"{sign}${abs_val/1e3:.0f}K"
    return f"{sign}${abs_val:.0f}"
