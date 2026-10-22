"""
skills/base.py
Abstract base class for all skills.

A Skill subclass declares:
  - name          stable identifier used by the router and CLI
  - description   one-line summary shown to the router LLM
  - parameters    JSON-schema dict describing run() kwargs (so the router can
                  extract arguments from the user message)

and implements:
  - run(**kwargs) -> SkillResult

Skills are *synchronous* on purpose — both consumers (Summarization CLI and
ChatbotUI's chat endpoint) are sync today, and the bottleneck is the LLM
call, not concurrency. If a skill needs concurrency internally it can use
threads or asyncio.run() inside run().

SKILL.md (sibling file in the same directory) holds the longer-form
"when to use this skill" guidance. The base class auto-locates it from the
subclass's module path so subclasses don't have to wire paths manually.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger("skills")


# ─────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────
@dataclass
class SkillResult:
    """Uniform return type so callers don't have to special-case each skill.

    - content:   primary human-readable output (markdown). For ChatbotUI this
                 is what gets streamed/displayed to the user.
    - data:      optional structured payload (tables, raw rows) for callers
                 that want to render their own UI on top.
    - artifacts: list of file paths written to disk (e.g. generate_report
                 writes output/{TICKER}_{DATE}.md and returns it here).
    - meta:      diagnostic info (token counts, timings, cache hits) — not
                 shown to the user, useful for logging/debugging.
    """

    content: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Path] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────
# Skill base class
# ─────────────────────────────────────────────
class Skill(ABC):
    """Subclass this to define a skill.

    Minimum subclass shape:

        class CompareTickersSkill(Skill):
            name = "compare_tickers"
            description = "Side-by-side comparison of 2+ tickers."
            parameters = {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                    },
                },
                "required": ["tickers"],
            }

            def run(self, *, tickers: list[str]) -> SkillResult:
                ...
    """

    # Required class attributes (subclasses must set)
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    # ── lifecycle ────────────────────────────
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Fail loudly at import time if a subclass forgot the basics.
        if not cls.__dict__.get("name"):
            raise TypeError(f"{cls.__name__} must set a class-level `name`")
        if not cls.__dict__.get("description"):
            raise TypeError(f"{cls.__name__} must set a class-level `description`")

    @abstractmethod
    def run(self, **kwargs: Any) -> SkillResult:
        """Execute the skill. Subclasses must implement.

        Keyword arguments are validated against `parameters` by the registry
        before this is called, so implementations can trust the inputs.
        """
        raise NotImplementedError

    # ── introspection helpers ────────────────
    @classmethod
    def skill_dir(cls) -> Path:
        """Directory the subclass lives in — used to locate SKILL.md."""
        return Path(inspect.getfile(cls)).parent

    @classmethod
    def skill_md(cls) -> str:
        """Read SKILL.md content. Returns empty string if missing (allowed
        for skills whose `description` is already sufficient)."""
        md_path = cls.skill_dir() / "SKILL.md"
        if not md_path.exists():
            logger.debug("No SKILL.md for %s at %s", cls.name, md_path)
            return ""
        return md_path.read_text(encoding="utf-8")

    @classmethod
    def spec(cls) -> dict[str, Any]:
        """Compact spec passed to the router LLM. Intentionally small to keep
        the routing prompt cheap — the full SKILL.md is only loaded if the
        router decides this skill is a candidate."""
        return {
            "name": cls.name,
            "description": cls.description,
            "parameters": cls.parameters,
        }
