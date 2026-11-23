import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

from jichul import pipeline
from jichul.config import load_accounts, load_rules
from jichul.models import Receipt, SupportingDoc, AttendeeEntry

EX = Path(__file__).resolve().parent.parent / "examples"
ROOT = EX.parent
RULES, ACC = load_rules(), load_accounts()


class FakeClient:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return NS(content=[NS(type="tool_use", name=kw["tool_choice"]["name"], input=self.outputs.pop(0))], stop_reason="tool_use")


def _ex(n):
    return json.loads((EX / f"{n}.json").read_text(encoding="utf-8"))


def test_parse_attendees_and_payees():
    a = pipeline.parse_attendees_text("본부: 홍길동, 김철수\n충북：이영희\n박민수 최하나")
    assert a == {"본부": ["홍길동", "김철수"], "충북": ["이영희"], "": ["박민수", "최하나"]}
    p = pipeline.parse_payees_text("홍길동 / 국민 / 000-00-000000\n충북 이영희 / 농협 / 111-1111")
    assert p["p1"] == {"label": "홍길동", "bank": "국민", "account": "000-00-000000"}
    assert p["p2"]["label"] == "충북 이영희"


def test_event_date_ignores_pre_purchased_tickets():
    rs = [Receipt(id="r1", source_file="a", date="2026-10-06", kind="고속·시외버스", amount=1, confidence="high"),
          Receipt(id="r2", source_file="b", date="2026-10-15", kind="식대", amount=1, confidence="high")]
    meta, notes = pipeline.infer_meta("x", rs, [])
    assert meta["event_date"] == "2026-10-15" and any("추정" in n for n in notes)


def test_explicit_input_beats_documents():
    doc = SupportingDoc(doc_type="서명부", event_date="2026-01-01", summary="s",
                        attendees=[AttendeeEntry(org="본부", name="가")])
    meta, notes = pipeline.infer_meta("x", [], [("s.png", doc)], event_date="2026-02-02", attendees_text="충북: 나")
    assert meta["event_date"] == "2026-02-02" and meta["attendees"] == {"충북": ["나"]}


def test_full_process_with_mock_client(tmp_path):
    ex = _ex("ex_250930")
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "all.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (ev / "sign.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    raw = [{k: r[k] for k in ("date", "time", "vendor", "kind", "amount", "route_or_items", "passengers", "confidence", "issue")}
           for r in ex["receipts"]]
    sign = {"documents": [{"doc_type": "서명부", "event_date": "2025-09-30", "summary": "서명부",
                           "attendees": [{"org": o, "name": n} for o, ns in ex["meta"]["attendees"].items() for n in ns]}]}
    # 파일 정렬순: all.png → sign.png
    for li in ex["draft"]["expenses"][0]["line_items"]:
        li["payee_key"] = None                                           # 입금처 1명만 입력 → 코드가 지정
    client = FakeClient([{"receipts": raw}, sign, ex["draft"]])
    res = pipeline.process(client, "희망간병 대표자회의", ev, tmp_path / "out", RULES, ACC, [ex], "m",
                           drafter="기안자A", payees_text="기안자A / 국민 / 123-45-678901")
    assert len(res.files) == 1 and res.files[0].exists()
    assert res.expenses[0].total == 327_600
    assert res.expenses[0].attendee_count == 12          # 서명부에서 읽음
    assert res.meta["event_date"] == "2025-09-30"
    assert any("sign.png" in n for n in res.notes)                       # 어느 파일에서 읽었는지 표시
    # 계좌번호는 어떤 API 호출에도 실리지 않고, 문서에는 들어간다
    assert all("123-45-678901" not in json.dumps(c, ensure_ascii=False) for c in client.calls)
    assert any("123-45-678901" in l for l in res.expenses[0].payee_lines)


def test_regenerate_clears_old_outputs(tmp_path):
    ex = _ex("ex_250930")
    rs = [Receipt(**r) for r in ex["receipts"]]
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.docx").write_bytes(b"x")
    pipeline.regenerate(FakeClient([ex["draft"]]), "t", rs, [], None, out, RULES, ACC, [], "m",
                        event_date="2025-09-30", drafter="A")
    assert not (out / "stale.docx").exists()


def test_save_upload_dedupes_and_shrinks(tmp_path):
    import io
    from PIL import Image
    a = pipeline.save_upload("r.pdf", b"%PDF", tmp_path)
    b = pipeline.save_upload("r.pdf", b"%PDF", tmp_path)
    assert a.name == "r.pdf" and b.name == "r_2.pdf"
    import random
    img = Image.effect_noise((3000, 3000), 80).convert("RGB")
    buf = io.BytesIO(); img.save(buf, "PNG")
    assert len(buf.getvalue()) > pipeline.MAX_IMAGE_BYTES
    p = pipeline.save_upload("big.png", buf.getvalue(), tmp_path)
    assert p.suffix == ".jpg" and p.stat().st_size < pipeline.MAX_IMAGE_BYTES


def test_app_renders_two_required_inputs(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.delenv("JICHUL_PASSWORD", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert at.text_input[0].label == "사업명"
    btn = next(b for b in at.button if b.label == "지출결의서 만들기")
    assert btn.disabled                                   # 사업명·파일 없으면 비활성


def test_app_password_gate(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("JICHUL_PASSWORD", "pw123")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert at.text_input[0].label == "비밀번호" and not any(t.label == "사업명" for t in at.text_input)
    at.text_input[0].set_value("wrong").run()
    assert any("맞지 않습니다" in e.value for e in at.error)
    at.text_input[0].set_value("pw123").run()
    assert any(t.label == "사업명" for t in at.text_input)


def test_render_embeds_valid_image_and_survives_corrupt_one(tmp_path):
    from PIL import Image
    from docx import Document
    from jichul.compute import resolve_all
    from jichul.models import DraftResult
    from jichul.render import build_docx
    ex = _ex("ex_260730")
    ex["receipts"][0]["source_file"] = "ok.png"
    ex["draft"]["expenses"][0]["line_items"].append(
        {"label": "기타", "detail": "테스트", "kind": "other", "receipt_ids": ["r2"], "payee_key": "drafter"})
    ex["receipts"].append(dict(ex["receipts"][0], id="r2", source_file="bad.png", amount=1000))
    Image.new("RGB", (200, 100), "white").save(tmp_path / "ok.png")
    (tmp_path / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rs = [Receipt(**r) for r in ex["receipts"]]
    exp = resolve_all(ex["meta"], DraftResult(**ex["draft"]), rs, RULES)[0]
    out = build_docx(exp, tmp_path / "o.docx", embed_dir=tmp_path)
    d = Document(out)
    assert len(d.inline_shapes) == 1                                    # ok.png 만 삽입
    assert any("[수동 첨부 필요] bad.png" in p.text for p in d.paragraphs)


def test_unknown_payee_key_warns():
    from jichul.compute import resolve_all
    from jichul.models import DraftResult
    from jichul.validate import validate
    ex = _ex("ex_250930")
    ex["meta"]["payees"] = {"p1": {"label": "A"}, "p2": {"label": "B"}}     # 키가 draft 와 다름
    rs = [Receipt(**r) for r in ex["receipts"]]
    d = DraftResult(**ex["draft"])
    exps = resolve_all(ex["meta"], d, rs, RULES)
    msgs = validate(ex["meta"], exps, d, rs, ACC, RULES)
    assert any("'daegu'가 입력된 입금처 목록에 없어" in m for m in msgs)
