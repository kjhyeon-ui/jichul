"""지출결의서 자동 작성 — 웹 화면.  실행: streamlit run app.py
사용자가 하는 일: 사업명 입력 + 증빙 파일 전부 업로드 + 버튼."""
from __future__ import annotations

import hmac
import os
import shutil
import tempfile
from pathlib import Path
from typing import get_args

import pandas as pd
import streamlit as st

from jichul import llm, pipeline
from jichul.config import ROOT, load_accounts, load_rules
from jichul.hangul import amount_phrase
from jichul.models import Receipt, ReceiptKind

st.set_page_config(page_title="지출결의서 자동 작성", page_icon="🧾", layout="centered")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RULES, ACC = load_rules(), load_accounts()


@st.cache_resource
def examples():
    return pipeline.load_examples(ROOT / "examples")


@st.cache_resource
def client():
    return llm.make_client()


def gate():
    pw = os.environ.get("JICHUL_PASSWORD")
    if not pw or st.session_state.get("authed"):
        return
    st.title("지출결의서 자동 작성")
    entered = st.text_input("비밀번호", type="password")
    if entered and hmac.compare_digest(entered, pw):
        st.session_state["authed"] = True
        st.rerun()
    elif entered:
        st.error("비밀번호가 맞지 않습니다.")
    st.stop()


def workdir() -> Path:
    if "workdir" not in st.session_state:
        st.session_state["workdir"] = tempfile.mkdtemp(prefix="jichul_")
    return Path(st.session_state["workdir"])


def placeholders(res: pipeline.Result) -> list[str]:
    out = []
    if res.meta.get("attendees") is None:
        out.append("참석자 명단")
    if not res.meta.get("drafter"):
        out.append("기안자")
    if any("[" in l for e in res.expenses for l in e.payee_lines):
        out.append("입금처(계좌)")
    return out


def attendees_to_text(att: dict | None) -> str:
    return "\n".join((f"{o}: {', '.join(n)}" if o else ", ".join(n)) for o, n in (att or {}).items())


def progress_cb(status):
    def cb(stage, msg):
        status.update(label=msg)
    return cb


def show_result(res: pipeline.Result):
    st.divider()
    st.subheader("결과")
    for n in res.notes:
        st.info(n)
    miss = placeholders(res)
    if miss:
        st.warning("문서에 [확인 필요]로 남은 칸: " + ", ".join(miss) + " — 아래 '읽은 내용 수정'에서 채우고 다시 만들 수 있습니다.")

    errs = [m[6:] for m in res.msgs if m.startswith("ERROR ")]
    warns = [m[5:] for m in res.msgs if m.startswith("WARN ")]
    infos = [m[5:] for m in res.msgs if m.startswith("INFO ")]
    for m in errs:
        st.error(m)
    for m in warns:
        st.warning(m)
    if infos:
        with st.expander("증빙 체크리스트"):
            for m in infos:
                st.write("• " + m)
    if res.draft.questions:
        with st.expander("모델이 확인을 요청한 것", expanded=True):
            for q in res.draft.questions:
                st.write("• " + q)

    for e, f in zip(res.expenses, res.files):
        with st.container(border=True):
            st.markdown(f"**{e.account_item}**  ·  {e.total:,}원")
            st.caption(f"{amount_phrase(e.total)}  ·  {e.title}")
            st.download_button(f"{e.account_item} 지출결의서 다운로드 (.docx)", f.read_bytes(), f.name, DOCX, key=f"dl_{f.name}")
    st.download_button("검토 메모 다운로드 (제출용 아님)", res.review.read_bytes(), res.review.name, "text/markdown", key="dl_review")
    st.caption("제출 전에 금액·날짜·참석자를 원본 증빙과 반드시 대조하세요. 검증은 규정·산수 오류만 잡을 수 있고, 판독 오류는 사람이 확인해야 합니다.")

    with st.expander("읽은 내용 수정 후 다시 만들기"):
        df = pd.DataFrame([{"id": r.id, "파일": r.source_file, "날짜": r.date or "", "종류": r.kind, "금액": r.amount,
                            "신뢰도": r.confidence, "비고": r.issue or ""} for r in res.receipts])
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True, key="receipt_editor",
            disabled=["id", "파일", "신뢰도"],
            column_config={"종류": st.column_config.SelectboxColumn(options=list(get_args(ReceiptKind))),
                           "금액": st.column_config.NumberColumn(min_value=0, step=100, format="%d")})
        c1, c2 = st.columns(2)
        ed = c1.text_input("사업일 (YYYY-MM-DD)", value=res.meta.get("event_date", ""))
        dr = c2.text_input("기안자", value=res.meta.get("drafter", ""))
        at = st.text_area("참석자 (한 줄에 한 소속: '본부: 홍길동, 김철수')", value=attendees_to_text(res.meta.get("attendees")))
        py = st.text_area("입금처 (한 줄에 한 명: '이름 / 은행 / 계좌번호')", value=st.session_state.get("inputs", {}).get("payees_text", ""))
        if st.button("수정 반영해서 다시 만들기"):
            by_id = {r.id: r for r in res.receipts}
            new = []
            for row in edited.to_dict("records"):
                o = by_id[row["id"]]
                upd = {"date": row["날짜"] or None, "kind": row["종류"], "amount": int(row["금액"]), "issue": row["비고"] or None}
                if upd["amount"] != o.amount or upd["date"] != o.date or upd["kind"] != o.kind:
                    upd["confidence"] = "high"          # 사람이 직접 고친 값
                    upd["issue"] = (upd["issue"] + " " if upd["issue"] else "") + "(사람이 수정)"
                new.append(o.model_copy(update=upd))
            kw = dict(st.session_state.get("inputs", {}), event_date=ed or None, drafter=dr, attendees_text=at, payees_text=py)
            st.session_state["inputs"] = kw
            run(lambda status: pipeline.regenerate(
                client(), st.session_state["title"], new, res.docs, Path(st.session_state["evidence_dir"]),
                workdir() / "out", RULES, ACC, examples(), llm.DEFAULT_MODEL, on_progress=progress_cb(status), **kw))


def run(job):
    try:
        with st.status("작업 중…", expanded=True) as status:
            res = job(status)
            status.update(label="완료", state="complete", expanded=False)
        st.session_state["res"] = res
    except Exception as e:  # noqa: BLE001  — 사용자에게 원인을 그대로 보여준다
        st.error(f"실패했습니다: {e}")
        st.session_state.pop("res", None)


gate()
st.title("🧾 지출결의서 자동 작성")
st.caption("사업명을 쓰고 증빙을 올리면, 목(계정과목)별 지출결의서(.docx)를 만들어 드립니다.")

if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error("서버에 ANTHROPIC_API_KEY 가 설정되어 있지 않습니다. 관리자에게 문의하세요.")
    st.stop()

title = st.text_input("사업명", placeholder="예: 10/15 희망간병 대표자회의")
files = st.file_uploader("증빙 업로드 — 여기에 파일을 전부 업로드하세요", accept_multiple_files=True,
                         type=["jpg", "jpeg", "png", "webp", "gif", "pdf"])
st.caption("영수증·승차권·카드내역은 물론, 있으면 참석자 서명부·회의결과·지도/유가 캡처도 함께 올리세요. (jpg·png·pdf, 아이폰 HEIC 는 JPG 로 변환)")

with st.expander("추가 정보 (선택) — 비워 두면 서류에서 읽거나 [확인 필요]로 표시합니다"):
    drafter = st.text_input("기안자", value=st.session_state.get("drafter", ""))
    event_date = st.date_input("사업일", value=None)
    attendees_text = st.text_area("참석자", placeholder="본부: 홍길동, 김철수\n충북: 이영희")
    payees_text = st.text_area("입금처", placeholder="홍길동 / 국민 / 000-00-0000000\n(계좌번호는 AI 서버로 전송되지 않고 문서에만 기입됩니다)")
    et = st.selectbox("회의 유형", ["(해당 없음)"] + list(ACC["event_types"]))

st.caption("업로드한 증빙은 판독을 위해 Claude API 로 전송됩니다. 계좌번호는 전송되지 않으며, 작업 파일은 서버 임시 폴더에만 저장됩니다.")

if st.button("지출결의서 만들기", type="primary", disabled=not (title.strip() and files)):
    st.session_state["drafter"] = drafter
    ev_dir = workdir() / "evidence"
    if ev_dir.exists():
        shutil.rmtree(ev_dir)
    for f in files:
        pipeline.save_upload(f.name, f.getvalue(), ev_dir)
    st.session_state.update(title=title, evidence_dir=str(ev_dir))
    kw = dict(event_date=event_date.isoformat() if event_date else None, drafter=drafter, payees_text=payees_text,
              attendees_text=attendees_text, event_type=None if et == "(해당 없음)" else et)
    st.session_state["inputs"] = kw
    run(lambda status: pipeline.process(client(), title, ev_dir, workdir() / "out", RULES, ACC, examples(), llm.DEFAULT_MODEL,
                                        on_progress=progress_cb(status), **kw))

if st.session_state.get("res"):
    show_result(st.session_state["res"])
