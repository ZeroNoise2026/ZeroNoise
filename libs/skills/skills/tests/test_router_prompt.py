"""
skills/tests/test_router_prompt.py
Sanity-check the router prompt by inspecting what would be sent to Claude,
without making the real API call. Useful when iterating on prompt wording
or skill descriptions.

Run:  python -m skills.tests.test_router_prompt
"""

from __future__ import annotations


def main() -> int:
    from skills.router import _build_system_prompt

    prompt = _build_system_prompt()
    print("=" * 60)
    print("ROUTER SYSTEM PROMPT (what gets sent to Claude):")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
    print(f"Length: {len(prompt)} chars")

    # Cheap structural sanity — every registered skill should be referenced
    # in the prompt, otherwise the router can't pick it.
    from skills import list_skills
    missing = [name for name in list_skills() if name not in prompt]
    if missing:
        print(f"\n✗ FAIL: skills not mentioned in prompt: {missing}")
        return 1
    print(f"\n✓ All {len(list_skills())} skills mentioned in prompt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
