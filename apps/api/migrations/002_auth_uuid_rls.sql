-- Migration 002: switch user_id from TEXT to UUID, add FK to auth.users,
-- enable RLS as a defense-in-depth layer.
--
-- Prerequisites: 001a (tables) already run.
-- Side effects: TRUNCATEs user-scoped tables (dev data, agreed to drop).
--
-- Run in Supabase SQL Editor. Idempotent: safe to re-run.

BEGIN;

-- ═══════════════════════════════════════════════════════════
-- 1) Drop legacy rows (the old localStorage user_ids do not map
--    to auth.users.id, so they would orphan after the FK).
-- ═══════════════════════════════════════════════════════════
TRUNCATE TABLE chat_messages       CASCADE;
TRUNCATE TABLE chat_sessions       CASCADE;

-- These tables may not exist yet in every environment — guard each.
DO $$
BEGIN
  IF to_regclass('public.user_watchlist')   IS NOT NULL THEN TRUNCATE TABLE user_watchlist; END IF;
  IF to_regclass('public.user_preferences') IS NOT NULL THEN TRUNCATE TABLE user_preferences; END IF;
  IF to_regclass('public.daily_briefings')  IS NOT NULL THEN TRUNCATE TABLE daily_briefings; END IF;
END $$;

-- ═══════════════════════════════════════════════════════════
-- 2) Convert TEXT user_id → UUID, add FK to auth.users(id).
-- ═══════════════════════════════════════════════════════════

-- chat_sessions
ALTER TABLE chat_sessions
  ALTER COLUMN user_id TYPE UUID USING user_id::uuid;
ALTER TABLE chat_sessions
  DROP CONSTRAINT IF EXISTS chat_sessions_user_id_fkey;
ALTER TABLE chat_sessions
  ADD  CONSTRAINT chat_sessions_user_id_fkey
       FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

-- user_watchlist
DO $$
BEGIN
  IF to_regclass('public.user_watchlist') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE user_watchlist ALTER COLUMN user_id TYPE UUID USING user_id::uuid';
    EXECUTE 'ALTER TABLE user_watchlist DROP CONSTRAINT IF EXISTS user_watchlist_user_id_fkey';
    EXECUTE 'ALTER TABLE user_watchlist ADD CONSTRAINT user_watchlist_user_id_fkey
             FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE';
  END IF;
END $$;

-- user_preferences
DO $$
BEGIN
  IF to_regclass('public.user_preferences') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE user_preferences ALTER COLUMN user_id TYPE UUID USING user_id::uuid';
    EXECUTE 'ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS user_preferences_user_id_fkey';
    EXECUTE 'ALTER TABLE user_preferences ADD CONSTRAINT user_preferences_user_id_fkey
             FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE';
  END IF;
END $$;

-- daily_briefings
DO $$
BEGIN
  IF to_regclass('public.daily_briefings') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE daily_briefings ALTER COLUMN user_id TYPE UUID USING user_id::uuid';
    EXECUTE 'ALTER TABLE daily_briefings DROP CONSTRAINT IF EXISTS daily_briefings_user_id_fkey';
    EXECUTE 'ALTER TABLE daily_briefings ADD CONSTRAINT daily_briefings_user_id_fkey
             FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE';
  END IF;
END $$;

-- ═══════════════════════════════════════════════════════════
-- 3) Enable RLS. service_role bypasses RLS automatically, so
--    these policies only apply when a request uses an anon /
--    user-JWT key — a defense-in-depth layer for the future.
-- ═══════════════════════════════════════════════════════════
ALTER TABLE chat_sessions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages    ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "chat_sessions_owner" ON chat_sessions;
CREATE POLICY "chat_sessions_owner" ON chat_sessions
  FOR ALL TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "chat_messages_owner" ON chat_messages;
CREATE POLICY "chat_messages_owner" ON chat_messages
  FOR ALL TO authenticated
  USING (EXISTS (
    SELECT 1 FROM chat_sessions s
    WHERE s.id = chat_messages.session_id AND s.user_id = auth.uid()
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM chat_sessions s
    WHERE s.id = chat_messages.session_id AND s.user_id = auth.uid()
  ));

DO $$
BEGIN
  IF to_regclass('public.user_watchlist') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE user_watchlist ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS "user_watchlist_owner" ON user_watchlist';
    EXECUTE 'CREATE POLICY "user_watchlist_owner" ON user_watchlist
             FOR ALL TO authenticated
             USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)';
  END IF;

  IF to_regclass('public.user_preferences') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS "user_preferences_owner" ON user_preferences';
    EXECUTE 'CREATE POLICY "user_preferences_owner" ON user_preferences
             FOR ALL TO authenticated
             USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)';
  END IF;

  IF to_regclass('public.daily_briefings') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE daily_briefings ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS "daily_briefings_owner" ON daily_briefings';
    EXECUTE 'CREATE POLICY "daily_briefings_owner" ON daily_briefings
             FOR ALL TO authenticated
             USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)';
  END IF;
END $$;

COMMIT;

-- Sanity:
--   SELECT relname, relrowsecurity FROM pg_class WHERE relname IN
--     ('chat_sessions','chat_messages','user_watchlist','user_preferences','daily_briefings');
