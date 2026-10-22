"""
common — infrastructure shared by every Python app in the monorepo.

  common.supabase   lazy Supabase client
  common.llm        Claude client (chat, chat_stream) with retries and audit
  common.audit      fire-and-forget rows in api_audit_log

Configuration comes from the environment (each app loads its own .env):
SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY.
"""
