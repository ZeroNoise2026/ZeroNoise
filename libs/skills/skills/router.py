"""
skills/router.py
Decide whether a user message should be handled by a skill, and if so which
one with what arguments.

Strategy: a single Claude call with a short system prompt listing every
registered skill (name + description + parameters). The model returns JSON:

    {"skill": "compare_tickers", "args": {"tickers": ["AAPL", "MSFT"]}}

or, when no skill fits:

    {"skill": null}

The chat endpoint uses the latter as the signal to fall back to the
existing RAG flow.

The call goes through common.llm, the monorepo's single Claude client.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from common.llm import chat, has_api_key
from skills.registry import all_skill_specs

logger = logging.getLogger("skills.router")

# Small, cheap model — routing is a classification, not generation.
# Override with SKILLS_ROUTER_MODEL if you want to A/B-test a smarter model.
_ROUTER_MODEL = os.environ.get("SKILLS_ROUTER_MODEL", "claude-haiku-5-5")
_ROUTER_EFFORT = "low"      # classification — keep routing fast
_ROUTER_MAX_TOKENS = 2000   # the JSON is tiny; headroom is for thinking


# ─────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────
@dataclass
class RouteDecision:
    """What route() returns. `skill` is None when no skill matches — the
    caller should fall back to its default behavior (e.g. RAG)."""

    skill: str | None
    args: dict[str, Any]
    raw: str  # raw LLM output, kept for logging/debugging


# ─────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────
_ROUTER_SYSTEM_PROMPT = """\
You are a routing classifier for a financial-analysis assistant. Given a
user message, decide which *skill* (if any) should handle it.

Available skills:
{skill_list}

Respond with a single JSON object and nothing else:

  {{"skill": "<skill_name>", "args": {{...}}}}

If no skill is a good fit, respond with:

  {{"skill": null}}

Rules:
- Only pick a skill if the user's intent clearly matches its description.
- Extract argument values from the user message. Do not invent tickers or
  parameters that aren't present.
- If required arguments are missing, set "skill": null so the caller can
  ask a clarifying question instead of guessing.
"""


def _build_system_prompt() -> str:
    specs = all_skill_specs()
    if not specs:
        # No skills registered → router should always return null.
        return _ROUTER_SYSTEM_PROMPT.format(skill_list="(none registered)")
    skill_list = "\n".join(
        f"- {s['name']}: {s['description']}\n  params: {json.dumps(s['parameters'])}"
        for s in specs
    )
    return _ROUTER_SYSTEM_PROMPT.format(skill_list=skill_list)


# ─────────────────────────────────────────────
# LLM call
# ─────────────────────────────────────────────
def _call_router_llm(system_prompt: str, user_message: str) -> str:
    """Send a routing classification request to Claude.

    Returns the model's raw text content. Caller (`route`) is responsible
    for JSON parsing and validation.

    If ANTHROPIC_API_KEY is unset we short-circuit to a no-route response
    so the framework remains importable in environments (e.g. CI) without
    credentials.
    """
    if not has_api_key():
        logger.warning("ANTHROPIC_API_KEY not set — router defaulting to no-route")
        return '{"skill": null}'

    content, _usage = chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        model=_ROUTER_MODEL,
        effort=_ROUTER_EFFORT,
        max_tokens=_ROUTER_MAX_TOKENS,
        service="skills",
    )
    return content


# Strip a ```json ... ``` fence if the model adds one despite our instructions.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _extract_json(raw: str) -> str:
    """Best-effort: tolerate code-fenced or whitespace-padded JSON."""
    m = _FENCE_RE.match(raw)
    return m.group(1) if m else raw.strip()


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────
def route(user_message: str) -> RouteDecision:
    """Classify `user_message` into (skill_name, args) or no-route.

    Always returns a RouteDecision — never raises on routing failure.
    Routing errors are logged and converted into a no-route decision so the
    chat endpoint can degrade gracefully to RAG.
    """
    system_prompt = _build_system_prompt()
    try:
        raw = _call_router_llm(system_prompt, user_message)
        parsed = json.loads(_extract_json(raw))
    except Exception as e:
        logger.exception("Router LLM call failed: %s", e)
        return RouteDecision(skill=None, args={}, raw="")

    skill = parsed.get("skill")
    args = parsed.get("args") or {}
    if skill is not None and not isinstance(skill, str):
        logger.warning("Router returned non-string skill: %r", skill)
        skill = None
    if not isinstance(args, dict):
        logger.warning("Router returned non-dict args: %r", args)
        args = {}

    return RouteDecision(skill=skill, args=args, raw=raw)
