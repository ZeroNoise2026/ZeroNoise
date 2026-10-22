-- scripts/setup_ticker_queries.sql
-- Query log for untracked (Tier 2) tickers — drives hot-ticker auto-promotion.
--
-- Lives in Postgres rather than process memory so every question-service
-- instance (Cloud Run scales horizontally) counts toward the same threshold.
--
-- Usage: Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS ticker_queries (
    id          BIGSERIAL PRIMARY KEY,
    ticker      VARCHAR(10) NOT NULL,
    queried_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Promotion check: count by ticker over the last 24h
CREATE INDEX IF NOT EXISTS idx_ticker_queries_ticker_time
    ON ticker_queries (ticker, queried_at DESC);
