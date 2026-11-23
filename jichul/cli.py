from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ROOT, load_accounts, load_rules, load_yaml
from .models import DraftResult, Receipt
from .pipeline import emit, fill_meta, load_examples


def _load_receipts(p: Path) -> list[Receipt]:
    return [Receipt(**r) for r in json.loads(p.read_text(encoding="utf-8"))]


def _save_receipts(p: Path, rs: list[Receipt]):
    p.write_text(json.dumps([r.model_dump() for r in rs], ensure_ascii=False, indent=1), encoding="utf-8")


def _emit(meta, draft: DraftResult, receipts, out_dir: Path, receipts_dir: Path | None, rules, acc, docs=None) -> int:
    notes = fill_meta(meta, receipts, docs or []) if "event_date" not in meta else []
    r = emit(meta, draft, receipts, out_dir, receipts_dir, rules, acc, docs, notes)
    for f in r.files + [r.review]:
        print("생성:", f)
    n_err = sum(m.startswith("ERROR") for m in r.msgs)
    n_warn = sum(m.startswith("WARN") for m in r.msgs)
    print(f"검증: 오류 {n_err}건, 경고 {n_warn}건 — 검토메모를 확인하세요")
    return 1 if n_err else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jichul", description="지출결의서 자동 작성기")
    ap.add_argument("--config-dir", type=Path, default=None)
    ap.add_argument("--examples-dir", type=Path, default=ROOT / "examples")
    ap.add_argument("--model", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("extract", help="영수증 폴더 → receipts.json (Claude 비전)")
    s.add_argument("--receipts-dir", type=Path, required=True)
    s.add_argument("--out", type=Path, default=Path("receipts.json"))

    s = sub.add_parser("draft", help="meta + receipts → draft.json (Claude)")
    s.add_argument("--meta", type=Path, required=True)
    s.add_argument("--receipts", type=Path, required=True)
    s.add_argument("--out", type=Path, default=Path("draft.json"))

    s = sub.add_parser("render", help="draft.json → docx + 검토메모 (API 호출 없음)")
    s.add_argument("--meta", type=Path, required=True)
    s.add_argument("--receipts", type=Path, required=True)
    s.add_argument("--draft", type=Path, required=True)
    s.add_argument("--receipts-dir", type=Path, default=None)
    s.add_argument("--out-dir", type=Path, default=Path("out"))

    s = sub.add_parser("run", help="전체 실행: 영수증 폴더 + meta → docx")
    s.add_argument("--meta", type=Path, required=True)
    s.add_argument("--receipts-dir", type=Path, required=True)
    s.add_argument("--out-dir", type=Path, default=Path("out"))

    s = sub.add_parser("demo", help="예시 JSON 으로 API 없이 docx 생성")
    s.add_argument("example", type=Path)
    s.add_argument("--out-dir", type=Path, default=Path("out"))

    a = ap.parse_args(argv)
    rules, acc = load_rules(a.config_dir), load_accounts(a.config_dir)

    if a.cmd == "demo":
        ex = json.loads(a.example.read_text(encoding="utf-8"))
        return _emit(ex["meta"], DraftResult(**ex["draft"]), [Receipt(**r) for r in ex["receipts"]], a.out_dir, None, rules, acc)

    from . import llm
    model = a.model or llm.DEFAULT_MODEL
    client = llm.make_client()

    if a.cmd == "extract":
        rs, docs = llm.extract_dir(client, a.receipts_dir, model, on_file=lambda i, n, f: print(f"[{i}/{n}] {f}"))
        _save_receipts(a.out, rs)
        print(f"영수증 {len(rs)}건, 기타 증빙 {len(docs)}건 판독 → {a.out}  (금액·날짜를 원본과 대조한 뒤 수정하세요)")
        return 0

    meta = load_yaml(a.meta)
    if a.cmd == "draft":
        draft = llm.draft_expenses(client, meta, _load_receipts(a.receipts), acc, rules, load_examples(a.examples_dir), model)
        a.out.write_text(draft.model_dump_json(indent=1), encoding="utf-8")
        print("초안 →", a.out)
        return 0

    if a.cmd == "render":
        draft = DraftResult(**json.loads(a.draft.read_text(encoding="utf-8")))
        return _emit(meta, draft, _load_receipts(a.receipts), a.out_dir, a.receipts_dir, rules, acc)

    if a.cmd == "run":
        rs, docs = llm.extract_dir(client, a.receipts_dir, model, on_file=lambda i, n, f: print(f"[{i}/{n}] {f}"))
        for n in fill_meta(meta, rs, docs):
            print("참고:", n)
        draft = llm.draft_expenses(client, meta, rs, acc, rules, load_examples(a.examples_dir), model)
        rc = _emit(meta, draft, rs, a.out_dir, a.receipts_dir, rules, acc, docs)
        _save_receipts(a.out_dir / "receipts.json", rs)
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())
