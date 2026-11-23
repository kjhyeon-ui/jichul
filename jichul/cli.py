from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .compute import parse_date, resolve_all
from .config import ROOT, load_accounts, load_rules, load_yaml
from .models import DraftResult, Receipt
from .render import build_docx
from .report import write_review
from .validate import validate


def _safe(s: str, n: int = 30) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("_")[:n]


def _load_examples(d: Path) -> list[dict]:
    out = []
    for p in sorted(d.glob("*.json")):
        ex = json.loads(p.read_text(encoding="utf-8"))
        if ex.get("fewshot"):
            out.append(ex)
    return out


def _load_receipts(p: Path) -> list[Receipt]:
    return [Receipt(**r) for r in json.loads(p.read_text(encoding="utf-8"))]


def _save_receipts(p: Path, rs: list[Receipt]):
    p.write_text(json.dumps([r.model_dump() for r in rs], ensure_ascii=False, indent=1), encoding="utf-8")


def _emit(meta, draft: DraftResult, receipts, out_dir: Path, receipts_dir: Path | None, rules, acc) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    exps = resolve_all(meta, draft, receipts, rules)
    msgs = validate(meta, exps, draft, receipts, acc, rules)
    d = parse_date(meta["event_date"])
    base = f"{d:%y%m%d}_{_safe(meta.get('project_title', '사업'))}"
    files = []
    for i, e in enumerate(exps, 1):
        f = out_dir / f"{base}_{i}_{_safe(e.account_item)}_지출결의서.docx"
        build_docx(e, f, embed_dir=receipts_dir)
        files.append(f)
    rv = write_review(out_dir / f"{base}_검토메모.md", meta, exps, msgs, receipts, draft)
    (out_dir / f"{base}_draft.json").write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    for f in files + [rv]:
        print("생성:", f)
    n_err = sum(m.startswith("ERROR") for m in msgs)
    n_warn = sum(m.startswith("WARN") for m in msgs)
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
        rs = llm.extract_dir(client, a.receipts_dir, model)
        _save_receipts(a.out, rs)
        print(f"{len(rs)}건 판독 → {a.out}  (금액·날짜를 원본과 대조한 뒤 수정하세요)")
        return 0

    meta = load_yaml(a.meta)
    if a.cmd == "draft":
        draft = llm.draft_expenses(client, meta, _load_receipts(a.receipts), acc, rules, _load_examples(a.examples_dir), model)
        a.out.write_text(draft.model_dump_json(indent=1), encoding="utf-8")
        print("초안 →", a.out)
        return 0

    if a.cmd == "render":
        draft = DraftResult(**json.loads(a.draft.read_text(encoding="utf-8")))
        return _emit(meta, draft, _load_receipts(a.receipts), a.out_dir, a.receipts_dir, rules, acc)

    if a.cmd == "run":
        rs = llm.extract_dir(client, a.receipts_dir, model)
        a.out_dir.mkdir(parents=True, exist_ok=True)
        _save_receipts(a.out_dir / "receipts.json", rs)
        draft = llm.draft_expenses(client, meta, rs, acc, rules, _load_examples(a.examples_dir), model)
        return _emit(meta, draft, rs, a.out_dir, a.receipts_dir, rules, acc)
    return 2


if __name__ == "__main__":
    sys.exit(main())
