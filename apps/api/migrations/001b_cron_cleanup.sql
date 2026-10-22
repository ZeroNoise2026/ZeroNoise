-- Migration 001b: 1-year cleanup cron jobs.
-- Prerequisite: pg_cron extension enabled.
--   Supabase Dashboard → Database → Extensions → search "pg_cron" → toggle ON
-- Then run this file in the SQL Editor.

-- Safety check: error out early with a clear message if pg_cron missing.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    RAISE EXCEPTION 'pg_cron not enabled. Enable it in Dashboard → Database → Extensions first.';
  END IF;
END $$;

-- ── daily_briefings: drop >1yr old ─────────────────────────
SELECT cron.schedule(
  'cleanup-daily-briefings-1yr',
  '15 3 * * *',  -- daily 03:15 UTC
  $$
    DELETE FROM daily_briefings
    WHERE briefing_date < (CURRENT_DATE - INTERVAL '1 year');
  $$
);

-- ── chat_sessions: drop sessions inactive >1yr (CASCADE drops messages)
SELECT cron.schedule(
  'cleanup-chat-sessions-1yr',
  '30 3 * * *',  -- daily 03:30 UTC
  $$
    DELETE FROM chat_sessions
    WHERE last_message_at < (now() - INTERVAL '1 year');
  $$
);

-- Sanity check:
-- SELECT jobname, schedule, active FROM cron.job WHERE jobname LIKE 'cleanup-%';
