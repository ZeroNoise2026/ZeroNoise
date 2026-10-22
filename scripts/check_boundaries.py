"""
scripts/check_boundaries.py
Enforce the monorepo rule: apps never import each other, and libs never
import apps. Code two apps need goes in libs/; apps talk over HTTP.

Python apps here import by top-level module name after a sys.path tweak, so
an import graph can't see cross-app reach. Instead this flags the two ways
code reaches across: `import apps...` / `from apps...`, and path literals
that point into another app's directory ("apps" / "<name>" or "apps/<name>").

    python scripts/check_boundaries.py      # exit 1 on any violation
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = sorted(p.name for p in (ROOT / "apps").iterdir() if p.is_dir())

IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+apps\.(\w+)", re.MULTILINE)
PATH_RE = re.compile(r"""["']apps["']\s*\)?\s*/\s*["'](\w+)["']|["']apps/(\w+)""")
SKIP_DIRS = {"venv", ".venv", "node_modules", "__pycache__"}


def owner(path: Path) -> str | None:
    """App name for files under apps/<name>/, None for libs/ and the rest."""
    rel = path.relative_to(ROOT).parts
    return rel[1] if rel[0] == "apps" else None


def main() -> int:
    violations: list[str] = []
    for top in ("apps", "libs"):
        for path in (ROOT / top).rglob("*.py"):
            if SKIP_DIRS & set(path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            me = owner(path)
            rel = str(path.relative_to(ROOT))
            targets = {m for m in IMPORT_RE.findall(text)}
            targets |= {a or b for a, b in PATH_RE.findall(text)}
            for target in sorted(targets & set(APPS)):
                if target == me:
                    continue
                violations.append(f"{rel} reaches into apps/{target}")

    for v in violations:
        print(f"✗ {v}")
    if violations:
        print("\nApps never import each other; libs never import apps. Move shared code to libs/.")
        return 1
    print("✓ boundaries ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
