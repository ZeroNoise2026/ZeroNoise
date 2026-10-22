"""
skills/
Shared skill framework for the QuantAgent project.

A "skill" is a self-contained capability (generate a research report, compare
tickers, etc.) that can be invoked from any consumer:

  - ChatbotUI backend (chat endpoint → LLM router → skill)
  - Summarization CLI (python -m summary.run --skill <name>)

Each skill lives in its own subdirectory under skills/, containing:
  - SKILL.md   description of when/how to use the skill (read by the router LLM)
  - skill.py   the implementation, a subclass of skills.base.Skill

Skills are discovered automatically by skills.registry on first import.
"""

from skills.base import Skill, SkillResult
from skills.registry import get_skill, list_skills, all_skill_specs

__all__ = ["Skill", "SkillResult", "get_skill", "list_skills", "all_skill_specs"]
