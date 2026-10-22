-- FIX: documents.date is DATE type in PostgreSQL, but function declared TEXT return
-- Solution: cast d.date::TEXT
-- Just run this single statement — it replaces the existing function

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
        (1 - (d.embedding <=> query_embedding))::FLOAT AS similarity
    FROM documents d
    WHERE
        d.embedding IS NOT NULL
        AND (filter_ticker IS NULL OR d.ticker = filter_ticker)
        AND (filter_doc_type IS NULL OR d.doc_type = filter_doc_type)
        AND (1 - (d.embedding <=> query_embedding)) >= similarity_threshold
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
