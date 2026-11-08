# -*- coding: utf-8 -*-
"""把 PRD / 详细设计 markdown 合并导出为 docx"""
import re, os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, '..')
DOCS = [
    os.path.join(_ROOT, '01_需求文档_PRD.md'),
    os.path.join(_ROOT, '02_详细设计.md'),
]
OUT = os.path.join(_ROOT, '波段策略回测工作台_需求与详细设计_v1.0.docx')

FONT = '微软雅黑'
MONO = 'Consolas'
ACCENT = RGBColor(0x1F, 0x4E, 0x79)


def set_cn(run, font=FONT):
    run.font.name = font
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font)


def shade(cell_or_par, hexcolor):
    el = cell_or_par._tc if hasattr(cell_or_par, '_tc') else cell_or_par._p
    pr = el.get_or_add_tcPr() if hasattr(cell_or_par, '_tc') else el.get_or_add_pPr()
    sh = OxmlElement('w:shd')
    sh.set(qn('w:val'), 'clear'); sh.set(qn('w:color'), 'auto'); sh.set(qn('w:fill'), hexcolor)
    pr.append(sh)


BOLD = re.compile(r'(\*\*[^*]+\*\*|`[^`]+`)')


def add_rich(par, text, size=10.5, color=None, base_bold=False):
    for seg in BOLD.split(text):
        if not seg:
            continue
        if seg.startswith('**') and seg.endswith('**'):
            r = par.add_run(seg[2:-2]); r.bold = True; r.font.size = Pt(size); set_cn(r)
        elif seg.startswith('`') and seg.endswith('`'):
            r = par.add_run(seg[1:-1]); r.font.name = MONO; r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
            r._element.rPr.rFonts.set(qn('w:eastAsia'), MONO)
        else:
            r = par.add_run(seg); r.font.size = Pt(size); r.bold = base_bold; set_cn(r)
        if color is not None:
            r.font.color.rgb = color


def parse_table(lines, i):
    rows = []
    while i < len(lines) and lines[i].strip().startswith('|'):
        rows.append(lines[i].strip()); i += 1
    cells = [[c.strip() for c in r.strip('|').split('|')] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r':?-{2,}:?', c or '-') for c in r)]
    return cells, i


def add_table(doc, cells):
    if not cells:
        return
    ncol = max(len(r) for r in cells)
    t = doc.add_table(rows=len(cells), cols=ncol)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ri, row in enumerate(cells):
        for ci in range(ncol):
            cell = t.cell(ri, ci)
            txt = row[ci] if ci < len(row) else ''
            cell.text = ''
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            if ri == 0:
                shade(cell, '1F4E79')
                add_rich(p, txt, 9, RGBColor(0xFF, 0xFF, 0xFF), True)
            else:
                add_rich(p, txt, 9)
                if ri % 2 == 0:
                    shade(cell, 'F2F5F9')
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def convert(path, doc, first=False):
    lines = open(path, encoding='utf-8').read().split('\n')
    i = 0
    if not first:
        doc.add_page_break()
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if s.startswith('```'):
            i += 1; buf = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                buf.append(lines[i]); i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(4)
            shade(p, 'F6F8FA')
            r = p.add_run('\n'.join(buf))
            r.font.name = MONO; r.font.size = Pt(8.5)
            r._element.rPr.rFonts.set(qn('w:eastAsia'), MONO)
            continue

        if s.startswith('|'):
            cells, i = parse_table(lines, i)
            add_table(doc, cells)
            continue

        if re.fullmatch(r'-{3,}', s):
            p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(2)
            pPr = p._p.get_or_add_pPr()
            pbdr = OxmlElement('w:pBdr'); bottom = OxmlElement('w:bottom')
            bottom.set(qn('w:val'), 'single'); bottom.set(qn('w:sz'), '6')
            bottom.set(qn('w:color'), 'CCCCCC'); pbdr.append(bottom); pPr.append(pbdr)
            i += 1; continue

        m = re.match(r'^(#{1,4})\s+(.*)$', s)
        if m:
            level = len(m.group(1)); txt = m.group(2).strip()
            if level == 1:
                if not first:
                    doc.add_page_break()
                p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(10)
                r = p.add_run(txt); r.bold = True; r.font.size = Pt(20)
                r.font.color.rgb = ACCENT; set_cn(r)
            elif level == 2:
                p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(14)
                p.paragraph_format.space_after = Pt(6)
                r = p.add_run(txt); r.bold = True; r.font.size = Pt(14)
                r.font.color.rgb = ACCENT; set_cn(r)
            elif level == 3:
                p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(10)
                p.paragraph_format.space_after = Pt(4)
                r = p.add_run(txt); r.bold = True; r.font.size = Pt(12); set_cn(r)
            else:
                p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(8)
                p.paragraph_format.space_after = Pt(3)
                r = p.add_run(txt); r.bold = True; r.font.size = Pt(11); set_cn(r)
            i += 1; continue

        if not s:
            i += 1; continue

        m = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)$', ln)
        if m:
            indent = len(m.group(1)) // 2
            p = doc.add_paragraph(style='List Bullet' if m.group(2) in '-*' else 'List Number')
            p.paragraph_format.left_indent = Cm(0.6 + 0.6 * indent)
            p.paragraph_format.space_after = Pt(2)
            add_rich(p, m.group(3), 10.5)
            i += 1; continue

        if s.startswith('>'):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            p.paragraph_format.space_before = Pt(4); p.paragraph_format.space_after = Pt(4)
            shade(p, 'EAF1F8')
            add_rich(p, s.lstrip('> ').strip(), 10)
            i += 1; continue

        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(5)
        p.paragraph_format.line_spacing = 1.35
        add_rich(p, s, 10.5)
        i += 1


doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
sec.left_margin = sec.right_margin = Cm(2.2)
sec.top_margin = sec.bottom_margin = Cm(2.0)

st = doc.styles['Normal']
st.font.name = FONT; st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn('w:eastAsia'), FONT)

# 封面
for _ in range(6):
    doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('个股波段策略回测工作台'); r.bold = True; r.font.size = Pt(30); r.font.color.rgb = ACCENT; set_cn(r)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('需求文档 + 详细设计'); r.font.size = Pt(18); r.font.color.rgb = RGBColor(0x60, 0x60, 0x60); set_cn(r)
for _ in range(4):
    doc.add_paragraph()
for line in ['版本：v1.0', '日期：2026-11-01', '形态：本地单机 Web 应用', '状态：待评审']:
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(line); r.font.size = Pt(12); set_cn(r)
doc.add_page_break()

convert(DOCS[0], doc, first=True)
convert(DOCS[1], doc)

doc.save(OUT)
print('docx ->', OUT, os.path.getsize(OUT), 'bytes')
