"""
question/structured.py
Plan 4 — call Claude to produce structured JSON, validate, then return.

Differences from common.llm.chat():
  * Constrains output with structured outputs (output_config.format json_schema)
  * jsonschema validation of the full schema, retry once on failure
  * Returns dict (already parsed), raises StructuredGenError on failure

Structured outputs don't support string/array length or numeric bound
constraints, so those are stripped from the schema sent to the API and
enforced client-side by the jsonschema validator instead.
"""
from __future__ import annotations
import json
import logging
import time
from typing import Any

from jsonschema import Draft202012Validator

from config import LLM_MODEL_QA
from common.llm import chat, LLMRefusal

logger = logging.getLogger(__name__)

_UNSUPPORTED_KEYS = {
    "minLength", "maxLength", "minItems", "maxItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "pattern",
}


class StructuredGenError(RuntimeError):
    """LLM failed to produce valid JSON. The caller should fallback."""


def api_schema(schema: Any) -> Any:
    """Copy of `schema` without the constraints structured outputs rejects."""
    if isinstance(schema, dict):
        return {k: api_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_KEYS}
    if isinstance(schema, list):
        return [api_schema(v) for v in schema]
    return schema


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    few_shot: list[dict[str, str]] | None = None,
    intent_label: str = "unknown",
    max_retries: int = 2,
    max_tokens: int = 4000,
) -> tuple[dict[str, Any], dict]:
    """Call Claude with a JSON schema, validate against it. Returns (parsed_json, usage).

    Raises StructuredGenError on repeated failure — caller must fallback.
    """
    validator = Draft202012Validator(schema)
    output_schema = api_schema(schema)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if few_shot:
        messages.extend(few_shot)
    messages.append({"role": "user", "content": user_prompt})

    model = LLM_MODEL_QA
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            raw, usage = chat(
                messages=messages,
                model=model,
                effort="low",
                output_schema=output_schema,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.time() - t0) * 1000)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as je:
                raise StructuredGenError(f"Invalid JSON from LLM: {je}; raw[:200]={raw[:200]!r}")

            # schema validate (incl. length constraints the API doesn't enforce)
            errors = sorted(validator.iter_errors(parsed), key=lambda e: e.path)
            if errors:
                msg = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3])
                raise StructuredGenError(f"Schema violation: {msg}")

            logger.info(
                f"structured_gen OK: intent={intent_label} model={model} "
                f"tokens={usage['total_tokens']} latency={latency_ms}ms attempt={attempt}"
            )
            return parsed, {**usage, "latency_ms": latency_ms, "model": model}

        except StructuredGenError as e:
            last_err = e
            logger.warning(f"structured_gen retry {attempt}/{max_retries}: {e}")
            # Add a corrective hint for next attempt
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": (
                    "Your previous response did not match the required schema. "
                    f"Error: {e}. Reply again with ONLY the corrected JSON object."
                )},
            ]
        except LLMRefusal as e:
            raise StructuredGenError(f"LLM refused: {e}")
        except Exception as e:
            last_err = e
            logger.error(f"structured_gen unexpected error: {type(e).__name__}: {e}")
            break

    raise StructuredGenError(f"Failed after {max_retries} attempts: {last_err}")
