"""reports.run._parse_skill_args accepts JSON or k=v pairs."""
from reports.run import _parse_skill_args


def test_empty_input():
    assert _parse_skill_args("") == {}


def test_json():
    raw = '{"tickers":["AAPL","MSFT"],"focus":"valuation"}'
    assert _parse_skill_args(raw) == {"tickers": ["AAPL", "MSFT"], "focus": "valuation"}


def test_key_value_pairs_coerce_bools_and_ints():
    assert _parse_skill_args("ticker=AAPL,dry_run=true") == {"ticker": "AAPL", "dry_run": True}
    assert _parse_skill_args("limit=10") == {"limit": 10}
