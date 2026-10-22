# Skills

Shared **skill framework** for the QuantAgent stack. A skill is a self-contained
capability — write a research report, compare tickers, explain a filing — that
any consumer in the project can invoke.

> Part of the [QuantAgent](https://github.com/ZeroNoise2026/QuantAgent) stack.
> You do not clone this repo directly: clone QuantAgent and run `./dev.sh`,
> which pulls this one as a sibling.

> **For PMs / analysts:** this is where reusable analyst workflows live. Today
> there are two — a single-ticker research report and a multi-ticker
> comparison. New skills show up in both the chat product and the CLI without
> either of them changing.

> **For engineers:** an installable Python package (`skills`) that owns skill
> discovery, the LLM router mapping user messages to skills, and a small
> neutral Claude client.

## Layout

```
Skills/                       <- the repo (clone dir name is irrelevant)
├── pyproject.toml            packaging; declares the `skills` package
├── .github/workflows/ci.yml  import guard on Linux + offline suite
└── skills/                   <- THE IMPORTABLE PACKAGE
    ├── __init__.py           public re-exports
    ├── base.py               Skill ABC + SkillResult
    ├── registry.py           auto-discovery of skills/*/skill.py
    ├── router.py             message -> (skill, args) classifier
    ├── _llm.py               neutral Claude client
    ├── generate_report/      SKILL.md + skill.py
    ├── compare_tickers/      SKILL.md + skill.py
    └── tests/
```

### Why the package sits in a subdirectory

It used to live at the repo root, which made the importable package name
whatever `git clone` happened to produce — `Skills`. Every call site imports
lowercase `skills`, so it only ever worked on case-insensitive filesystems
(macOS). On Linux, Docker and CI the import failed and callers silently
degraded to their fallback path.

Renaming the folder is not a fix: **git does not track the clone directory
name**, so it reverts on the next clone. Moving the package into a tracked
`skills/` subdirectory fixes it permanently, and makes the repo pip-installable
as a bonus. `.github/workflows/ci.yml` checks out into a directory literally
named `Skills` on ubuntu and asserts `import skills` still resolves, so the bug
cannot come back unnoticed.

## Install

```bash
pip install -e /path/to/Skills          # editable, for development
pip install "git+https://github.com/ZeroNoise2026/Skills.git@main"
```

`./dev.sh setup` in QuantAgent does the editable install automatically, which
overrides the git pin in `QuantAgent/backend/requirements.txt` so local edits
take effect without a push.

Without installing, adding the repo root to `sys.path` also works — that is
what the consumers do when they find a local clone.

## Available skills

| Name | Purpose | Required args | Returns |
|---|---|---|---|
| `generate_report` | Full investment-analysis report for one ticker | `ticker: str` | Markdown + artifact at `Summarization/output/{TICKER}_{DATE}.md` |
| `compare_tickers` | Side-by-side comparison of 2+ tickers | `tickers: list[str]` | Markdown (no artifact) |

## Usage

```python
from skills import get_skill, list_skills
from skills.router import route

result = get_skill("generate_report").run(ticker="AAPL")
print(result.content)

decision = route("Compare AAPL and MSFT on valuation")
if decision.skill:
    result = get_skill(decision.skill).run(**decision.args)
```

From the Summarization CLI:

```bash
python -m summary.run --list-skills
python -m summary.run --skill generate_report --args ticker=AAPL
python -m summary.run --skill compare_tickers --args '{"tickers":["AAPL","MSFT"]}'
```

From the QuantAgent chat endpoint: `backend/main.py` already calls the router
inside `/api/chat/stream`. Per message:

```python
decision = route(user_message)
if decision.skill is not None:
    result = get_skill(decision.skill).run(**decision.args)
    yield_as_sse(result.content)
else:
    yield_from(rag_pipeline(user_message))
```

Routing failures (missing key, network blip, bad JSON) return `skill=None` and
fall through to RAG, so no new failure mode is introduced in chat. A *missing
package*, by contrast, is logged loudly once at import time — it used to be
swallowed per-request, which hid a container that shipped without this package.

## Adding a skill

1. Create `skills/<name>/` with `__init__.py`, `SKILL.md` and `skill.py`.
2. Subclass `skills.base.Skill`, set `name` / `description` / `parameters`
   (JSON Schema), implement `run(**kwargs) -> SkillResult`.
3. Write `SKILL.md` describing when the skill should fire.
4. Done — the registry discovers it on next import. No registration step.

Names must be unique across the tree; duplicates raise at import time.

```python
from skills.base import Skill, SkillResult

class NewsDigestSkill(Skill):
    name = "news_digest"
    description = "Summarize recent news for a single ticker."
    parameters = {
        "type": "object",
        "properties": {"ticker": {"type": "string"}, "days": {"type": "integer", "default": 7}},
        "required": ["ticker"],
    }

    def run(self, *, ticker: str, days: int = 7) -> SkillResult:
        return SkillResult(content="...", meta={"ticker": ticker, "days": days})
```

Any subdirectory of `skills/` not starting with `_` or `.` and containing a
`skill.py` is treated as a skill. `tests/` has no `skill.py`, so it is ignored.

## How routing works

One Claude call with a system prompt listing every registered skill (name,
description, JSON schema). The model returns:

```json
{"skill": "compare_tickers", "args": {"tickers": ["AAPL", "MSFT"]}}
```

…or `{"skill": null}` when nothing fits. The classifier is small and cheap
(`claude-haiku-5-5`, effort `low`), so routing costs roughly one fast call per
message. Inspect the exact prompt with:

```bash
python -m skills.tests.test_router_prompt
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Without it, routing and LLM-backed skills disable themselves and the caller falls through to its default behaviour |
| `SKILLS_ROUTER_MODEL` | `claude-haiku-5-5` | Model used for routing |
| `SKILLS_COMPARE_MODEL` | `claude-haiku-5-5` | Model used by `compare_tickers` |

## Testing

```bash
python -m skills.tests.test_smoke          # 33 offline cases, fully mocked
python -m skills.tests.test_router_prompt  # print the router prompt, send nothing
python -m skills.tests.test_live_router    # real Claude call; needs the key
```

CI runs the first one on ubuntu plus an import guard. The smoke suite skips its
cross-repo CLI test when the sibling `Summarization` repo is not checked out, so
a standalone clone still passes.

## Cross-repo paths

`generate_report` and `compare_tickers` reach into the sibling `Summarization`
repo for data and LLM access, via
`Path(__file__).resolve().parents[3] / "Summarization"`. That depth assumes:

```
<workspace>/Skills/skills/<skill_name>/skill.py
```

If a file here ever moves up or down a level, that constant moves with it.

## Design notes

- **Synchronous, not async.** Both consumers are sync; the bottleneck is the
  LLM call, not concurrency. A skill needing internal parallelism can use
  threads or `asyncio.run()` itself.
- **One instance per call.** `get_skill()` returns a fresh instance, so a skill
  can keep request-scoped state on `self` safely.
- **No persistence.** Results go back to the caller; there is no `skill_runs`
  table. `SkillResult.artifacts` is the escape hatch for skills that write files.
- **`_llm.py` is the survivor.** Two near-identical Claude wrappers exist
  elsewhere (`Summarization/shared/llm.py`, `QuantAgent/backend/summarizer.py`).
  When those three are consolidated, this is the one to keep.

## Known tech debt

- `registry.invoke()` does not validate kwargs against the JSON schema. Harmless
  while a human reads the output; genuinely dangerous the day this runs in a
  loop, where a bad arg silently corrupts a whole trajectory.
- The Claude client is duplicated in three places (see above).
- `compare_tickers` lets the LLM build the metrics table. More robust would be
  to assemble it deterministically from `earnings` / `price_snapshot` and let the
  LLM write only the prose around it — the pattern `Summarization/question`
  already uses.
