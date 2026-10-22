# generate_report

Generate a comprehensive investment-analysis report for one or more tickers
and save it as a Markdown file under `Summarization/output/`.

## When to use

Trigger this skill when the user asks for any of:

- "give me a report on AAPL"
- "deep analysis of MSFT"
- "show me NVDA's fundamentals"
- Any request for a *standalone, written* analysis of a single ticker
  (multi-ticker comparisons go to `compare_tickers`, not this one).

Note: the router LLM handles both English and Chinese inputs naturally;
non-English phrasings asking for a ticker report route here too.

Do **not** use when:

- The user just wants a quick price/news lookup → answer from RAG instead.
- The user wants to compare 2+ tickers → use `compare_tickers`.
- The user asks about a *specific* SEC filing → use `explain_filing` (future).

## Inputs

- `ticker` (string, required): single ticker symbol, e.g. "AAPL".
- `dry_run` (bool, optional, default false): fetch and assemble context but
  skip the LLM call. Useful for testing.

## Output

- `content`: the full Markdown report.
- `artifacts`: `[Path(Summarization/output/{TICKER}_{YYYY-MM-DD}.md)]`.
- `meta`: `{doc_counts, total_chars, cache_hit, input_hash}`.
