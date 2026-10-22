"""
question/templates/renderer.py
Main entry point — renders (template_source, context) into the final Markdown string.

Any exception (undefined variable / render error) is wrapped as RenderError; the caller catches it and goes through fallback.
"""
from __future__ import annotations
import logging
from typing import Any
from jinja2 import UndefinedError, TemplateSyntaxError

from .engine import compile_template

logger = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Template rendering failed — the caller should fallback to the legacy generation path."""


def render(template_source: str, context: dict[str, Any]) -> str:
    """Render template string with given context. Raises RenderError on any failure.

    Also validates that no literal "{{" or "}}" slipped through (indicates the LLM
    produced a field name that was interpreted as text rather than a real variable,
    which StrictUndefined should already catch — this is belt-and-braces).
    """
    try:
        tpl = compile_template(template_source)
        out = tpl.render(**context)
    except UndefinedError as e:
        raise RenderError(f"Undefined variable during render: {e}") from e
    except TemplateSyntaxError as e:
        raise RenderError(f"Template syntax error: {e}") from e
    except Exception as e:  # pragma: no cover — catch-all to prevent crashes
        raise RenderError(f"Unexpected render error: {type(e).__name__}: {e}") from e

    # Defensive: after render, the output should not contain any residual {{ }}
    if "{{" in out or "}}" in out:
        raise RenderError(f"Residual mustache markers in output (first 120 chars): {out[:120]!r}")
    return out
