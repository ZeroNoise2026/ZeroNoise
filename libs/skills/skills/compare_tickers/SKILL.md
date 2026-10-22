# compare_tickers

Side-by-side comparison of two or more tickers — financial metrics, recent
events, and a qualitative read.

## When to use

Trigger this skill when the user wants to **compare** multiple tickers:

- "compare AAPL and MSFT"
- "compare NVDA vs AMD"
- "AAPL GOOGL MSFT — which has the most stable fundamentals?"
- "AAPL MSFT NVDA which is the best buy"

Note: the router LLM handles non-English inputs naturally; equivalent
phrasings in other languages route here too.

Do **not** use when:

- The user only mentions one ticker → use `generate_report`.
- The user wants raw numbers, no analysis → answer from RAG / direct query.
- The user mentions multiple tickers in passing but is asking about
  something else (e.g. "did AAPL or MSFT report yesterday?") → RAG.

## Inputs

- `tickers` (array of strings, required, min 2): ticker symbols, e.g.
  `["AAPL", "MSFT"]`. Case-insensitive; will be uppercased.
- `focus` (string, optional): comparison angle, e.g. "valuation",
  "earnings growth", "recent catalysts". Free-form — passed into the
  prompt as a hint. Defaults to "overall fundamentals".

## Output

- `content`: a Markdown report with a metrics table + per-ticker
  observations + a final ranking/recommendation paragraph.
- `artifacts`: none (comparisons are ad-hoc; we don't persist them).
- `meta`: `{tickers, focus, ticker_chars: {ticker: int, ...}}`.

## Notes for routing

When extracting `tickers` from a free-text message, prefer all-caps
sequences of 1–5 letters that look like symbols. If the user only mentions
one ticker, do **not** pick this skill — pick `generate_report` instead.
