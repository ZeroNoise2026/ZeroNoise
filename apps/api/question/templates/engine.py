"""
question/templates/engine.py
Jinja2 SandboxedEnvironment — singleton, built once at startup.

Security policy:
  * Disable block syntax like {% block %} / {% for %} / {% if %} — the LLM can only use {{var}} and | filter
    Mechanism: override block_start_string/block_end_string to sentinel strings unlikely to appear in normal text,
    so any {% ... %} is treated as plain text. This is more reliable than scanning the string with regex.
  * StrictUndefined — raises UndefinedError when an undefined variable appears in the template (no silent empty output).
  * autoescape=False — we output Markdown, not HTML.
"""
from __future__ import annotations
import logging
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined, Template

from .filters import ALL_FILTERS
from .functions import ALL_FUNCTIONS

logger = logging.getLogger(__name__)

# Sentinel string: 32-byte random text, probability of appearing in body content ≈ 0. Equivalent to "disable block syntax".
_BLOCK_SENTINEL_START = "{%__QA_PLAN4_BLOCK_DISABLED__"
_BLOCK_SENTINEL_END = "__QA_PLAN4_BLOCK_DISABLED__%}"

_env: SandboxedEnvironment | None = None


def get_env() -> SandboxedEnvironment:
    """Lazy singleton — one Environment shared across the entire process (template compile cache)."""
    global _env
    if _env is None:
        env = SandboxedEnvironment(
            block_start_string=_BLOCK_SENTINEL_START,
            block_end_string=_BLOCK_SENTINEL_END,
            variable_start_string="{{",
            variable_end_string="}}",
            comment_start_string="{#",
            comment_end_string="#}",
            autoescape=False,
            undefined=StrictUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        for name, fn in ALL_FILTERS.items():
            env.filters[name] = fn
        for name, fn in ALL_FUNCTIONS.items():
            env.globals[name] = fn
        _env = env
        logger.info(
            f"Jinja2 sandbox initialized: filters={list(ALL_FILTERS)}, "
            f"globals={list(ALL_FUNCTIONS)}, blocks=DISABLED, strict=True"
        )
    return _env


def compile_template(source: str) -> Template:
    """Compile a template string under the sandboxed env."""
    return get_env().from_string(source)
