"""금액·문구 계산은 전부 코드가 한다. LLM은 영수증 id를 묶고 문장만 쓴다."""
from __future__ import annotations

import math
from datetime import date

from .models import DraftResult, ExpenseDraft, Receipt, ResolvedExpense, ResolvedItem

WEEKDAY = "월화수목금토일"


def fmt_date_ko(d: date, with_weekday: bool = False) -> str:
    s = f"{d.year}년 {d.month}월 {d.day}일"
    return f"{s}({WEEKDAY[d.weekday()]})" if with_weekday else s


def parse_date(s) -> date:
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s))


def event_line(meta: dict) -> str:
    d = parse_date(meta["event_date"])
    s = fmt_date_ko(d, with_weekday=True)
    if meta.get("event_time"):
        s += f" {meta['event_time']}"
    return s


def attendees(meta: dict) -> tuple[str, int | None]:
    """meta.attendees = {소속: [이름,...]} -> '본부 A,B/서울 C/총 N명'. 없으면 자리표시."""
    att = meta.get("attendees")
    if not att:
        return "[참석자 명단 필요 — 모두 기입하고 총 몇 명인지도 기입]", None
    parts, n = [], 0
    for org, names in att.items():
        names = list(names or [])
        n += len(names)
        parts.append(f"{org} {','.join(names)}".strip())
    return "/".join(parts) + f"/총 {n}명", n


def fuel_raw(trip: dict, fuel_cfg: dict) -> tuple[float, str]:
    ft = trip["fuel_type"]
    if ft in ("ev", "전기"):
        raise ValueError("전기차는 영수증 전액 — fuel_trips 대신 영수증으로 처리하세요")
    key = {"휘발유": "gasoline", "경유": "diesel", "LPG": "lpg"}.get(ft, ft)
    div = fuel_cfg["divisor"][key]
    km, price = float(trip["km"]), float(trip["unit_price"])
    raw = km * price / div
    return raw, f"{km:g}km × {price:,.0f}원 ÷ {div} = {raw:,.2f}원"


def round_amount(raw: float, fuel_cfg: dict) -> int:
    unit = fuel_cfg["round_unit"]
    q = math.floor(raw / unit) if fuel_cfg["round_mode"] == "floor" else round(raw / unit)
    return int(q * unit)


def payee_text(key: str, payees: dict, amount: int | None) -> str:
    p = payees.get(key, {})
    label = p.get("label") or p.get("name") or key
    bank = " ".join(x for x in (p.get("bank"), p.get("account")) if x)
    s = f"{label} : {bank}" if bank else f"{label} : [계좌 확인 필요]"
    if amount is not None:
        s += f" / ₩{amount:,}"
    return s


def resolve(meta: dict, draft: ExpenseDraft, receipts: dict[str, Receipt], rules: dict) -> ResolvedExpense:
    fuel_trips = {t["id"]: t for t in meta.get("fuel_trips", [])}
    payees = meta.get("payees", {})
    att_line, att_n = attendees(meta)
    warnings: list[str] = []
    items: list[ResolvedItem] = []
    files: list[str] = []

    only_payee = next(iter(payees)) if len(payees) == 1 else None
    defaulted = False
    for li in draft.line_items:
        if li.payee_key is None and only_payee:
            li = li.model_copy(update={"payee_key": only_payee})
            defaulted = True
        amt, notes = 0, []
        for rid in li.receipt_ids:
            r = receipts.get(rid)
            if r is None:
                warnings.append(f"[오류] '{li.label}': 존재하지 않는 영수증 id {rid}")
                continue
            amt += r.amount
            if r.source_file not in files:
                files.append(r.source_file)
        raw_sum, any_trip = 0.0, False
        for tid in li.fuel_trip_ids:
            t = fuel_trips.get(tid)
            if t is None:
                warnings.append(f"[오류] '{li.label}': 존재하지 않는 fuel_trip id {tid}")
                continue
            raw, note = fuel_raw(t, rules["fuel"])
            raw_sum += raw
            any_trip = True
            notes.append(f"{t.get('route', tid)}: {note}")
        if any_trip:
            rounded = round_amount(raw_sum, rules["fuel"])
            amt += rounded
            notes.append(f"합계 {raw_sum:,.2f}원 → {rounded:,}원 ({rules['fuel']['round_unit']}원 단위 절사, 유류비 항목 합계 기준)")
        items.append(ResolvedItem(
            label=li.label, detail=li.detail, kind=li.kind, amount=amt, receipt_ids=li.receipt_ids,
            payee_key=li.payee_key, claimant_orgs=li.claimant_orgs, reason=li.reason,
            calc_note=" | ".join(notes) or None,
        ))

    if defaulted:
        warnings.append("[참고] 입금처가 1명만 입력되어 입금처가 비어 있던 항목에 그 사람을 지정함 — 맞는지 확인하세요")
    total = sum(i.amount for i in items)

    # 입금처: payee_key 별 합계
    by_payee: dict[str, int] = {}
    for i in items:
        by_payee[i.payee_key or "__none__"] = by_payee.get(i.payee_key or "__none__", 0) + i.amount
    lines = []
    multi = len([k for k in by_payee if k != "__none__"]) > 1
    for k, a in by_payee.items():
        if k == "__none__":
            lines.append(f"[입금처 확인 필요] / ₩{a:,}")
        else:
            lines.append(payee_text(k, payees, a if multi or "__none__" in by_payee else None))

    return ResolvedExpense(
        account_item=draft.account_item, title=draft.title, event_line=event_line(meta),
        drafter=meta.get("drafter", "[기안자 필요]"),
        draft_date=str(parse_date(meta.get("draft_date") or date.today())),
        attendees_line=att_line, attendee_count=att_n, items=items, total=total,
        payee_lines=lines, attachments=list(draft.attachments), attachment_files=files,
        warnings=warnings, questions=[],
    )


def resolve_all(meta: dict, result: DraftResult, receipts: list[Receipt], rules: dict) -> list[ResolvedExpense]:
    by_id = {r.id: r for r in receipts}
    out = []
    for e in result.expenses:
        re_ = resolve(meta, e, by_id, rules)
        re_.questions = list(result.questions)
        re_.warnings += [f"[검토메모] {n}" for n in e.reviewer_notes]
        out.append(re_)
    return out
