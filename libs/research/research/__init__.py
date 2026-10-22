"""
research — ticker research shared by the apps and skills.

  research.data         read-only queries: documents, earnings, price_snapshot
  research.formatters   rows -> prompt-ready text
  research.context      TickerContext + fetch_context()
  research.cache        summary_cache lookups keyed on input_hash
  research.report       build_report(): context -> cache or Claude -> Markdown

Configuration (environment): MAX_CONTEXT_CHARS, LLM_MODEL_SUMMARY,
LLM_EFFORT_SUMMARY, plus whatever common needs.
"""
