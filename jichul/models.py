"""데이터 모델. LLM이 채우는 부분(Receipt 추출, Draft)과 코드가 계산하는 부분(Resolved*)을 분리한다."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ReceiptKind = Literal[
    "식대", "기차", "고속·시외버스", "택시", "항공", "기타교통", "유류비", "톨게이트",
    "주차", "숙박", "대관료", "물품구입", "강사료·발제비", "기타",
]
Confidence = Literal["high", "medium", "low"]


# ── 1단계: 영수증 추출 (LLM, 이미지/PDF → 구조화) ────────────────────────────
class ReceiptRaw(BaseModel):
    date: Optional[str] = Field(None, description="결제일 YYYY-MM-DD. 영수증에 없으면 null")
    time: Optional[str] = Field(None, description="HH:MM. 없으면 null")
    vendor: Optional[str] = Field(None, description="가맹점·운송사·발행처")
    kind: ReceiptKind
    amount: int = Field(description="이 영수증 한 장의 결제금액(원). 판독 불가면 0")
    route_or_items: Optional[str] = Field(None, description="구간(출발→도착) 또는 품목 요약. 보이는 것만")
    passengers: Optional[str] = Field(None, description="탑승자명/인원. 보이는 것만")
    confidence: Confidence
    issue: Optional[str] = Field(None, description="흐림·잘림·취소/환불·중복 가능성 등 사람이 봐야 할 문제")


class ExtractionResult(BaseModel):
    receipts: list[ReceiptRaw] = Field(description="파일 안에서 식별한 영수증 전부(한 파일에 여러 장 가능)")


class Receipt(ReceiptRaw):
    id: str
    source_file: str


# ── 2단계: 초안 (LLM) — 금액은 쓰지 않는다. 영수증 id만 묶는다 ──────────────────
ItemKind = Literal["meal", "transport", "taxi", "air", "fuel", "toll", "lodging", "venue", "goods", "fee", "other"]


class LineItem(BaseModel):
    label: str = Field(description="예: '강원 교통비', '점심 식사', '유류비'")
    detail: str = Field(description="예: '춘천↔청량리 왕복 1명'. 금액은 쓰지 말 것(코드가 붙임)")
    kind: ItemKind
    receipt_ids: list[str] = Field(default_factory=list)
    fuel_trip_ids: list[str] = Field(default_factory=list, description="meta.fuel_trips 의 id")
    payee_key: Optional[str] = Field(None, description="meta.payees 의 key. 모르면 null")
    claimant_orgs: list[str] = Field(default_factory=list, description="교통비를 청구하는 사람 1명당 소속(분회/지부) 1개. 예: 2명이 각각 경대·동산이면 ['경대간병','동산간병']. 모르면 빈 리스트")
    reason: Optional[str] = Field(None, description="택시 이용·기준 초과·카페 이용 등 사유. 자료에 있는 것만")


class ExpenseDraft(BaseModel):
    account_item: str = Field(description="목. accounts.yaml 의 name 중 하나. 지출결의서 한 장에는 목 하나만")
    title: str = Field(description="<사업/회의/출장명>")
    line_items: list[LineItem]
    attachments: list[str] = Field(default_factory=list, description="<첨부>에 적을 증빙 이름")
    reviewer_notes: list[str] = Field(default_factory=list)


class DraftResult(BaseModel):
    expenses: list[ExpenseDraft] = Field(description="목별로 나눈 지출결의서들")
    unassigned_receipt_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, description="자료가 부족해 사람이 답해야 할 것")


# ── 3단계: 코드가 계산한 결과 ───────────────────────────────────────────────
class ResolvedItem(BaseModel):
    label: str
    detail: str
    kind: str
    amount: int
    receipt_ids: list[str]
    payee_key: Optional[str]
    claimant_orgs: list[str] = Field(default_factory=list)
    reason: Optional[str] = None
    calc_note: Optional[str] = None


class ResolvedExpense(BaseModel):
    account_item: str
    title: str
    event_line: str
    drafter: str
    draft_date: str
    attendees_line: str
    attendee_count: Optional[int]
    items: list[ResolvedItem]
    total: int
    payee_lines: list[str]
    attachments: list[str]
    attachment_files: list[str]
    warnings: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
