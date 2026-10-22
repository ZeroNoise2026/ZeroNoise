-- match_documents: pgvector cosine similarity search
-- Run this in Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor)
--
-- How it works:
--   1. Takes a 384-dim query vector (from embedding-service)
--   2. Uses pgvector's <=> operator (cosine distance) to find similar documents
--   3. Filters by optional ticker and doc_type
--   4. Returns top-K results with similarity scores
--
-- Prerequisites:
--   - pgvector extension enabled: CREATE EXTENSION IF NOT EXISTS vector;
--   - documents table has: embedding VECTOR(384) column
--   - An index for fast search (created below)

-- Step 1: Enable pgvector (if not already done)
CREATE EXTENSION IF NOT EXISTS vector;

-- Step 2: Create the search function
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(384),
    match_count INT DEFAULT 8,
    similarity_threshold FLOAT DEFAULT 0.3,
    filter_ticker TEXT DEFAULT NULL,
    filter_doc_type TEXT DEFAULT NULL
)
RETURNS TABLE (
    id TEXT,
    content TEXT,
    ticker TEXT,
    date TEXT,
    source TEXT,
    doc_type TEXT,
    section TEXT,
    title TEXT,
    url TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.id,
        d.content,
        d.ticker,
        d.date::TEXT,
        d.source,
        d.doc_type,
        d.section,
        d.title,
        d.url,
        -- cosine similarity = 1 - cosine distance
        -- pgvector <=> operator returns cosine distance (0 = identical, 2 = opposite)
        (1 - (d.embedding <=> query_embedding))::FLOAT AS similarity
    FROM documents d
    WHERE
        -- Only search docs that have embeddings
        d.embedding IS NOT NULL
        -- Optional ticker filter
        AND (filter_ticker IS NULL OR d.ticker = filter_ticker)
        -- Optional doc_type filter
        AND (filter_doc_type IS NULL OR d.doc_type = filter_doc_type)
        -- Similarity threshold (cosine distance < 1 - threshold)
        AND (1 - (d.embedding <=> query_embedding)) >= similarity_threshold
    ORDER BY d.embedding <=> query_embedding  -- ascending distance = descending similarity
    LIMIT match_count;
END;
$$;

-- Step 3: Create an index for faster search (ivfflat)
-- lists = sqrt(num_rows) is a good starting point
-- With ~2200 docs, lists=47 is reasonable; adjust as data grows
-- NOTE: You need at least as many rows as lists to create the index
CREATE INDEX IF NOT EXISTS idx_documents_embedding
ON documents
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 50);

-- Step 4 (optional): Add last_queried column to tracked_tickers for demotion
ALTER TABLE tracked_tickers
ADD COLUMN IF NOT EXISTS last_queried TIMESTAMPTZ;
