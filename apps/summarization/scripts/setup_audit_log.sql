-- scripts/setup_audit_log.sql
-- API audit log table — one row per logical operation (approach B)
--
-- Usage: Run this in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS api_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- WHO
    service      TEXT NOT NULL,          -- 'summary' | 'question' | 'data-pipeline'

    -- WHAT (comma-separated if multiple APIs in one operation, e.g. 'finnhub,fmp')
    api_type     TEXT NOT NULL,          -- 'claude_llm' (legacy: 'kimi_llm') | 'embedding' | 'finnhub' | 'fmp' | 'finnhub,fmp'
    endpoint     TEXT,                   -- 'chat.completions' | '/api/finnhub/news/AAPL'
    model        TEXT,                   -- 'claude-haiku-5-5' | NULL for non-LLM

    -- LLM metrics (NULL for non-LLM calls)
    tokens_in    INT,
    tokens_out   INT,
    tokens_total INT,

    -- Performance
    latency_ms   INT,
    status       TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'error' | 'timeout' | 'rate_limited'
    error_msg    TEXT
);

-- Dashboard queries: recent first
CREATE INDEX idx_audit_created ON api_audit_log (created_at DESC);

-- Filter by service + api type
CREATE INDEX idx_audit_service_api ON api_audit_log (service, api_type);
