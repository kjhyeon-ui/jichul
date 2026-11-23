from __future__ import annotations

from pathlib import Path

from .hangul import amount_phrase
from .models import DraftResult, Receipt, ResolvedExpense


def write_review(path: Path, meta: dict, expenses: list[ResolvedExpense], msgs: list[str],
                 receipts: list[Receipt], draft: DraftResult) -> Path:
    errs = [m for m in msgs if m.startswith("ERROR")]
    warns = [m for m in msgs if m.startswith("WARN")]
    infos = [m for m in msgs if m.startswith("INFO")]
    L = [f"# 검토 메모 — {meta.get('project_title', '')}", "",
         "이 파일은 제출용이 아닙니다. 지출결의서 제출 전 사람이 확인할 항목입니다.", "",
         f"- 오류 {len(errs)}건 / 경고 {len(warns)}건 / 참고 {len(infos)}건", ""]
    L += ["## 생성된 지출결의서", "", "| 목 | 합계 | 한글 표기 | 항목 수 | 참석자 수 |", "|---|---:|---|---:|---:|"]
    for e in expenses:
        L.append(f"| {e.account_item} | {e.total:,}원 | {amount_phrase(e.total)} | {len(e.items)} | {e.attendee_count or '미입력'} |")
    L.append("")
    for title, group in (("오류 (제출 전 수정)", errs), ("경고 (사유 기재 또는 확인)", warns), ("참고 (증빙 체크리스트)", infos)):
        if group:
            L += [f"## {title}", ""] + [f"- {m.split(' ', 1)[1]}" for m in group] + [""]
    if draft.questions:
        L += ["## 모델이 사람에게 묻는 것", ""] + [f"- {q}" for q in draft.questions] + [""]
    other = [w for e in expenses for w in e.warnings]
    if other:
        L += ["## 기타", ""] + [f"- {w}" for w in other] + [""]
    L += ["## 영수증 판독 결과 (원본과 대조하세요)", "", "| id | 파일 | 날짜 | 종류 | 금액 | 신뢰도 | 비고 |", "|---|---|---|---|---:|---|---|"]
    for r in receipts:
        L.append(f"| {r.id} | {r.source_file} | {r.date or '-'} | {r.kind} | {r.amount:,} | {r.confidence} | {r.issue or ''} |")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")
    return path
