"""양식(본부_지출결의서_양식 2024 ver.)을 docx 로 재현한다.
원본 양식이 PDF(한글 2020 출력)뿐이라 레이아웃을 직접 재현했다. 원본 hwp/docx 가 있으면 이 모듈만 교체하면 된다."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .compute import parse_date
from .hangul import amount_phrase
from .models import ResolvedExpense

FONT = "굴림"
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}


def _font(run, size=11, bold=False, underline=False, color=None):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.underline = underline
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(a), FONT)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _para(container, text="", size=11, bold=False, align=None, underline=False, space_after=0, color=None):
    p = container.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    if align is not None:
        p.alignment = align
    if text:
        _font(p.add_run(text), size, bold, underline, color)
    return p


def _borders(table, outer=18, inner=4):
    tbl = table._tbl
    pr = tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), str(outer if edge in ("top", "left", "bottom", "right") else inner))
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), "000000")
        b.append(e)
    pr.append(b)


def _set_widths(table, widths_cm):
    table.autofit = False
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    grid = table._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths_cm):
        gc.set(qn("w:w"), str(int(w / 2.54 * 1440)))
    for row in table.rows:
        for cell, w in zip(row.cells, widths_cm):
            cell.width = Cm(w)


def _row_height(row, cm, exact=False):
    trPr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(int(cm / 2.54 * 1440)))
    h.set(qn("w:hRule"), "exact" if exact else "atLeast")
    trPr.append(h)


def _cell_text(cell, text, size=11, bold=False, align=WD_ALIGN_PARAGRAPH.LEFT, color=None):
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    p.alignment = align
    p.paragraph_format.space_after = Pt(0)
    _font(p.add_run(text), size, bold, color=color)
    return p


def _cell_margins(cell, left=0.25, right=0.2):
    tcPr = cell._tc.get_or_add_tcPr()
    m = OxmlElement("w:tcMar")
    for side, v in (("left", left), ("right", right)):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:w"), str(int(v / 2.54 * 1440)))
        e.set(qn("w:type"), "dxa")
        m.append(e)
    tcPr.append(m)


def build_docx(exp: ResolvedExpense, out_path: Path, embed_dir: Path | None = None) -> Path:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(1.8)
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    # 제목
    t = _para(doc, "", align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    _font(t.add_run("지 출 결 의 서"), 22, True, underline=True)

    # 결재란 (우측)
    sign = doc.add_table(rows=2, cols=4)
    sign.alignment = WD_TABLE_ALIGNMENT.RIGHT
    _borders(sign, outer=6, inner=6)
    _set_widths(sign, [0.9, 2.3, 2.3, 2.3])
    a = sign.cell(0, 0).merge(sign.cell(1, 0))
    a.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    a.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(a.paragraphs[0].add_run("결"), 10)
    pp = a.add_paragraph()
    pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(pp.add_run("재"), 10)
    for i, h in enumerate(("담당", "사무국장", "본부장"), start=1):
        _cell_text(sign.cell(0, i), h, 9, align=WD_ALIGN_PARAGRAPH.CENTER)
    _row_height(sign.rows[0], 0.6)
    _row_height(sign.rows[1], 1.9)

    _para(doc, "", space_after=8)

    # 지급 일자 (총무 기입란)
    yr = parse_date(exp.draft_date).year
    p = _para(doc, "", space_after=4)
    _font(p.add_run(f"지급 일자 : {yr}년      월      일 (          )"), 11, True, underline=True)

    # 본문 표
    W = [2.2, 7.6, 2.0, 4.8]
    tb = doc.add_table(rows=5, cols=4)
    _borders(tb, outer=18, inner=6)
    _set_widths(tb, W)
    d = parse_date(exp.draft_date)

    _cell_text(tb.cell(0, 0), "기안\n날짜".replace("\n", " "), 10.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(tb.cell(0, 1), f"{d.year}년 {d.month}월 {d.day}일", 11)
    _cell_text(tb.cell(0, 2), "목", 10.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    _cell_text(tb.cell(0, 3), exp.account_item, 11, bold=True)

    for r_i, label, text, color in (
        (1, "기 안 자", exp.drafter, None),
        (2, "참 석 자", exp.attendees_line, (0xC0, 0, 0) if exp.attendee_count is None else None),
    ):
        _cell_text(tb.cell(r_i, 0), label, 10.5, align=WD_ALIGN_PARAGRAPH.CENTER)
        m = tb.cell(r_i, 1).merge(tb.cell(r_i, 3))
        _cell_text(m, text, 11, color=color)

    _cell_text(tb.cell(3, 0), "금 액", 10.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    m = tb.cell(3, 1).merge(tb.cell(3, 3))
    pr = _cell_text(m, "", 11)
    _font(pr.add_run(amount_phrase(exp.total)), 11, True)
    pr.paragraph_format.tab_stops.add_tab_stop(Cm(14.0), WD_TAB_ALIGNMENT.RIGHT)
    _font(pr.add_run(f"\t(₩ {exp.total:,})"), 11, True)

    _cell_text(tb.cell(4, 0), "적 요", 10.5, align=WD_ALIGN_PARAGRAPH.CENTER)
    body = tb.cell(4, 1).merge(tb.cell(4, 3))
    body.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    first = body.paragraphs[0]
    first.paragraph_format.space_before = Pt(8)
    first.paragraph_format.space_after = Pt(0)
    _font(first.add_run(f"<일시> : {exp.event_line}"), 11)

    def line(text, indent=0.0, size=11):
        p = body.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(indent)
        _font(p.add_run(text), size)
        return p

    line(f"<사업/회의/출장명> : {exp.title}")
    line("<내역> :")
    for it in exp.items:
        txt = f"- {it.label} : {it.detail} {it.amount:,}원".replace("  ", " ")
        if it.reason:
            txt += f" (사유: {it.reason})"
        line(txt, 0.4)
        if it.calc_note:
            line(f"· {it.calc_note}", 0.9, 9.5)
    line("<입금처> :")
    for pl in exp.payee_lines:
        line(pl, 0.4)
    if exp.attachments:
        line("<첨부> : " + ", ".join(exp.attachments))
    line("")

    for i, h in enumerate((1.1, 1.1, 1.1, 1.1, 6.0)):
        _row_height(tb.rows[i], h)
    for row in tb.rows:
        for c in row.cells:
            _cell_margins(c)

    _para(doc, "일반회계", 11, align=WD_ALIGN_PARAGRAPH.RIGHT)

    # 증빙 첨부 (이미지만 삽입. PDF·HEIC 등은 목록으로 안내)
    if exp.attachment_files:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        _para(doc, "첨부 : 영수증 및 증빙", 12, True, space_after=6)
        for f in exp.attachment_files:
            path = (embed_dir / f) if embed_dir else None
            embedded = False
            if path and path.exists() and path.suffix.lower() in IMG_EXT:
                try:                                   # 손상된 이미지 한 장 때문에 전체 문서 생성이 실패하지 않도록
                    _para(doc, f, 9, space_after=2)
                    doc.add_picture(str(path), width=Cm(12.0))
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT
                    _para(doc, "", space_after=6)
                    embedded = True
                except Exception:  # noqa: BLE001
                    pass
            if not embedded:
                _para(doc, f"[수동 첨부 필요] {f}", 10, color=(0xC0, 0, 0), space_after=4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path
