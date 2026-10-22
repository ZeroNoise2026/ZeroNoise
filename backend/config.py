import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# JWT secret for verifying Supabase Auth access tokens (legacy HS256).
# Found in Dashboard → Project Settings → API → JWT Settings → JWT Secret.
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
# "low" | "medium" | "high" — thinking depth for summaries and briefings
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")

EMBEDDING_SERVICE_URL = os.getenv("EMBEDDING_SERVICE_URL", "http://localhost:8002")
QUESTION_SERVICE_URL = os.getenv("QUESTION_SERVICE_URL", "http://localhost:8003")

MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "380000"))

BRIEFING_WINDOW_MINUTES = int(os.getenv("BRIEFING_WINDOW_MINUTES", "15"))
