"""API 키 없이 llm.py 의 요청 구성·응답 파싱·재시도 경로를 검증한다. (실제 모델 품질은 검증하지 않는다.)"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from jichul import llm
from jichul.config import load_accounts, load_rules
from jichul.models import Receipt

EX = Path(__file__).resolve().parent.parent / "examples"


class FakeClient:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        return NS(content=[NS(type="tool_use", name=kw["tool_choice"]["name"], input=out)], stop_reason="tool_use")


def _ex(n):
    return json.loads((EX / f"{n}.json").read_text(encoding="utf-8"))


def test_inline_refs_has_no_refs():
    from jichul.models import DraftResult
    s = json.dumps(llm.inline_refs(DraftResult.model_json_schema()))
    assert "$ref" not in s and "$defs" not in s


def test_extract_file_parses_and_sends_image(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake = FakeClient([{"receipts": [{"kind": "기차", "amount": 8600, "confidence": "high"}]}])
    rs = llm.extract_file(fake, img)
    assert rs[0].amount == 8600
    block = fake.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"


def test_unsupported_format_rejected(tmp_path):
    f = tmp_path / "a.heic"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        llm.extract_file(FakeClient([]), f)


def test_draft_sends_no_bank_accounts_and_parses():
    ex = _ex("ex_250930")
    fake = FakeClient([ex["draft"]])
    res = llm.draft_expenses(fake, ex["meta"], [Receipt(**r) for r in ex["receipts"]],
                             load_accounts(), load_rules(), [ex, _ex("ex_260730")])
    assert res.expenses[0].account_item == "조직사업비"
    sent = json.dumps(fake.calls[0], ensure_ascii=False)
    assert "000-00-000000-0" not in sent      # 계좌번호는 API 로 보내지 않는다
    assert "<example>" in fake.calls[0]["system"]


def test_draft_retries_once_on_invalid_output():
    ex = _ex("ex_260730")
    fake = FakeClient([{"expenses": [{"account_item": "전략사업비"}]}, ex["draft"]])   # 첫 응답은 필수 필드 누락
    res = llm.draft_expenses(fake, ex["meta"], [Receipt(**r) for r in ex["receipts"]],
                             load_accounts(), load_rules(), [])
    assert len(fake.calls) == 2 and res.expenses[0].line_items
