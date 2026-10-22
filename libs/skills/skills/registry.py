"""
skills/registry.py
Auto-discovers Skill subclasses in skills/*/skill.py and exposes a lookup API.

Discovery rules:
  - Each subdirectory of skills/ (except dunder dirs) that contains skill.py
    is imported once at registry initialization.
  - Any Skill subclass defined in that module is registered under its
    `name` class attribute. Duplicate names raise at import time.

Public API:
  - get_skill(name)        -> Skill instance (or raise KeyError)
  - list_skills()          -> list[str] of registered names
  - all_skill_specs()      -> list[dict] for the router LLM
  - invoke(name, **kwargs) -> SkillResult (validates kwargs against schema)

The registry is built lazily on first access so importing skills/ doesn't
force-load every skill (helpful for unit tests and CLI startup time).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Any

from skills.base import Skill, SkillResult

logger = logging.getLogger("skills.registry")

_registry: dict[str, type[Skill]] | None = None


# ─────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────
def _discover() -> dict[str, type[Skill]]:
    """Walk skills/*/skill.py and collect Skill subclasses.

    Why this layout: putting each skill in its own dir (rather than one flat
    skills/foo.py file) gives every skill a natural place to keep SKILL.md,
    prompt templates, fixtures, and tests next to its code.
    """
    found: dict[str, type[Skill]] = {}
    skills_root = Path(__file__).parent

    for entry in skills_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue
        if not (entry / "skill.py").exists():
            continue

        module_name = f"skills.{entry.name}.skill"
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            # Don't let one broken skill take down the whole registry —
            # log and skip. Production behavior may want to fail-fast; flip
            # this `continue` to `raise` if you prefer that.
            logger.exception("Failed to import %s: %s", module_name, e)
            continue

        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Skill)
                and obj is not Skill
            ):
                if obj.name in found:
                    raise RuntimeError(
                        f"Duplicate skill name {obj.name!r}: "
                        f"{found[obj.name].__module__} vs {obj.__module__}"
                    )
                found[obj.name] = obj
                logger.info("Registered skill: %s", obj.name)

    return found


def _ensure_loaded() -> dict[str, type[Skill]]:
    global _registry
    if _registry is None:
        _registry = _discover()
    return _registry


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def get_skill(name: str) -> Skill:
    """Return a fresh instance of the named skill.

    Skills are instantiated per-call rather than cached as singletons so a
    skill can safely keep request-scoped state on `self` without leaking
    between invocations.
    """
    registry = _ensure_loaded()
    if name not in registry:
        raise KeyError(
            f"Unknown skill {name!r}. Available: {sorted(registry)}"
        )
    return registry[name]()


def list_skills() -> list[str]:
    return sorted(_ensure_loaded().keys())


def all_skill_specs() -> list[dict[str, Any]]:
    """Compact specs for every registered skill — what the router LLM sees."""
    return [cls.spec() for cls in _ensure_loaded().values()]


def invoke(name: str, **kwargs: Any) -> SkillResult:
    """Convenience: look up + run in one call.

    TODO: validate kwargs against `parameters` JSON schema before calling
    run(). Deferred to when we add jsonschema as a dep — for now skills can
    do their own arg checks in run().
    """
    skill = get_skill(name)
    logger.info("Invoking skill %s with kwargs=%s", name, list(kwargs))
    return skill.run(**kwargs)


def reload() -> None:
    """Drop the cached registry. Useful in tests / dev when adding a skill
    without restarting the process."""
    global _registry
    _registry = None
