-- Migration 001a: tables only (safe to run without pg_cron).
-- Run this first. Then enable pg_cron in Dashboard → Database → Extensions,
-- then run 001b_cron_cleanup.sql.

-- ═══════════════════════════════════════════════════════════
-- Chat sessions + messages (schema B)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS chat_sessions (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  title           TEXT,                 -- first 40 chars of first user msg
  tickers         TEXT[] DEFAULT '{}',
  created_at      TIMESTAMPTZ DEFAULT now(),
  last_message_at TIMESTAMPTZ DEFAULT now(),
  message_count   INT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_time
  ON chat_sessions (user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
  id          BIGSERIAL PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content     TEXT NOT NULL,
  tickers     TEXT[] DEFAULT '{}',
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_time
  ON chat_messages (session_id, created_at ASC);
