"""Phase 1 smoke test for question.templates."""
from question.templates import render, RenderError
from question.templates.filters import money, pct
from question.templates.functions import yoy, qoq, render_table


def main():
    # 1. filters
    assert money(24_900_000_000) == "$24.9B", money(24_900_000_000)
    assert money(840_000_000) == "$840M", money(840_000_000)
    assert money(None) == "N/A"
    assert money(-1_234_567_890) == "-$1.2B"
    assert pct(0.1537) == "+15.37%", pct(0.1537)
    assert pct(-0.05) == "-5.00%"
    assert pct(None) == "N/A"
    print("filters OK")

    # 2. yoy / qoq
    earnings = [
        {"quarter": "Q4 2025", "revenue": 24_900_000_000, "eps": 0.24},
        {"quarter": "Q3 2025", "revenue": 22_000_000_000, "eps": 0.20},
        {"quarter": "Q2 2025", "revenue": 21_500_000_000, "eps": 0.18},
        {"quarter": "Q1 2025", "revenue": 20_000_000_000, "eps": 0.15},
        {"quarter": "Q4 2024", "revenue": 20_750_000_000, "eps": 0.30},
    ]
    assert round(yoy(earnings, "revenue"), 4) == round(
        (24.9e9 - 20.75e9) / 20.75e9, 4
    )
    assert round(qoq(earnings, "revenue"), 4) == round(
        (24.9e9 - 22e9) / 22e9, 4
    )
    print("functions OK")

    # 3. render_table
    tbl = render_table(
        earnings,
        [
            ("Quarter", "quarter", "str"),
            ("EPS", "eps", "money_2"),
            ("Revenue", "revenue", "money"),
        ],
        limit=4,
    )
    assert "| Q4 2025 | $0.24 | $24.9B |" in tbl, tbl
    print("render_table OK")
    print(tbl)

    # 4. full render
    tpl = "**Revenue:** {{ revenue | money }} (YoY {{ rev_yoy | pct }})"
    out = render(tpl, {"revenue": 24_900_000_000, "rev_yoy": 0.20})
    assert out == "**Revenue:** $24.9B (YoY +20.00%)", out
    print("render OK:", out)

    # 5. block syntax disabled — {% ... %} treated as literal text (no variable inside)
    out = render("literal: {% for item in list %} ITEM {% endfor %}", {})
    assert "{% for item in list %}" in out, out
    assert " ITEM " in out
    assert "{% endfor %}" in out
    print("block disabled OK:", out)

    # 6. StrictUndefined -> RenderError
    try:
        render("{{ missing_var }}", {})
        assert False, "should have raised"
    except RenderError as e:
        print("StrictUndefined OK:", e)

    # 7. residual mustache detection
    try:
        out = render('{{ literal }}', {"literal": "has {{x}} inside"})
        assert False, f"should have raised; got {out!r}"
    except RenderError as e:
        print("Residual mustache OK:", e)

    print("=== PHASE 1 ALL TESTS PASS ===")


if __name__ == "__main__":
    main()
