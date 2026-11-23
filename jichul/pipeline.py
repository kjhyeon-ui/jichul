"""UI·CLI 공용 파이프라인: 증빙 폴더 → 판독 → meta 보완 → 초안 → 검증 → docx."""
from __future__ import annotations

import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import llm
from .compute import parse_date, resolve_all
from .models import DraftResult, Receipt, ResolvedExpense, SupportingDoc
from .render import build_docx
from .report import write_review
from .validate import validate

PRE_PURCHASED = {"기차", "고속·시외버스", "항공"}   # 사업일보다 먼저 사는 경우가 많아 사업일 추정에서 뺀다


@dataclass
class Result:
    meta: dict
    receipts: list[Receipt]
    docs: list[tuple[str, SupportingDoc]]
    draft: DraftResult
    expenses: list[ResolvedExpense]
    msgs: list[str]
    files: list[Path]
    review: Path
    notes: list[str] = field(default_factory=list)


# ── 입력 문자열 파싱 ─────────────────────────────────────────────────────────
def parse_attendees_text(text: str) -> dict[str, list[str]]:
    """'본부: 홍길동, 김철수' (한 줄에 한 소속). 소속 없이 이름만 써도 됨."""
    out: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        org, _, rest = re.split(r"([:：])", line, maxsplit=1) if re.search(r"[:：]", line) else ("", "", line)
        names = [n for n in re.split(r"[,，、/\s]+", rest) if n]
        out.setdefault(org.strip(), []).extend(names)
    return out


def parse_payees_text(text: str) -> dict[str, dict]:
    """'홍길동 / 국민 / 000-00-000000' (한 줄에 한 명)."""
    out = {}
    for i, line in enumerate([l for l in (text or "").splitlines() if l.strip()], 1):
        parts = [p.strip() for p in re.split(r"\s*[/,\t]\s*", line.strip())]
        out[f"p{i}"] = {"label": parts[0], "bank": parts[1] if len(parts) > 1 else "", "account": parts[2] if len(parts) > 2 else ""}
    return out


# ── meta 보완 (명시 입력 > 서류에서 읽은 값 > 영수증 추정) ──────────────────────
def _modal_receipt_date(receipts: list[Receipt]) -> str | None:
    pool = [r.date for r in receipts if r.date and r.kind not in PRE_PURCHASED] or [r.date for r in receipts if r.date]
    return Counter(pool).most_common(1)[0][0] if pool else None


def fill_meta(meta: dict, receipts: list[Receipt], docs: list[tuple[str, SupportingDoc]]) -> list[str]:
    notes: list[str] = []
    order = {"서명부": 0, "회의결과": 1, "사업계획서": 2, "기타": 3}
    docs_sorted = sorted(docs, key=lambda x: order[x[1].doc_type])

    if not meta.get("event_date"):
        src = next(((f, d) for f, d in docs_sorted if d.event_date), None)
        if src:
            meta["event_date"] = src[1].event_date
            notes.append(f"사업일을 '{src[0]}'에서 읽음({src[1].event_date}) — 확인하세요")
        elif (m := _modal_receipt_date(receipts)):
            meta["event_date"] = m
            notes.append(f"사업일을 영수증 날짜에서 추정함({m}) — 실제 사업일과 다르면 수정하세요")
        else:
            meta["event_date"] = str(date.today())
            notes.append("사업일을 알 수 없어 오늘 날짜로 둠 — 반드시 수정하세요")

    if not meta.get("attendees"):
        src = next(((f, d) for f, d in docs_sorted if d.attendees), None)
        if src:
            grouped: dict[str, list[str]] = {}
            for a in src[1].attendees:
                names = grouped.setdefault((a.org or "").strip(), [])
                if a.name not in names:
                    names.append(a.name)
            meta["attendees"] = grouped
            n = sum(len(v) for v in grouped.values())
            notes.append(f"참석자 {n}명을 '{src[0]}'에서 읽음 — 이름·인원을 원본과 대조하세요")
        else:
            notes.append("참석자 명단이 없음 — 서명부를 올리거나 직접 입력하세요")
    return notes


def infer_meta(title: str, receipts, docs, *, event_date=None, drafter="", payees_text="", attendees_text="",
               event_type=None) -> tuple[dict, list[str]]:
    meta: dict = {"project_title": title.strip(), "drafter": drafter.strip() or os.environ.get("JICHUL_DRAFTER", "")}
    if not meta["drafter"]:
        meta.pop("drafter")
    if event_date:
        meta["event_date"] = str(event_date)
    if event_type:
        meta["event_type"] = event_type
    if attendees_text.strip():
        meta["attendees"] = parse_attendees_text(attendees_text)
    if payees_text.strip():
        meta["payees"] = parse_payees_text(payees_text)
    return meta, fill_meta(meta, receipts, docs)


# ── 생성·검증·출력 ───────────────────────────────────────────────────────────
def _safe(s: str, n: int = 30) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("_")[:n]


def emit(meta, draft: DraftResult, receipts, out_dir: Path, receipts_dir: Path | None, rules, acc, docs=None, notes=None) -> Result:
    if out_dir.exists():
        shutil.rmtree(out_dir)          # 목 개수가 달라졌을 때 이전 파일이 남지 않도록
    out_dir.mkdir(parents=True)
    exps = resolve_all(meta, draft, receipts, rules)
    msgs = validate(meta, exps, draft, receipts, acc, rules)
    for n in notes or []:
        msgs.append("WARN " + n)
    base = f"{parse_date(meta['event_date']):%y%m%d}_{_safe(meta.get('project_title', '사업'))}"
    files = []
    for i, e in enumerate(exps, 1):
        f = out_dir / f"{base}_{i}_{_safe(e.account_item)}_지출결의서.docx"
        build_docx(e, f, embed_dir=receipts_dir)
        files.append(f)
    rv = write_review(out_dir / f"{base}_검토메모.md", meta, exps, msgs, receipts, draft)
    (out_dir / f"{base}_draft.json").write_text(draft.model_dump_json(indent=1), encoding="utf-8")
    return Result(meta, receipts, docs or [], draft, exps, msgs, files, rv, notes or [])


def load_examples(d: Path) -> list[dict]:
    import json
    return [ex for p in sorted(d.glob("*.json")) if (ex := json.loads(p.read_text(encoding="utf-8"))).get("fewshot")]


def process(client, title: str, evidence_dir: Path, out_dir: Path, rules, acc, examples, model, *,
            on_progress=None, **meta_kw) -> Result:
    """업로드 폴더 전체를 판독해 지출결의서까지 만든다."""
    say = on_progress or (lambda *_: None)
    receipts, docs = llm.extract_dir(client, evidence_dir, model, on_file=lambda i, n, f: say("extract", f"증빙 읽는 중 ({i}/{n}) {f}"))
    return regenerate(client, title, receipts, docs, evidence_dir, out_dir, rules, acc, examples, model, on_progress=say, **meta_kw)


def regenerate(client, title: str, receipts, docs, evidence_dir: Path, out_dir: Path, rules, acc, examples, model, *,
               on_progress=None, **meta_kw) -> Result:
    say = on_progress or (lambda *_: None)
    meta, notes = infer_meta(title, receipts, docs, **meta_kw)
    say("draft", "목 배정·문구 작성 중")
    draft = llm.draft_expenses(client, meta, receipts, acc, rules, examples, model)
    say("render", "검증 및 문서 생성 중")
    return emit(meta, draft, receipts, out_dir, evidence_dir, rules, acc, docs, notes)


# ── 업로드 저장 (API 한도: 이미지 5MB) ───────────────────────────────────────
MAX_IMAGE_BYTES = 4_500_000


def save_upload(name: str, data: bytes, dest_dir: Path) -> Path:
    """업로드 파일을 저장한다. 같은 이름이면 번호를 붙이고, 큰 이미지는 JPG 로 줄인다."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem, suf = Path(name).stem, Path(name).suffix.lower()
    path = dest_dir / f"{stem}{suf}"
    n = 2
    while path.exists():
        path = dest_dir / f"{stem}_{n}{suf}"
        n += 1
    if suf in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and len(data) > MAX_IMAGE_BYTES:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((2200, 2200))
        path = path.with_suffix(".jpg")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data = buf.getvalue()
    path.write_bytes(data)
    return path
