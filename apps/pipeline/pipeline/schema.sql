-- QuantAgent Database Schema
-- PostgreSQL + pgvector on Supabase
-- Run via: python -m pipeline.init_db

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Documents table (semantic search target)
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    embedding   VECTOR(384),
    ticker      VARCHAR(10),
    date        VARCHAR(10),          -- YYYY-MM-DD
    source      VARCHAR(30),          -- finnhub | fmp | edgar
    doc_type    VARCHAR(20),          -- news | 10-K | 10-Q | earnings | regulatory
    section     VARCHAR(50),
    title       TEXT,
    url         TEXT,                 -- canonical link to original source (news only currently — NULL for filings/earnings)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Idempotent ALTER for existing deployments (ignored if column already exists).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS url TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_documents_ticker   ON documents (ticker);
CREATE INDEX IF NOT EXISTS idx_documents_date     ON documents (date);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_ticker_date ON documents (ticker, date);

-- 3. Earnings table (structured financial data)
CREATE TABLE IF NOT EXISTS earnings (
    ticker      VARCHAR(10) NOT NULL,
    quarter     VARCHAR(20) NOT NULL,
    date        VARCHAR(10),
    eps         DOUBLE PRECISION,
    revenue     BIGINT,
    net_income  BIGINT,
    guidance    TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, quarter)
);

CREATE INDEX IF NOT EXISTS idx_earnings_ticker ON earnings (ticker);

-- 4. Price snapshot table
CREATE TABLE IF NOT EXISTS price_snapshot (
    ticker      VARCHAR(10) NOT NULL,
    date        VARCHAR(10) NOT NULL,
    close_price DOUBLE PRECISION,
    pe_ratio    DOUBLE PRECISION,
    market_cap  BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_price_ticker ON price_snapshot (ticker);

-- 5. Tracked tickers (pipeline processes these every 6h)
CREATE TABLE IF NOT EXISTS tracked_tickers (
    ticker              VARCHAR(10) PRIMARY KEY,
    ticker_type         TEXT        DEFAULT 'stock',     -- stock | etf
    follower_count      INTEGER     DEFAULT 0,
    is_active           BOOLEAN     DEFAULT TRUE,
    last_news_fetch     TIMESTAMPTZ,
    last_filing_fetch   TIMESTAMPTZ,
    last_successful_run TIMESTAMPTZ
);

-- 6. User watchlist (linked to Supabase Auth)
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id     UUID        NOT NULL,
    ticker      VARCHAR(10) NOT NULL,
    added_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, ticker)
);

-- 7. Semantic search helper function (used by Person B's retriever)
CREATE OR REPLACE FUNCTION search_documents(
    query_embedding VECTOR(384),
    match_ticker    TEXT    DEFAULT NULL,
    match_doc_type  TEXT    DEFAULT NULL,
    match_count     INT     DEFAULT 5
)
RETURNS TABLE (
    id          TEXT,
    content     TEXT,
    ticker      VARCHAR(10),
    date        VARCHAR(10),
    source      VARCHAR(30),
    doc_type    VARCHAR(20),
    title       TEXT,
    similarity  FLOAT
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.id, d.content, d.ticker, d.date, d.source, d.doc_type, d.title,
        1 - (d.embedding <=> query_embedding) AS similarity
    FROM documents d
    WHERE
        (match_ticker   IS NULL OR d.ticker   = match_ticker)
        AND (match_doc_type IS NULL OR d.doc_type = match_doc_type)
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 8. User preferences (timezone for daily briefings)
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     UUID        PRIMARY KEY,
    timezone    TEXT        DEFAULT 'America/New_York',
    briefing_enabled BOOLEAN DEFAULT TRUE,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 9. Daily briefings (generated per user per day)
CREATE TABLE IF NOT EXISTS daily_briefings (
    user_id         UUID        NOT NULL,
    briefing_date   VARCHAR(10) NOT NULL,   -- YYYY-MM-DD in user's local timezone
    content         TEXT        NOT NULL,
    tickers         TEXT[],                 -- array of tickers included
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, briefing_date)
);

CREATE INDEX IF NOT EXISTS idx_briefings_user ON daily_briefings (user_id);

-- 10. Summary cache (Summarization service — input-hash-based dedup, 90-day retention)
CREATE TABLE IF NOT EXISTS summary_cache (
    ticker          VARCHAR(10) NOT NULL,
    input_hash      CHAR(64)    NOT NULL,    -- SHA256 over source_doc_ids + model + prompt_version
    summary_date    DATE        NOT NULL,
    content         TEXT        NOT NULL,
    model           VARCHAR(50) NOT NULL,
    prompt_version  VARCHAR(20) NOT NULL,
    tokens_in       INT,
    tokens_out      INT,
    source_doc_ids  TEXT[],                  -- lineage: which inputs produced this summary
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ticker, input_hash)
);

-- Most-recent-summary lookups
CREATE INDEX IF NOT EXISTS idx_summary_cache_ticker_created
    ON summary_cache (ticker, created_at DESC);

-- Cleanup job (DELETE WHERE created_at < NOW() - INTERVAL '90 days')
CREATE INDEX IF NOT EXISTS idx_summary_cache_created
    ON summary_cache (created_at);

-- 11. Seed initial tracked tickers
INSERT INTO tracked_tickers (ticker, ticker_type) VALUES
    ('AAPL',  'stock'),
    ('MSFT',  'stock'),
    ('NVDA',  'stock'),
    ('GOOGL', 'stock'),
    ('AMZN',  'stock'),
    ('META',  'stock'),
    ('TSLA',  'stock'),
    ('VOO',   'etf'),
    ('SPY',   'etf'),
    ('QQQ',   'etf')
ON CONFLICT (ticker) DO NOTHING;
