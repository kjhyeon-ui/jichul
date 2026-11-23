from __future__ import annotations

import json

EXTRACT_SYSTEM = """당신은 영수증·승차권·카드/이체 내역을 판독해 구조화하는 보조자입니다.
- 이미지/문서에 실제로 보이는 값만 기록합니다. 보이지 않거나 흐리거나 잘린 값은 추측하지 말고 null(금액은 0)로 두고, issue 에 이유를 적고 confidence 를 낮춥니다.
- 한 파일에 여러 장이 있으면 장마다 별도 항목으로 기록합니다(왕복 승차권 2장 = 2개 항목, 한 장에 '일반 3'처럼 여러 명 요금이 합산돼 있으면 합산 결제금액 그대로 1개 항목, passengers 에 인원).
- 같은 영수증이 두 번 찍힌 것으로 보이면 한 번만 기록하고 issue 에 적습니다.
- 취소·환불 표시가 있으면 issue 에 적습니다.
- amount 는 결제금액(원, 정수)입니다. 금액 칸이 여러 개면 '실제 결제/영수액'을 고르고, 애매하면 issue 에 후보를 적습니다.
- 영수증이 아닌 서류(참석자 서명부, 회의결과, 사업계획서, 웹자보 등)는 documents 에 기록합니다. 서명부·회의결과의 참석자는 또렷하게 읽히는 이름만 넣고, 손글씨가 불분명하면 넣지 말고 issue 에 적습니다. 일자는 서류에 명시된 것만 씁니다.
- 지도 캡처·유가(오피넷) 캡처는 영수증이 아니므로 documents(기타)로 기록하고 summary 에 거리/단가를 적되, 보이는 값만 적습니다.
- 반드시 submit_receipts 도구로만 답합니다."""


def _account_guide(accounts_cfg: dict) -> str:
    lines = []
    for a in accounts_cfg["accounts"]:
        s = f"- {a['name']}: {' '.join(str(a.get('use_when', '')).split())}"
        if a.get("notes"):
            s += f" / 참고: {' '.join(str(a['notes']).split())}"
        if a.get("evidence"):
            s += f" / 증빙: {', '.join(a['evidence'])}"
        lines.append(s)
    ev = [f"- {k}: 기본 목 {v['account']}" + (f", 소속별 교통비 지원 {v['travel_support_per_org']}명" if 'travel_support_per_org' in v else "")
          for k, v in accounts_cfg.get("event_types", {}).items()]
    return "\n".join(lines) + "\n\n[사업/회의 유형별 기본 목]\n" + "\n".join(ev)


def strip_secrets(meta: dict) -> dict:
    """API 로 보내는 meta 에서 계좌번호는 뺀다(초안 작성에 필요 없음)."""
    m = json.loads(json.dumps(meta, ensure_ascii=False, default=str))
    for k, p in (m.get("payees") or {}).items():
        m["payees"][k] = {"label": p.get("label") or p.get("name") or k}
    return m


def draft_system(accounts_cfg: dict, rules: dict, examples: list[dict]) -> str:
    lim = rules["limits"]
    shots = []
    for ex in examples:
        shots.append("<example>\n[입력]\n" + json.dumps(
            {"meta": strip_secrets(ex["meta"]), "receipts": ex["receipts"]}, ensure_ascii=False, indent=1)
            + "\n[출력 — submit_draft 도구 입력]\n" + json.dumps(ex["draft"], ensure_ascii=False, indent=1) + "\n</example>")
    return f"""당신은 의료연대본부 일반회계 지출결의서 초안 작성 보조자입니다. 영수증 추출 결과와 사업 정보(meta)를 받아, 목(계정과목)별로 지출결의서 초안을 만듭니다.

[절대 원칙]
1. 입력에 없는 사실(이름, 인원, 소속, 계좌, 거리, 단가, 사유, 일정)을 만들지 않습니다. 모르면 비워 두고 questions 에 사람이 답할 질문을 적습니다.
2. 금액을 계산하거나 문장에 적지 않습니다. 항목은 receipt_ids / fuel_trip_ids 로 영수증·유류 계산을 묶기만 하면 코드가 금액을 계산해 붙입니다. detail 에 금액을 쓰지 마세요.
3. 지출결의서 한 장에는 목 하나만 씁니다. 한 사업에 목이 섞이면(예: 상집 출장비 + 대표자 조직사업비) 목별로 expenses 를 나눕니다. 상집 간부의 교통비·식대는 출장비, 상집이 아닌 참석자의 회의 교통비·식대는 회의비 또는 사업비입니다.
4. 모든 영수증은 정확히 한 항목에 배정하거나 unassigned_receipt_ids 에 넣습니다. 같은 영수증을 두 곳에 넣지 않습니다.
5. 택시, 기준 초과 식대, 카페 이용은 사유가 입력에 있을 때만 reason 에 적습니다. 없으면 reason 을 비우고 questions 에 사유를 묻습니다.
6. 영수증의 kind 와 모순되는 목 배정을 하지 않습니다(예: 대관료를 출장비로 넣지 않음). 판단이 애매하면 가장 규정에 맞는 목을 고르되 reviewer_notes 에 근거와 대안을 적습니다.
7. 한국어로, 항목 문구는 짧고 사실만 씁니다(예: '춘천↔청량리 왕복 1명').
8. 규정 문서가 서로 다르면 2026 매뉴얼[26]을 우선합니다.

[수치 기준 — 코드가 따로 검증하므로 참고용]
식대 1인 {lim['meal_per_person']:,}원 이내, 다과 1인 {lim['snack_per_person']:,}원, 숙박 1박 {lim['lodging_per_night']:,}원(2인1실), 사업회의 항공료 {lim['air_cap_event_support']:,}원 한도, 지출일로부터 {lim['claim_deadline_months']}개월 이내 청구.

[목(계정과목) 기준]
{_account_guide(accounts_cfg)}

{chr(10).join(shots)}

반드시 submit_draft 도구로만 답합니다."""


def draft_user(meta: dict, receipts: list[dict]) -> str:
    return ("[입력]\n" + json.dumps({"meta": strip_secrets(meta), "receipts": receipts}, ensure_ascii=False, indent=1)
            + "\n\n위 자료만으로 지출결의서 초안을 만드세요.")
