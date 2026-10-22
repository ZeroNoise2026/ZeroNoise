-- 003_user_files.sql
-- Phase 2: user-uploaded files. Binary lives in Supabase Storage; this table
-- only holds metadata + a pointer (`storage_path`) and parser output (`parsed_meta`).
--
-- Run AFTER 002_auth_uuid_rls.sql (depends on auth.users + chat_sessions UUIDs).

CREATE TABLE IF NOT EXISTS user_files (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id    text        REFERENCES chat_sessions(id) ON DELETE SET NULL,
    filename      text        NOT NULL,
    mime_type     text        NOT NULL,
    size_bytes    bigint      NOT NULL CHECK (size_bytes > 0),
    storage_path  text        NOT NULL UNIQUE,
    parsed_meta   jsonb,       -- {kind, sheets|rows|page_count|..., error?}
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_files_user_created
    ON user_files (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_files_session
    ON user_files (session_id) WHERE session_id IS NOT NULL;

ALTER TABLE user_files ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_files_select_own ON user_files;
CREATE POLICY user_files_select_own ON user_files
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS user_files_insert_own ON user_files;
CREATE POLICY user_files_insert_own ON user_files
    FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS user_files_update_own ON user_files;
CREATE POLICY user_files_update_own ON user_files
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS user_files_delete_own ON user_files;
CREATE POLICY user_files_delete_own ON user_files
    FOR DELETE USING (auth.uid() = user_id);

-- Storage bucket policies are configured separately in the Supabase dashboard
-- (Storage → Policies on the `user-files` bucket). See backend/files/storage.py
-- for the path convention: <user_id>/<file_id>.<ext>
