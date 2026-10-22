"""
evals/judge.py

Claim-level faithfulness judge.

Why claim-level and not a 1-10 score
------------------------------------
A holistic "rate this summary 1-10" is the cheapest thing to build and the
least useful thing to own. It is unstable across runs, it cannot tell you
*which* sentence was wrong, and it cannot distinguish "the context did not
mention this" from "the context says the opposite". So this judge does two
stages:

    stage 1  summary  -> atomic, self-contained claims
    stage 2  claims + context -> per-claim verdict + verbatim evidence span

    faithfulness = supported / total_claims

Verdicts are three-valued on purpose:

    supported     the context states this, or directly entails it
    unsupported   the context is SILENT on this. Not necessarily false.
    contradicted  the context states something incompatible. Always fatal.

`unsupported` and `contradicted` are different failures with different fixes
(retrieval gap vs. the model overriding its evidence), so collapsing them
would throw away the most actionable signal in the whole suite.

The judge is told to reason ONLY from the supplied context. A claim that is
true in the real world but absent from the context is `unsupported`, not
`supported`. That is the definition of faithfulness, and it is also why this
suite measures faithfulness rather than correctness — see README, FX3.

Judges misbehave too
--------------------
A judge that returns unparseable JSON must not silently count as a pass OR a
fail. It retries once; if it still fails, `JudgeError` is raised and the
runner records the run as `error` in its own column.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# The judge deliberately reuses the generator's Claude client object rather
# than constructing a fourth copy of it in this repo. Only the *model* differs.
import summarizer

# Default judge model. Must not be the same model that generated the summary
# (summarizer uses LLM_MODEL, claude-haiku-5-5 by default), and should be at
# least as capable. Override with EVAL_JUDGE_MODEL or --judge-model.
DEFAULT_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-sonnet-5-5")

VALID_VERDICTS = {"supported", "unsupported", "contradicted"}

# Which fixture fields make up "the context", and how they are labelled to the
# judge. Order matches the order summarizer.generate_summary assembles them.
CONTEXT_SECTIONS = [
    ("price_text", "PRICE"),
    ("earnings_text", "EARNINGS"),
    ("news_text", "NEWS"),
    ("filings_text", "FILINGS"),
]


class JudgeError(RuntimeError):
    """The judge itself failed (bad JSON, API error). Neither pass nor fail."""


@dataclass
class JudgeResult:
    claims: list[dict[str, Any]] = field(default_factory=list)
    faithfulness: float = 0.0
    n_supported: int = 0
    n_unsupported: int = 0
    n_contradicted: int = 0
    total_claims: int = 0
    model: str = ""
    votes: int = 1
    latency_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def has_contradiction(self) -> bool:
        return self.n_contradicted > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "faithfulness": round(self.faithfulness, 4),
            "counts": {
                "supported": self.n_supported,
                "unsupported": self.n_unsupported,
                "contradicted": self.n_contradicted,
                "total": self.total_claims,
            },
            "has_contradiction": self.has_contradiction,
            "model": self.model,
            "votes": self.votes,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "claims": self.claims,
        }


# ─────────────────────────────────────────────────────────────
# Context assembly
# ─────────────────────────────────────────────────────────────
def build_context(fixture: dict[str, Any]) -> str:
    """Rebuild the evidence the generator saw, with section labels.

    Labels matter: they let the judge report WHICH source supported a claim,
    which is what surfaces the FX3-style problem where two sections of the
    same context disagree with each other.
    """
    parts: list[str] = []
    for key, label in CONTEXT_SECTIONS:
        text = (fixture.get(key) or "").strip()
        if text:
            parts.append(f"[{label}]\n{text}")
    if not parts:
        return "(no context supplied)"
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# LLM plumbing
# ─────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(raw: str) -> str:
    m = _FENCE_RE.match(raw or "")
    return m.group(1) if m else (raw or "").strip()


def _call_json(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, int], int]:
    """One JSON-mode call. Returns (parsed, usage, latency_ms).

    Raises JudgeError if the response is not parseable JSON — the caller owns
    the retry policy.
    """
    t0 = time.time()
    try:
        # Current Claude models reject non-default temperature, so the judge is
        # not deterministic — use --judge-votes to smooth it. Low effort keeps
        # the judge cheap; max_tokens leaves headroom for thinking.
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens + 4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"effort": "low"},
        )
    except Exception as e:  # API-level failure is also a judge failure
        raise JudgeError(f"judge API call failed: {type(e).__name__}: {e}") from e

    latency_ms = int((time.time() - t0) * 1000)
    if resp.stop_reason == "refusal":
        raise JudgeError(f"judge model {model} declined the request (stop_reason=refusal)")
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()

    u = resp.usage
    usage = {
        "prompt_tokens": u.input_tokens or 0,
        "completion_tokens": u.output_tokens or 0,
        "total_tokens": (u.input_tokens or 0) + (u.output_tokens or 0),
    }

    try:
        parsed = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as e:
        raise JudgeError(f"judge returned unparseable JSON: {e}; raw[:200]={raw[:200]!r}")

    if not isinstance(parsed, dict):
        raise JudgeError(f"judge returned {type(parsed).__name__}, expected a JSON object")

    return parsed, usage, latency_ms


def _merge_usage(target: dict[str, int], add: dict[str, int]) -> None:
    for k, v in add.items():
        target[k] = target.get(k, 0) + v


# ─────────────────────────────────────────────────────────────
# Stage 1 — claim extraction
# ─────────────────────────────────────────────────────────────
_EXTRACT_SYSTEM = """\
You break a financial summary into atomic, independently checkable claims.

Return ONLY a JSON object of this exact shape:

  {"claims": ["<claim 1>", "<claim 2>", ...]}

Rules for each claim:
- ATOMIC: exactly one assertion. Split "Revenue was $48.2B, up 31%" into two.
- SELF-CONTAINED: resolve every pronoun and reference. Name the company or
  ticker explicitly in each claim, so the claim can be checked on its own.
- VERBATIM IN MEANING: do not soften, strengthen, or reinterpret. If the text
  hedges ("may", "is expected to"), keep the hedge in the claim.
- CHECKABLE ONLY: extract assertions of fact - figures, dates, named events,
  named entities, stated guidance. SKIP pure opinion, recommendation, and
  generic outlook language that no evidence could confirm or deny
  ("the outlook is positive", "investors should watch closely").

Extract every checkable claim. Do not deduplicate near-identical claims.
Return only the JSON object."""


def extract_claims(
    summary: str,
    *,
    client: Any,
    model: str,
    max_tokens: int = 1500,
) -> tuple[list[str], dict[str, int], int]:
    user = (
        "Break the following financial summary into atomic checkable claims. "
        "Respond with ONLY the JSON object described in the system message.\n\n"
        "SUMMARY:\n"
        f"{summary}"
    )
    parsed, usage, latency = _call_json(client, model, _EXTRACT_SYSTEM, user, max_tokens)

    raw_claims = parsed.get("claims")
    if not isinstance(raw_claims, list):
        raise JudgeError(f"claim extraction: 'claims' missing or not a list; got {parsed!r:.200}")

    claims = [c.strip() for c in raw_claims if isinstance(c, str) and c.strip()]
    return claims, usage, latency


# ─────────────────────────────────────────────────────────────
# Stage 2 — per-claim verification
# ─────────────────────────────────────────────────────────────
_VERIFY_SYSTEM = """\
You verify claims against a supplied CONTEXT, and nothing else.

Return ONLY a JSON object of this exact shape:

  {"results": [
     {"claim": "<claim verbatim>",
      "verdict": "supported" | "unsupported" | "contradicted",
      "evidence": "<verbatim span copied from CONTEXT, or empty string>",
      "evidence_source": "PRICE" | "EARNINGS" | "NEWS" | "FILINGS" | "NONE"}
  ]}

Verdict definitions - read carefully, they are not interchangeable:

- "supported"    CONTEXT explicitly states the claim, or the claim follows
                 directly from it by arithmetic or trivial restatement.
- "unsupported"  CONTEXT is SILENT on the claim. Use this even when you
                 personally believe the claim is true. Absence of evidence
                 is "unsupported", never "supported".
- "contradicted" CONTEXT states something logically incompatible with the
                 claim - a different number for the same quantity, a
                 different date for the same event, an opposite direction.

CRITICAL: judge ONLY from CONTEXT. You have no outside knowledge. A claim
that is factually true in the real world but absent from CONTEXT is
"unsupported". Do not fill gaps from memory.

"evidence" must be copied verbatim from CONTEXT (max 200 chars). Use an empty
string only when the verdict is "unsupported". "evidence_source" is the
bracketed section label the evidence came from, or "NONE".

Return one result per claim, in the order given. Return only the JSON object."""


def verify_claims(
    claims: list[str],
    context: str,
    *,
    client: Any,
    model: str,
    max_tokens: int = 3000,
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    user = (
        "Verify each claim against the CONTEXT. Respond with ONLY the JSON "
        "object described in the system message.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"CLAIMS:\n{numbered}"
    )
    parsed, usage, latency = _call_json(client, model, _VERIFY_SYSTEM, user, max_tokens)

    raw = parsed.get("results")
    if not isinstance(raw, list):
        raise JudgeError(f"verification: 'results' missing or not a list; got {parsed!r:.200}")

    results: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise JudgeError(f"verification: result {i} is {type(item).__name__}, expected object")
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in VALID_VERDICTS:
            raise JudgeError(f"verification: result {i} has invalid verdict {verdict!r}")
        results.append({
            "claim": str(item.get("claim") or (claims[i] if i < len(claims) else "")),
            "verdict": verdict,
            "evidence": str(item.get("evidence") or ""),
            "evidence_source": str(item.get("evidence_source") or "NONE").strip().upper(),
        })

    if len(results) != len(claims):
        raise JudgeError(
            f"verification: judge returned {len(results)} verdicts for {len(claims)} claims"
        )
    return results, usage, latency


# ─────────────────────────────────────────────────────────────
# Majority vote
# ─────────────────────────────────────────────────────────────
# The judge is an LLM, so the judge is nondeterministic too. Running it once
# and treating the answer as ground truth repeats the exact mistake that n=3
# exists to avoid on the generator side.
#
# A single spurious "contradicted" fails an entire fixture, so borderline
# claims are the expensive ones. On a tie we deliberately pick the LESS severe
# verdict: we would rather under-report a contradiction than fail a build on
# one flaky vote.
_SEVERITY = {"supported": 0, "unsupported": 1, "contradicted": 2}


def _majority(verdicts: list[str]) -> str:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    top = max(counts.values())
    winners = [v for v, c in counts.items() if c == top]
    return min(winners, key=lambda v: _SEVERITY[v])


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
def judge_summary(
    summary: str,
    fixture: dict[str, Any],
    *,
    model: Optional[str] = None,
    client: Any = None,
    votes: int = 1,
    retries: int = 1,
) -> JudgeResult:
    """Judge one summary. Raises JudgeError if the judge itself misbehaves.

    Args:
        summary: markdown produced by summarizer.generate_summary().
        fixture: raw fixture record (`_trap` is never read).
        model: judge model. Defaults to EVAL_JUDGE_MODEL, then
            DEFAULT_JUDGE_MODEL. Must differ from the generator's model.
        client: Anthropic client. Defaults to the generator's Claude client
            (same endpoint, different model). Injectable for testing.
        votes: run stage 2 this many times and take a per-claim majority.
        retries: extra attempts on a JSON parse failure. 1 = try twice total.
    """
    model = model or DEFAULT_JUDGE_MODEL
    client = client or summarizer._get_client()
    context = build_context(fixture)

    usage_total: dict[str, int] = {}
    latency_total = 0
    last_err: Optional[JudgeError] = None

    for attempt in range(retries + 1):
        try:
            claims, u1, l1 = extract_claims(summary, client=client, model=model)
            _merge_usage(usage_total, u1)
            latency_total += l1

            if not claims:
                # A summary with no checkable claims is vacuously faithful.
                # Surface it as 0 claims rather than dividing by zero.
                return JudgeResult(
                    claims=[], faithfulness=1.0, total_claims=0,
                    model=model, votes=votes,
                    latency_ms=latency_total, usage=usage_total,
                )

            ballots: list[list[dict[str, Any]]] = []
            for _ in range(max(1, votes)):
                results, u2, l2 = verify_claims(claims, context, client=client, model=model)
                _merge_usage(usage_total, u2)
                latency_total += l2
                ballots.append(results)

            merged: list[dict[str, Any]] = []
            for i in range(len(claims)):
                per_claim = [b[i] for b in ballots]
                verdict = _majority([r["verdict"] for r in per_claim])
                # Keep the evidence from a ballot that agrees with the winner.
                chosen = next((r for r in per_claim if r["verdict"] == verdict), per_claim[0])
                entry = {
                    "claim": chosen["claim"],
                    "verdict": verdict,
                    "evidence": chosen["evidence"],
                    "evidence_source": chosen["evidence_source"],
                }
                if votes > 1:
                    entry["votes"] = [r["verdict"] for r in per_claim]
                merged.append(entry)

            n_sup = sum(1 for c in merged if c["verdict"] == "supported")
            n_uns = sum(1 for c in merged if c["verdict"] == "unsupported")
            n_con = sum(1 for c in merged if c["verdict"] == "contradicted")
            total = len(merged)

            return JudgeResult(
                claims=merged,
                faithfulness=(n_sup / total) if total else 1.0,
                n_supported=n_sup,
                n_unsupported=n_uns,
                n_contradicted=n_con,
                total_claims=total,
                model=model,
                votes=votes,
                latency_ms=latency_total,
                usage=usage_total,
            )

        except JudgeError as e:
            last_err = e
            if attempt < retries:
                continue
            raise JudgeError(f"judge failed after {attempt + 1} attempt(s): {e}") from e

    raise JudgeError(f"judge failed: {last_err}")
