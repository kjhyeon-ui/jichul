import json
from pathlib import Path

import pytest

from jichul.compute import resolve_all
from jichul.config import load_accounts, load_rules
from jichul.hangul import amount_phrase
from jichul.models import DraftResult, Receipt
from jichul.validate import validate

EX = Path(__file__).resolve().parent.parent / "examples"
RULES, ACC = load_rules(), load_accounts()


def run(name):
    ex = json.loads((EX / f"{name}.json").read_text(encoding="utf-8"))
    receipts = [Receipt(**r) for r in ex["receipts"]]
    draft = DraftResult(**ex["draft"])
    exps = resolve_all(ex["meta"], draft, receipts, RULES)
    msgs = validate(ex["meta"], exps, draft, receipts, ACC, RULES)
    return ex, exps, msgs


@pytest.mark.parametrize("name", ["ex_250930", "ex_260730", "ex_261015"])
def test_totals_and_hangul_match_samples(name):
    ex, exps, _ = run(name)
    assert len(exps) == 1
    e = exps[0]
    assert e.total == ex["expected"]["total"]
    assert amount_phrase(e.total) == ex["expected"]["hangul"]
    assert e.attendee_count == ex["expected"]["attendee_count"]


def test_fuel_reproduces_260730():
    _, exps, _ = run("ex_260730")
    fuel = next(i for i in exps[0].items if i.kind == "fuel")
    assert fuel.amount == 46_610          # 샘플과 동일 (10원 단위 절사, 항목 합계 기준)


def test_payee_totals_250930():
    _, exps, _ = run("ex_250930")
    lines = exps[0].payee_lines
    assert len(lines) == 4
    assert "₩145,800" in lines[0] and "₩25,600" in lines[1] and "₩17,200" in lines[2] and "₩139,000" in lines[3]


def test_clean_samples_have_no_errors():
    for n in ("ex_250930", "ex_260730"):
        _, _, msgs = run(n)
        assert not [m for m in msgs if m.startswith("ERROR")], msgs


def test_261015_flags_three_chungbuk_claimants():
    _, _, msgs = run("ex_261015")
    assert any("충북 교통비 청구 3명" in m for m in msgs), msgs


def test_double_assignment_is_error():
    ex = json.loads((EX / "ex_250930.json").read_text(encoding="utf-8"))
    ex["draft"]["expenses"][0]["line_items"][1]["receipt_ids"].append("r1")   # r1 중복
    receipts = [Receipt(**r) for r in ex["receipts"]]
    draft = DraftResult(**ex["draft"])
    exps = resolve_all(ex["meta"], draft, receipts, RULES)
    msgs = validate(ex["meta"], exps, draft, receipts, ACC, RULES)
    assert any(m.startswith("ERROR") and "중복 배정" in m for m in msgs)


def test_meal_over_limit_needs_reason():
    ex = json.loads((EX / "ex_250930.json").read_text(encoding="utf-8"))
    ex["receipts"][-1]["amount"] = 200_000                                      # 12명 -> 16,667원/인
    receipts = [Receipt(**r) for r in ex["receipts"]]
    draft = DraftResult(**ex["draft"])
    exps = resolve_all(ex["meta"], draft, receipts, RULES)
    msgs = validate(ex["meta"], exps, draft, receipts, ACC, RULES)
    assert any(m.startswith("ERROR") and "식대 1인당" in m for m in msgs)
