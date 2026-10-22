"""
evals/checks.py

Deterministic structural checks. Zero cost, sub-millisecond, and they run
BEFORE the LLM judge — a summary that already violates the structural spec
does not deserve judge tokens spent on it.

These checks are not arbitrary. They encode the promises that
`summarizer.SUMMARY_SYSTEM_PROMPT` already makes to its caller:

    "STRICT 300-word limit"
    "Well-structured, using Markdown format"
    "All data citations must be specific ... never fabricate data"
    "If a category of data is missing, skip it - do not invent information"

A promise in a prompt is a spec. This module is that spec, made executable.

Public API
----------
    run_checks(summary, fixture) -> {"passed": bool, "violations": [str]}
"""

from __future__ import annotations

import re
from typing import Any

MAX_WORDS = 300

# ─────────────────────────────────────────────────────────────
# 1. Word count
# ─────────────────────────────────────────────────────────────
# Counting words in markdown needs a defensible definition, because
# "the model blew the 300-word limit" is a headline finding and the first
# thing anyone will challenge is how you counted.
#
# Rule: strip code spans and markdown *syntax* characters, then count
# whitespace-separated tokens that contain at least one alphanumeric
# character. So "$1,284.50" counts as one word; a table's "|" and "---"
# separators count as nothing. Table *content* is deliberately still
# counted — dumping numbers into a table is not a way to dodge the limit.

_CODE_SPAN_RE = re.compile(r"`{1,3}[^`]*`{1,3}", re.DOTALL)
_MD_SYNTAX_RE = re.compile(r"[*_`>#|~\[\]()]+")


def count_words(markdown: str) -> int:
    """Markdown-aware word count. See module docstring for the rule."""
    text = _CODE_SPAN_RE.sub(" ", markdown or "")
    text = _MD_SYNTAX_RE.sub(" ", text)
    return sum(1 for tok in text.split() if any(ch.isalnum() for ch in tok))


# ─────────────────────────────────────────────────────────────
# 2. Markdown header presence
# ─────────────────────────────────────────────────────────────
# The spec names `##` and `###` specifically, so that is what we accept.
# Up to three leading spaces is still a valid ATX heading in CommonMark.
_HEADER_RE = re.compile(r"^ {0,3}#{2,3}[ \t]+\S", re.MULTILINE)


def has_markdown_header(markdown: str) -> bool:
    return bool(_HEADER_RE.search(markdown or ""))


# ─────────────────────────────────────────────────────────────
# 3. No fabricated financials when the fixture has no earnings data
# ─────────────────────────────────────────────────────────────
# This is the check that actually needs care.
#
# The naive implementation — "flag any $X.XB or XX.X% when earnings_text is
# empty" — is WRONG, and the provided fixtures prove it. FX2's price_text is:
#
#     2026-07-30 close: $94.20 (-1.3%). 5-day range: $92.10 - $99.45.
#     30-day change: -4.2%.
#
# A correct, faithful summary of that fixture will say "closed at $94.20,
# down 1.3%" — and a bare currency/percentage regex flags it as fabrication.
# The check would fail exactly the summaries it is supposed to pass.
#
# So we anchor on FINANCIAL-STATEMENT SEMANTICS, not on the presence of a
# number: a violation requires a metric term (EPS, revenue, gross margin...)
# *bound to* a figure by proximity and direction. Price, market cap and P/E
# are explicitly excluded, because those are legitimately derivable from
# price_text.

_METRIC_TERMS = [
    "earnings per share",
    "eps",
    "revenue",
    "revenues",
    "net income",
    "operating income",
    "gross profit",
    "net profit",
    "gross margin",
    "operating margin",
    "profit margin",
    "net margin",
    "ebitda",
    "free cash flow",
    "operating cash flow",
    "top line",
    "bottom line",
    "earnings",
]

# Terms that legitimately carry figures sourced from price_text. If a metric
# match is really part of one of these phrases, it is not a violation.
_PRICE_CONTEXT_TERMS = [
    "market cap",
    "market capitalization",
    "p/e",
    "pe ratio",
    "price-to-earnings",
    "price to earnings",
    "share price",
    "stock price",
    "closing price",
    "earnings date",
    "earnings call",
    "earnings report",
    "earnings season",
]

# $1.2B / $48,200 / $6.12 / 31% / 3.9 %
_FIGURE_RE = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?\s?(?:[KMBT]|thousand|million|billion|trillion)?\b"
    r"|\b\d+(?:\.\d+)?\s?%)",
    re.IGNORECASE,
)

_METRIC_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in sorted(_METRIC_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# How far a figure may sit from a metric term and still count as "bound" to it.
_FORWARD_WINDOW = 40   # "revenue of $48.2B", "EPS $6.12", "revenue rose 31%"
_BACKWARD_WINDOW = 30  # "$48.2B in revenue", "31% growth in revenue"

_BACKWARD_LINK_RE = re.compile(r"^\s*(?:in|of|for|on)\s+$", re.IGNORECASE)


def _is_price_context(text: str, start: int, end: int) -> bool:
    """True when the metric match is really part of a price/valuation phrase."""
    window = text[max(0, start - 25): end + 25].lower()
    return any(term in window for term in _PRICE_CONTEXT_TERMS)


def find_financial_figures(summary: str) -> list[str]:
    """Return snippets where a financial-statement metric is bound to a figure.

    Empty list means the summary talks about no such metric with a number,
    which is what we require when the fixture supplied no earnings data.
    """
    text = summary or ""
    hits: list[str] = []
    seen: set[tuple[int, int]] = set()

    for m in _METRIC_RE.finditer(text):
        if _is_price_context(text, m.start(), m.end()):
            continue

        # Forward: metric ... figure
        ahead = text[m.end(): m.end() + _FORWARD_WINDOW]
        fm = _FIGURE_RE.search(ahead)
        bound = fm is not None

        # Backward: figure (in|of|for) metric
        if not bound:
            behind_start = max(0, m.start() - _BACKWARD_WINDOW)
            behind = text[behind_start: m.start()]
            for bm in _FIGURE_RE.finditer(behind):
                between = behind[bm.end():]
                if _BACKWARD_LINK_RE.match(between) or between.strip() == "":
                    bound = True
                    break

        if not bound:
            continue

        lo = max(0, m.start() - _BACKWARD_WINDOW)
        hi = min(len(text), m.end() + _FORWARD_WINDOW)
        # Snap outward to whitespace so the reported snippet is readable.
        # These violations are meant to be audited by a human; a snippet that
        # starts mid-word ("mary ### Financial highlights Revenu") makes a
        # false positive impossible to eyeball.
        while lo > 0 and not text[lo - 1].isspace():
            lo -= 1
        while hi < len(text) and not text[hi].isspace():
            hi += 1
        span = (lo, hi)
        if span in seen:
            continue
        seen.add(span)
        snippet = " ".join(text[lo:hi].split())
        hits.append(snippet)

    return hits


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
def run_checks(summary: str, fixture: dict[str, Any]) -> dict[str, Any]:
    """Run every deterministic check against one generated summary.

    Args:
        summary: the markdown returned by summarizer.generate_summary().
        fixture: the raw fixture record. Only the context fields are read;
            `_trap` is never touched — the checks must stay general, or they
            stop being an eval and become an answer key.

    Returns:
        {"passed": bool, "violations": [str]}
    """
    violations: list[str] = []

    words = count_words(summary)
    if words > MAX_WORDS:
        violations.append(
            f"word-count: {words} words exceeds the {MAX_WORDS}-word limit "
            f"promised by SUMMARY_SYSTEM_PROMPT (over by {words - MAX_WORDS})"
        )

    if not has_markdown_header(summary):
        violations.append(
            "structure: no markdown header (`##` or `###`) found; the prompt "
            "asks for a well-structured markdown document"
        )

    if not (fixture.get("earnings_text") or "").strip():
        for snippet in find_financial_figures(summary):
            violations.append(
                f"fabricated-financials: fixture supplied no earnings_text, but "
                f"the summary states a financial-statement figure -> \"{snippet}\""
            )

    return {"passed": not violations, "violations": violations}
