"""
question/schemas
================
Plan 4 — JSON Schema + template definitions. One file per intent, exporting:

  SCHEMA          — jsonschema dict, used to validate LLM output (for EARNINGS / COMPARISON)
  TEMPLATE        — Jinja2 template string
  build_context() — builds the render context from DB data (injects backend-precomputed yoy/qoq/table, etc.)
  SYSTEM_PROMPT   — (only for LLM-involved intents) tells the LLM to output JSON per the schema
  FEW_SHOT        — (same as above) example pairs
"""
