-- Migration 003: persist citation sources alongside chat messages.
-- Prerequisite: 001a (chat_messages exists).
--
-- The question-service emits a `sources` SSE event after the answer stream
-- finishes. We capture that list and store it on the assistant message so
-- the citation block survives a page refresh.
--
-- Shape (JSONB array):
--   [
--     {"id":"doc:abc12...","doc_type":"news","ticker":"AAPL",
--      "date":"2026-05-08","title":"Apple beats Q2","url":"https://...",
--      "label":null,"similarity":0.81},
--     {"id":"table:earnings:AAPL:Q4 2025","doc_type":"earnings_table",...}
--   ]
--
-- We keep it as JSONB (not a separate normalized table) because:
--   - Sources are write-once with the message; no independent updates
--   - We never query inside the array — it's display payload
--   - JSONB is cheap to store NULL/empty for user messages

ALTER TABLE chat_messages
  ADD COLUMN IF NOT EXISTS sources JSONB;
