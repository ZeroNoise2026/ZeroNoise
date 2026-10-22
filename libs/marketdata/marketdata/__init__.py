"""
marketdata — clients for the external market-data APIs.

  marketdata.finnhub.FinnhubClient   news, earnings surprises, basic financials
  marketdata.fmp.FMPClient           real-time quotes, financial statements
  marketdata.edgar.EdgarClient       SEC filings and XBRL facts
  marketdata.retry.with_retry        backoff on 429 / 5xx / network errors

Used by apps/jobs (batch ingestion) and apps/api (live lookups for
untracked tickers). Keys come from the environment at construction time:
FINNHUB_API_KEY, FMP_API_KEY, EDGAR_USER_AGENT.
"""
