# 지출결의서 자동 작성기 (의료연대본부 일반회계)

입력: 사업명 등 meta(YAML) + 영수증 폴더(jpg/png/webp/pdf)
출력: 목(계정과목)별 지출결의서 `.docx` + 사람이 확인할 `검토메모.md`

## 구조 — LLM 과 코드의 역할 분리
| 단계 | 담당 | 내용 |
|---|---|---|
| 1 extract | Claude(비전) | 영수증 → 구조화 JSON. 안 보이는 값은 추측하지 않고 `issue` 로 표시 |
| 2 draft | Claude | 영수증을 항목으로 묶고 목을 배정, 문구 작성. **금액은 쓰지 않음** |
| 3 resolve | 코드 | 금액 합산, 유류비 계산, 한글 금액, 참석자 수, 입금처별 합계 |
| 4 validate | 코드 | 중복·누락 영수증, 식대 1인 기준, 택시 사유, 청구기한, 소속별 교통비 인원 등 |
| 5 render | 코드 | 양식 레이아웃 docx 생성, 이미지 영수증 첨부 |

"훈련"은 하지 않습니다. 규정은 `config/accounts.yaml`·`config/rules.yaml`(출처 [26]/[23] 표기), 문체는 `examples/` 의 few-shot 으로 줍니다.
규정이 바뀌면 YAML 만 고치면 됩니다. 좋은 샘플을 추가하려면 `examples/ex_*.json` 형식으로 넣고 `"fewshot": true`.

## 설치·실행
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...            # 모델 변경: JICHUL_MODEL 또는 --model

python -m jichul run --meta examples/meta_template.yaml --receipts-dir ./영수증 --out-dir out
# 단계별:  extract → (receipts.json 을 원본과 대조·수정) → draft → render
python -m jichul extract --receipts-dir ./영수증 --out receipts.json
python -m jichul draft   --meta meta.yaml --receipts receipts.json --out draft.json
python -m jichul render  --meta meta.yaml --receipts receipts.json --draft draft.json --receipts-dir ./영수증

python -m jichul demo examples/ex_260730.json     # API 없이 docx 생성 확인
python -m pytest -q tests                          # 테스트 (API 불필요)
```
오류(ERROR)가 있으면 종료 코드 1. **제출 전 검토메모를 반드시 확인**하세요 — 영수증 판독값은 사람이 원본과 대조해야 합니다.

## 알려진 한계
- LLM 단계(extract/draft)는 이 환경에서 실제 API 로 실행해 보지 못했습니다(요청 구성·파싱·재시도만 mock 테스트).
- 양식이 PDF 뿐이라 docx 레이아웃을 직접 재현했습니다. 원본 hwp/docx 가 있으면 `render.py` 만 교체.
- HEIC 는 미지원(JPG 변환). PDF 영수증은 판독은 되나 docx 에 이미지로 삽입되지 않고 '수동 첨부 필요'로 표시.
- 목 하나당 지출결의서 한 장을 만듭니다(양식의 '목' 칸이 하나, 매뉴얼은 출장비·사업비 구분 청구).
