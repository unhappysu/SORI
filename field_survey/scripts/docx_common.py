"""field_survey 문서(docx) 공통 서식 도구.

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

- A4 세로, 여백 22 mm, 맑은 고딕, 본문 10.5 pt, 줄 간격 1.4
- 제목 스타일(Heading 1~3)과 자동 목차(TOC 필드) — 목차 숫자는 Word에서 필드 갱신 시 채워짐
- 바닥글: 생성형 AI 활용 표기 + 쪽 번호
- 글 안의 간단한 표기를 실제 서식으로 바꿈:
    L_{Aeq}  → L 뒤 "Aeq" 아래첨자
    10^{0.1}  → "0.1" 위첨자
    **굵게**  → 굵게
"""
import re
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

FONT = "맑은 고딕"
BODY_PT = 10.5
MARGIN_MM = 22
BODY_W_MM = 210 - 2 * MARGIN_MM          # 166 mm
AI_NOTE = "본 문서는 생성형 AI(Claude)를 활용하여 작성되었음"

HEAD_FILL = "D9E2F3"     # 표 머리글
INPUT_FILL = "FFF8DC"    # 사용자 기입 칸
NOTE_FILL = "F2F2F2"

_TOKEN = re.compile(r"(_\{[^}]*\}|\^\{[^}]*\}|\*\*[^*]+\*\*)")


def _set_font(run_or_style_font, element, size=None, bold=None, color=None):
    f = run_or_style_font
    f.name = FONT
    if size is not None:
        f.size = Pt(size)
    if bold is not None:
        f.bold = bold
    if color is not None:
        f.color.rgb = RGBColor.from_string(color)
    rpr = element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(a), FONT)
    for a in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(a)) is not None:
            del rfonts.attrib[qn(a)]


def add_rich(paragraph, text, size=None, bold=None, color=None):
    """text 안의 _{..}, ^{..}, **..** 표기를 서식으로 바꿔 넣는다."""
    for part in _TOKEN.split(text):
        if not part:
            continue
        sub = sup = b = False
        if part.startswith("_{"):
            part, sub = part[2:-1], True
        elif part.startswith("^{"):
            part, sup = part[2:-1], True
        elif part.startswith("**"):
            part, b = part[2:-2], True
        r = paragraph.add_run(part)
        _set_font(r.font, r._element, size=size, bold=(True if b else bold), color=color)
        if sub:
            r.font.subscript = True
        if sup:
            r.font.superscript = True
    return paragraph


def new_document(title_for_props):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.orientation = WD_ORIENT.PORTRAIT
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Mm(MARGIN_MM))
    sec.header_distance = Mm(10)
    sec.footer_distance = Mm(10)
    doc.core_properties.title = title_for_props
    doc.core_properties.author = "전농사거리 소음현황조사(생성형 AI Claude 활용)"

    st = doc.styles
    normal = st["Normal"]
    _set_font(normal.font, normal.element, size=BODY_PT)
    pf = normal.paragraph_format
    pf.line_spacing = 1.4
    pf.space_after = Pt(4)
    pf.space_before = Pt(0)

    for name, size, before, after in (("Heading 1", 15, 16, 8), ("Heading 2", 12.5, 12, 6),
                                       ("Heading 3", 11, 8, 4), ("Title", 24, 0, 12)):
        s = st[name]
        _set_font(s.font, s.element, size=size, bold=True, color="1F3864")
        s.font.italic = False
        s.paragraph_format.space_before = Pt(before)
        s.paragraph_format.space_after = Pt(after)
        s.paragraph_format.line_spacing = 1.25
        s.paragraph_format.keep_with_next = True
    # 목차 스타일
    for lvl in (1, 2, 3):
        try:
            s = st[f"TOC {lvl}"]
        except KeyError:
            s = st.add_style(f"TOC {lvl}", 1)
            s.base_style = normal
        _set_font(s.font, s.element, size=BODY_PT)
        s.paragraph_format.left_indent = Mm(6 * (lvl - 1))
        s.paragraph_format.space_after = Pt(2)
        s.paragraph_format.line_spacing = 1.3
    for name in ("List Bullet", "List Number", "Caption"):
        s = st[name]
        _set_font(s.font, s.element, size=BODY_PT if name != "Caption" else 9.5)
        s.paragraph_format.line_spacing = 1.4 if name != "Caption" else 1.2
    cap = st["Caption"]
    cap.font.color.rgb = RGBColor.from_string("1F3864")
    cap.font.italic = False
    cap.font.bold = True
    cap.paragraph_format.space_before = Pt(6)
    cap.paragraph_format.space_after = Pt(3)
    cap.paragraph_format.keep_with_next = True

    _footer(sec)
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    # 목차 필드는 export_pdf()에서 Word로 갱신한다(updateFields 설정은 Word가 열 때 확인 창을 띄워 자동 변환을 막음)
    return doc


def _field(paragraph, instr, placeholder="", size=None):
    r1 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "begin"); r1._element.append(fc)
    r2 = paragraph.add_run()
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = instr
    r2._element.append(it)
    r3 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "separate"); r3._element.append(fc)
    r4 = paragraph.add_run(placeholder)
    _set_font(r4.font, r4._element, size=size)
    r5 = paragraph.add_run()
    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "end"); r5._element.append(fc)
    for r in (r1, r2, r3, r5):
        _set_font(r.font, r._element, size=size)


def _footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER        # 탭 정지점은 Pages에서 위치가 달라져 가운데 정렬 사용
    p.paragraph_format.line_spacing = 1.0
    add_rich(p, AI_NOTE, size=8, color="595959")
    r = p.add_run("  |  ")
    _set_font(r.font, r._element, size=8, color="595959")
    _field(p, "PAGE", "1", size=8)
    r = p.add_run(" / ")
    _set_font(r.font, r._element, size=8, color="595959")
    _field(p, "NUMPAGES", "1", size=8)
    # 위쪽 가는 선(문단 테두리)
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "4"), ("w:color", "A6A6A6")):
        top.set(qn(k), v)
    bdr.append(top)
    _insert_ordered(ppr, bdr, ["w:shd", "w:tabs", "w:suppressAutoHyphens", "w:spacing", "w:ind", "w:jc", "w:rPr"])


def add_toc(doc, title="목차"):
    p = doc.add_paragraph()
    add_rich(p, title, size=15, bold=True, color="1F3864")
    p.paragraph_format.space_after = Pt(10)
    p = doc.add_paragraph()
    _field(p, 'TOC \\o "1-3" \\h \\z \\u', "(목차는 Word에서 필드 갱신 시 표시됩니다)")
    page_break(doc)


def page_break(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_break(WD_BREAK.PAGE)


def h(doc, text, level):
    p = doc.add_heading("", level=level)
    add_rich(p, text)
    return p


def para(doc, text, style=None, size=None, bold=None, align=None, space_after=None, indent_mm=None):
    p = doc.add_paragraph(style=style)
    add_rich(p, text, size=size, bold=bold)
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "right":
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    if indent_mm is not None:
        p.paragraph_format.left_indent = Mm(indent_mm)
    return p


# ── Word 수식(OMML) ──────────────────────────────
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def m_r(text, italic=True):
    sty = "" if italic else "<m:rPr><m:sty m:val=\"p\"/></m:rPr>"
    return f"<m:r>{sty}<m:t xml:space=\"preserve\">{text}</m:t></m:r>"


def m_sub(base, sub):
    return f"<m:sSub><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>"


def m_sup(base, sup):
    return f"<m:sSup><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>"


def m_frac(num, den):
    return f"<m:f><m:num>{num}</m:num><m:den>{den}</m:den></m:f>"


def m_sum(e, sub="", sup=""):
    hide = ""
    if not sub:
        hide += "<m:subHide m:val=\"1\"/>"
    if not sup:
        hide += "<m:supHide m:val=\"1\"/>"
    return (f"<m:nary><m:naryPr><m:chr m:val=\"∑\"/>{hide}</m:naryPr>"
            f"<m:sub>{sub}</m:sub><m:sup>{sup}</m:sup><m:e>{e}</m:e></m:nary>")


def m_paren(e, beg="(", end=")"):
    return f"<m:d><m:dPr><m:begChr m:val=\"{beg}\"/><m:endChr m:val=\"{end}\"/></m:dPr><m:e>{e}</m:e></m:d>"


def math_para(doc, inner):
    """inner: OMML 조각 문자열. 가운데 정렬된 수식 문단을 넣는다."""
    from docx.oxml import parse_xml
    p = doc.add_paragraph()
    xml = f"<m:oMathPara xmlns:m=\"{_M}\"><m:oMath>{inner}</m:oMath></m:oMathPara>"
    p._p.append(parse_xml(xml))
    return p


def bullets(doc, items, style="List Bullet"):
    for it in items:
        para(doc, it, style=style)


def caption(doc, text):
    return para(doc, text, style="Caption")


def _insert_ordered(parent, child, successors):
    """스키마 순서를 지켜 child 를 successors(뒤에 와야 할 요소 이름들) 중 첫 요소 앞에 넣는다."""
    for tag in successors:
        found = parent.find(qn(tag))
        if found is not None:
            found.addprevious(child)
            return child
    parent.append(child)
    return child


def _shade(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    for old in tcpr.findall(qn("w:shd")):
        tcpr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
    _insert_ordered(tcpr, shd, ["w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"])


def _row_flags(row, header=False):
    trpr = row._tr.get_or_add_trPr()
    cs = OxmlElement("w:cantSplit"); trpr.append(cs)
    if header:
        th = OxmlElement("w:tblHeader"); trpr.append(th)


def table(doc, rows, widths_mm, header_rows=1, size=9, fills=None, align=None, min_height_mm=None,
          bold_first_col=False):
    """rows: 2차원 리스트(문자열, '\\n'은 칸 안 문단 나눔). widths_mm 합은 본문 폭 이하.
    fills: {(r, c): 'RRGGBB'} 칸 색. align: 열별 'l'/'c'/'r' 문자열."""
    assert sum(widths_mm) <= BODY_W_MM + 0.5, f"표 폭 {sum(widths_mm)} mm > 본문 {BODY_W_MM} mm"
    ncol = len(widths_mm)
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = doc.styles["Table Grid"]
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    # 표 전체 폭과 고정 레이아웃
    tblpr = t._tbl.tblPr
    tw = OxmlElement("w:tblW"); tw.set(qn("w:w"), str(int(sum(widths_mm) * 56.7))); tw.set(qn("w:type"), "dxa")
    for old in tblpr.findall(qn("w:tblW")):
        tblpr.remove(old)
    _insert_ordered(tblpr, tw, ["w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd",
                                "w:tblLayout", "w:tblCellMar", "w:tblLook"])
    # 고정 레이아웃(w:tblLayout)은 t.autofit = False 가 이미 넣음
    grid = t._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths_mm):
        gc.set(qn("w:w"), str(int(w * 56.7)))
    fills = fills or {}
    for ri, row in enumerate(rows):
        tr = t.rows[ri]
        _row_flags(tr, header=ri < header_rows)
        if min_height_mm and ri >= header_rows:
            tr.height = Mm(min_height_mm)
        for ci in range(ncol):
            cell = tr.cells[ci]
            cell.width = Mm(widths_mm[ci])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            val = row[ci] if ci < len(row) else ""
            lines = str(val).split("\n")
            cell.paragraphs[0].text = ""
            for li, line in enumerate(lines):
                p = cell.paragraphs[0] if li == 0 else cell.add_paragraph()
                p.paragraph_format.line_spacing = 1.2
                p.paragraph_format.space_after = Pt(0)
                is_head = ri < header_rows
                add_rich(p, line, size=size, bold=True if is_head or (bold_first_col and ci == 0) else None)
                a = "c" if is_head else (align[ci] if align else "l")
                p.alignment = {"l": WD_ALIGN_PARAGRAPH.LEFT, "c": WD_ALIGN_PARAGRAPH.CENTER,
                               "r": WD_ALIGN_PARAGRAPH.RIGHT}[a]
            if ri < header_rows:
                _shade(cell, HEAD_FILL)
            if (ri, ci) in fills:
                _shade(cell, fills[(ri, ci)])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def keep_together(t):
    """표가 쪽에서 나뉘지 않게: 마지막 행을 뺀 모든 칸 문단에 '다음 단락과 함께'를 건다(행 나눔 금지는 table()에서 이미 설정)."""
    for row in t.rows[:-1]:
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.keep_with_next = True
    return t


def note_box(doc, text, fill=NOTE_FILL, size=9.5):
    """한 칸짜리 표로 만든 안내 상자(본문 폭)."""
    t = table(doc, [[text]], [BODY_W_MM], header_rows=0, size=size, fills={(0, 0): fill})
    return t


def cover(doc, title, subtitle, meta_rows):
    for _ in range(5):
        para(doc, "", space_after=6)
    p = doc.add_paragraph(style="Title")
    add_rich(p, title)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, subtitle, size=13, align="center", space_after=30)
    table(doc, meta_rows, [45, 100], header_rows=0, size=10.5, bold_first_col=True,
          fills={(i, 0): HEAD_FILL for i in range(len(meta_rows))})
    for _ in range(3):
        para(doc, "", space_after=6)
    note_box(doc, f"**{AI_NOTE}.** 기준값·조항은 국가법령정보센터 원문으로 확인한 것만 적었고, "
                  "원문에서 확인하지 못한 값은 '미확인'으로 표시하였다. 측정 결과는 포함하지 않는다.", size=10)
    page_break(doc)


PAGES_SCRIPT = r'''
on run argv
  set inF to POSIX file (item 1 of argv)
  set outF to POSIX file (item 2 of argv)
  with timeout of 240 seconds
    tell application "Pages"
      set d to open inF
      delay 2
      export d to outF as PDF
      close d saving no
    end tell
  end timeout
end run
'''


def set_font(name):
    """문서 전체 글꼴을 바꾼다(new_document 전에 호출)."""
    global FONT
    FONT = name


def export_pdf(docx_path, pdf_path, script_dir):
    """Pages(macOS)로 docx를 열어 PDF로 내보낸다. Pages가 목차 쪽번호를 채운다.
    Pages에 없는 글꼴은 다른 글꼴로 바뀌므로, PDF용 docx는 시스템 글꼴로 따로 만든다."""
    script = Path(script_dir) / "pages2pdf.applescript"
    script.write_text(PAGES_SCRIPT, encoding="utf-8")
    subprocess.run(["osascript", str(script), str(docx_path), str(pdf_path)], check=True, timeout=300)
