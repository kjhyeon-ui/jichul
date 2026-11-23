"""결정적 검증. LLM 출력이 아니라 규칙·산수로 잡을 수 있는 것만 잡는다.
심각도: ERROR(제출 전 반드시 수정) / WARN(사유 기재 또는 확인) / INFO(체크리스트)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .compute import parse_date
from .models import DraftResult, Receipt, ResolvedExpense


def _months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 31


def validate(meta: dict, expenses: list[ResolvedExpense], draft: DraftResult,
             receipts: list[Receipt], accounts_cfg: dict, rules: dict) -> list[str]:
    msgs: list[str] = []
    names = {a["name"] for a in accounts_cfg["accounts"]}
    by_id = {r.id: r for r in receipts}
    lim = rules["limits"]
    draft_date = parse_date(expenses[0].draft_date) if expenses else date.today()
    event_date = parse_date(meta["event_date"]) if meta.get("event_date") else None

    # 1) 모든 영수증이 정확히 한 번 사용되었는가
    used = defaultdict(list)
    for ei, e in enumerate(expenses):
        for it in e.items:
            for rid in it.receipt_ids:
                used[rid].append(ei)
    for r in receipts:
        n = len(used.get(r.id, []))
        if n == 0 and r.id not in draft.unassigned_receipt_ids:
            msgs.append(f"ERROR 영수증 {r.id}({r.source_file}, {r.amount:,}원)가 어느 항목에도 배정되지 않음")
        if n > 1:
            msgs.append(f"ERROR 영수증 {r.id}({r.source_file}, {r.amount:,}원)가 {n}번 중복 배정됨 — 이중청구 위험")
    for rid in draft.unassigned_receipt_ids:
        r = by_id.get(rid)
        if r:
            msgs.append(f"WARN 미배정 영수증 {rid}({r.source_file}, {r.amount:,}원) — 사업과 무관한지 확인")

    # 2) 영수증 판독 품질
    for r in receipts:
        if r.confidence != "high" or r.issue or r.amount <= 0:
            msgs.append(f"WARN 영수증 {r.id}({r.source_file}) 확인 필요: 신뢰도={r.confidence}"
                        + (f", {r.issue}" if r.issue else "") + (", 금액 0원" if r.amount <= 0 else ""))

    for e in expenses:
        tag = f"[{e.account_item}]"
        # 3) 목 유효성
        if e.account_item not in names:
            msgs.append(f"ERROR {tag} accounts.yaml 에 없는 목")
        if e.total <= 0:
            msgs.append(f"ERROR {tag} 합계가 0원 이하")
        # 4) 입금처
        for k in sorted({i.payee_key for i in e.items if i.payee_key and i.payee_key not in meta.get("payees", {})}):
            msgs.append(f"WARN {tag} 입금처 '{k}'가 입력된 입금처 목록에 없어 계좌가 비어 있음")
        if any(i.payee_key is None for i in e.items):
            msgs.append(f"WARN {tag} 입금처가 지정되지 않은 항목이 있음")
        # 5) 청구 기한(지출일 기준 3개월) — 영수증 날짜 기준
        for it in e.items:
            for rid in it.receipt_ids:
                r = by_id.get(rid)
                if r and r.date:
                    d = parse_date(r.date)
                    if d > draft_date:
                        msgs.append(f"WARN {tag} 영수증 {rid} 날짜({r.date})가 기안일({draft_date})보다 이후")
                    elif _months_between(d, draft_date) > lim["claim_deadline_months"]:
                        msgs.append(f"ERROR {tag} 영수증 {rid} 날짜({r.date})가 기안일 기준 {lim['claim_deadline_months']}개월 초과")
        # 6) 식대 1인당 기준
        meal_total = sum(i.amount for i in e.items if i.kind == "meal")
        if meal_total and e.attendee_count:
            per = meal_total / e.attendee_count
            if per > lim["meal_per_person"]:
                has_reason = any(i.reason for i in e.items if i.kind == "meal")
                msgs.append(f"{'WARN' if has_reason else 'ERROR'} {tag} 식대 1인당 {per:,.0f}원 > 기준 {lim['meal_per_person']:,}원"
                            + ("" if has_reason else " — 초과 사유를 회계감사가 납득할 만큼 상세히 적어야 함"))
        elif meal_total and not e.attendee_count:
            msgs.append(f"WARN {tag} 식대가 있으나 참석자 수를 알 수 없어 1인당 기준 검사 불가")
        # 7) 택시 사유
        for it in e.items:
            if it.kind == "taxi" and not it.reason:
                msgs.append(f"ERROR {tag} 택시 '{it.label}' — 이용 사유 필수(물품이동·대중교통 곤란·귀가 23시 이후 등)")
        # 8) 항공 한도(사업회의 지원)
        if meta.get("event_type"):
            for it in e.items:
                if it.kind == "air" and it.amount > lim["air_cap_event_support"]:
                    msgs.append(f"WARN {tag} 항공료 {it.amount:,}원 > 사업회의 한도 {lim['air_cap_event_support']:,}원")
        # 9) 사업회의 소속별 교통비 지원 인원
        et = accounts_cfg.get("event_types", {}).get(meta.get("event_type", ""), None)
        if et and "travel_support_per_org" in et:
            cap = et["travel_support_per_org"]
            per_org: dict[str, int] = defaultdict(int)
            for it in e.items:
                if it.kind in ("transport", "taxi", "air", "fuel", "toll"):
                    for org in it.claimant_orgs:
                        per_org[org] += 1
            for org, n in per_org.items():
                if n > cap:
                    msgs.append(f"WARN {tag} {org} 교통비 청구 {n}명 > '{meta['event_type']}' 지원 기준 {cap}명 "
                                f"(2026 매뉴얼 사업회의 지원표) — 예외 사유·운영위 결정 확인")
            if et.get("account") and e.account_item != et["account"] and all(
                    it.kind != "fuel" for it in e.items) and e.account_item not in ("출장비", "회의비", "식대"):
                msgs.append(f"WARN {tag} '{meta['event_type']}'의 기본 목은 {et['account']} — 다른 목을 쓴 이유 확인")
        # 10) 자차 유류비 증빙
        if any(i.calc_note for i in e.items):
            msgs.append(f"INFO {tag} 유류비 첨부 필요: 네이버지도 왕복 km 캡처, 오피넷 당일 단가 캡처, 톨게이트 영수증(하이패스는 카드내역)")
        # 11) 참석자
        if e.attendee_count is None:
            msgs.append(f"WARN {tag} 참석자 명단 없음 — 양식에 직접 기입해야 함")
        # 12) 목별 증빙 체크리스트
        acc = next((a for a in accounts_cfg["accounts"] if a["name"] == e.account_item), None)
        if acc and acc.get("evidence"):
            msgs.append(f"INFO {tag} 증빙 확인: " + " / ".join(acc["evidence"]))

    # 13) 목이 여러 개일 때 안내
    if len(expenses) > 1:
        msgs.append("INFO 목이 " + ", ".join(e.account_item for e in expenses) + " 로 나뉘어 지출결의서가 "
                    f"{len(expenses)}장 생성됨 (양식의 '목' 칸은 하나, 매뉴얼은 출장비와 사업비를 구분해 청구하라고 함)")
    return msgs
