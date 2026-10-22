"""
question/templates
==================

Plan 4 template engine — decouples "numbers + narrative":
  1. The LLM only produces JSON slots (schemas/*.py defines the JSON Schema)
  2. The backend uses this package's Jinja2 sandbox environment to render the final Markdown

Key constraints:
  * SandboxedEnvironment — forbids access to __class__ / __mro__ / any external attributes
  * StrictUndefined     — if the LLM writes a wrong field name it raises immediately, triggering fallback
  * Disable {% %} block expressions — tables/loops are pre-generated as strings by the backend's render_* functions
  * filters: money / pct / compact — uniform number formatting
  * functions: yoy / qoq / sum_fy / pick_max / render_table
"""
from .renderer import render, RenderError

__all__ = ["render", "RenderError"]
