"""Render the reviewed Traditional Chinese research report as a readable PDF.

The Markdown report is the canonical source. No results are calculated here.
Generation requires --data-final so incomplete study output is not published.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
PAGE_W, PAGE_H = A4
MARGIN_X = 44
CONTENT_W = PAGE_W - 2 * MARGIN_X
INK = colors.HexColor('#233345')
MUTED = colors.HexColor('#657581')
TEAL = colors.HexColor('#006B74')
PALE = colors.HexColor('#EFF5F5')
GRID = colors.HexColor('#D7E1E4')
RUST = colors.HexColor('#925137')
DEFAULT_FONT = Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
DEFAULT_BOLD_FONT = Path('/System/Library/Fonts/STHeiti Medium.ttc')


def register_fonts(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f'CJK font unavailable: {path}')
    pdfmetrics.registerFont(TTFont('ReportCJK', str(path)))
    bold_path = DEFAULT_BOLD_FONT if DEFAULT_BOLD_FONT.is_file() else path
    pdfmetrics.registerFont(TTFont('ReportCJKBold', str(bold_path), subfontIndex=0))
    pdfmetrics.registerFontFamily(
        'ReportCJK', normal='ReportCJK', bold='ReportCJKBold',
        italic='ReportCJK', boldItalic='ReportCJKBold',
    )


def make_styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName='ReportCJK', textColor=INK, wordWrap='CJK',
                splitLongWords=True, allowWidows=0, allowOrphans=0)
    return {
        'title': ParagraphStyle('Title', fontSize=23, leading=32,
                                spaceAfter=12, **{**base, 'fontName': 'ReportCJKBold'}),
        'kicker': ParagraphStyle('Kicker', fontSize=8, leading=12,
                                 textColor=TEAL, fontName='ReportCJK', spaceAfter=12),
        'h2': ParagraphStyle('H2', fontSize=14, leading=20,
                             spaceBefore=13, spaceAfter=7, keepWithNext=True,
                             **{**base, 'fontName': 'ReportCJKBold'}),
        'h3': ParagraphStyle('H3', fontSize=11, leading=16,
                             spaceBefore=10, spaceAfter=5, keepWithNext=True,
                             **{**base, 'fontName': 'ReportCJKBold'}),
        'body': ParagraphStyle('Body', fontSize=9.6, leading=14.8,
                               spaceAfter=7, **base),
        'lead': ParagraphStyle('Lead', fontSize=10.1, leading=16,
                               spaceAfter=0, **base),
        'small': ParagraphStyle('Small', fontSize=8, leading=12,
                                spaceAfter=6, **{**base, 'textColor': MUTED}),
        'table': ParagraphStyle('Table', fontSize=8.6, leading=12.3,
                                spaceAfter=0, **base),
        'thead': ParagraphStyle('TableHead', fontSize=8.6, leading=12.3,
                                spaceAfter=0,
                                **{**base, 'textColor': colors.white, 'fontName': 'ReportCJKBold'}),
        'bullet': ParagraphStyle('Bullet', fontSize=9.4, leading=14.5,
                                 leftIndent=12, firstLineIndent=-10, spaceAfter=4, **base),
        'caption': ParagraphStyle('Caption', fontSize=8.2, leading=12,
                                  spaceBefore=3, spaceAfter=9, **{**base, 'textColor': MUTED}),
        'code': ParagraphStyle('Code', fontSize=8, leading=11.5,
                               leftIndent=9, rightIndent=9, backColor=PALE,
                               borderPadding=7, spaceAfter=8, **base),
    }


def inline(text: str, source_dir: Path) -> str:
    """Translate the small Markdown subset used by the canonical report."""
    text = text.replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')
    tokens: list[str] = []

    def keep(value: str) -> str:
        tokens.append(value)
        return f'ZZTOKEN{len(tokens) - 1}ZZ'

    def link(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if not re.match(r'^[a-z]+://', target):
            target = (source_dir / target).resolve().as_uri()
        return keep(f'<link href="{html.escape(target, quote=True)}" color="#006B74">'
                    f'{inline(label, source_dir)}</link>')

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, text)
    text = re.sub(r'`([^`]+)`', lambda m: keep(
        f'<font color="#355866">{html.escape(m.group(1))}</font>'), text)
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
    text = text.replace('&lt;br&gt;', '<br/>').replace('&lt;br/&gt;', '<br/>')
    for index, value in enumerate(tokens):
        text = text.replace(f'ZZTOKEN{index}ZZ', value)
    return text


class Rule(Flowable):
    def __init__(self, width: float, color=TEAL):
        super().__init__()
        self.width, self.height, self.color = width, 8, color

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(1.6)
        self.canv.line(0, 5, self.width, 5)


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_states = []

    def showPage(self):
        self.saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        count = len(self.saved_states)
        for state in self.saved_states:
            self.__dict__.update(state)
            self.setFont('ReportCJK', 7.4)
            self.setFillColor(MUTED)
            self.drawRightString(PAGE_W - MARGIN_X, 25, f'{self._pageNumber} / {count}')
            super().showPage()
        super().save()


class ResearchDoc(BaseDocTemplate):
    def __init__(self, filename: str, source_hash: str, **kwargs):
        super().__init__(filename, pagesize=A4, leftMargin=MARGIN_X,
                         rightMargin=MARGIN_X, topMargin=50, bottomMargin=43, **kwargs)
        self.source_hash = source_hash
        frame = Frame(MARGIN_X, 43, CONTENT_W, PAGE_H - 93,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='report', frames=[frame], onPage=self.chrome))

    def chrome(self, canv: canvas.Canvas, doc):
        canv.saveState()
        canv.setFillColor(TEAL)
        canv.rect(MARGIN_X, PAGE_H - 26, 23, 3, fill=1, stroke=0)
        canv.setFont('ReportCJK', 7.4)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN_X + 32, PAGE_H - 27, '台股趨勢與動能 | 固定策略 v1')
        canv.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 27, '2025 - 2026')
        canv.setStrokeColor(GRID)
        canv.setLineWidth(.5)
        canv.line(MARGIN_X, 39, PAGE_W - MARGIN_X, 39)
        canv.setFont('ReportCJK', 7)
        canv.drawString(MARGIN_X, 25, '條件式回測研究 | 不代表實盤可實現績效或競賽合規')
        canv.restoreState()


def make_table(lines: list[str], styles: dict, source_dir: Path) -> Table:
    rows = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in lines]
    rows = [row for row in rows if not all(re.fullmatch(r':?-{2,}:?', cell) for cell in row)]
    ncols = len(rows[0])
    if any(len(row) != ncols for row in rows):
        raise ValueError('Malformed Markdown table: differing column counts')
    if ncols == 2:
        widths = [CONTENT_W * .34, CONTENT_W * .66]
    elif ncols == 3:
        widths = [CONTENT_W * .40, CONTENT_W * .30, CONTENT_W * .30]
    elif ncols == 4:
        widths = [CONTENT_W * .43] + [CONTENT_W * .19] * 3
    else:
        widths = [CONTENT_W / ncols] * ncols
    cells = [[Paragraph(inline(cell, source_dir), styles['thead' if rowno == 0 else 'table'])
              for cell in row] for rowno, row in enumerate(rows)]
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT', spaceBefore=2, spaceAfter=10)
    commands = [
        ('BACKGROUND', (0, 0), (-1, 0), TEAL),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 9),
        ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('LINEBELOW', (0, 0), (-1, 0), .5, TEAL),
        ('LINEBELOW', (0, -1), (-1, -1), .5, GRID),
    ]
    for rowno in range(1, len(rows)):
        if rowno % 2:
            commands.append(('BACKGROUND', (0, rowno), (-1, rowno), PALE))
    table.setStyle(TableStyle(commands))
    return table


def parse_markdown(source: Path, styles: dict) -> list[Flowable]:
    lines = source.read_text(encoding='utf-8').splitlines()
    story: list[Flowable] = []
    index, first_body = 0, True
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line in ('<!-- pagebreak -->', '<!-- PAGEBREAK -->', '\\newpage'):
            story.append(PageBreak())
            index += 1
            continue
        if line.startswith('<!--'):
            index += 1
            continue
        if line.startswith('# '):
            story.extend([
                Paragraph('回測研究報告 / CONTINUOUS PORTFOLIO', styles['kicker']),
                Paragraph(inline(line[2:], source.parent), styles['title']),
                Rule(60), Spacer(1, 7),
            ])
            index += 1
            continue
        if line.startswith('## '):
            story.append(Paragraph(inline(line[3:], source.parent), styles['h2']))
            index += 1
            continue
        if line.startswith('### '):
            story.append(Paragraph(inline(line[4:], source.parent), styles['h3']))
            index += 1
            continue
        image_match = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', line)
        if image_match:
            label, image_path = image_match.groups()
            image_file = (source.parent / image_path).resolve()
            if not image_file.is_file():
                raise FileNotFoundError(f'Report figure not found: {image_file}')
            picture = Image(str(image_file))
            max_height = 365 if 'performance' in image_file.name else 235
            scale = min(CONTENT_W / picture.imageWidth, max_height / picture.imageHeight)
            picture.drawWidth = picture.imageWidth * scale
            picture.drawHeight = picture.imageHeight * scale
            picture.hAlign = 'CENTER'
            story.append(KeepTogether([picture, Paragraph(inline(label, source.parent), styles['caption'])]))
            index += 1
            continue
        if line.startswith('|'):
            block = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                block.append(lines[index])
                index += 1
            story.append(make_table(block, styles, source.parent))
            continue
        if line.startswith('```'):
            index += 1
            block = []
            while index < len(lines) and not lines[index].strip().startswith('```'):
                block.append(html.escape(lines[index]))
                index += 1
            story.append(Paragraph('<br/>'.join(block), styles['code']))
            index += 1
            continue
        if re.match(r'^[-*] ', line):
            story.append(Paragraph('• ' + inline(line[2:], source.parent), styles['bullet']))
            index += 1
            continue
        if re.match(r'^\d+\. ', line):
            story.append(Paragraph(inline(line, source.parent), styles['bullet']))
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(
            r'^(#|\||!\[|```|<!--|[-*] |\d+\. )', lines[index].strip()
        ):
            block.append(lines[index].strip())
            index += 1
        text = ' '.join(block)
        if first_body and text.startswith('**'):
            callout = Table([[Paragraph(inline(text, source.parent), styles['lead'])]],
                            colWidths=[CONTENT_W], spaceAfter=11)
            callout.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FAF3EE')),
                ('LINEBEFORE', (0, 0), (0, -1), 2.5, RUST),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            story.append(callout)
        else:
            style = styles['small'] if text.startswith(('來源：', '證據：')) else styles['body']
            story.append(Paragraph(inline(text, source.parent), style))
        first_body = False
    return story


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='reports/backtest_2025_to_now_v1.md')
    parser.add_argument('--output', default='reports/backtest_2025_to_now_v1.pdf')
    parser.add_argument('--font', type=Path, default=DEFAULT_FONT)
    parser.add_argument('--data-final', action='store_true',
                        help='Confirm the source and its linked results have completed verification.')
    parser.add_argument('--min-pages', type=int, default=4)
    parser.add_argument('--max-pages', type=int, default=7)
    args = parser.parse_args()
    if not args.data_final:
        parser.error('Refusing to publish before DATA_FINAL; pass --data-final only after verification.')
    source, output = ROOT / args.source, ROOT / args.output
    raw = source.read_text(encoding='utf-8')
    if re.search(r'\b(?:TODO|TBD|PLACEHOLDER|DATA_PENDING)\b', raw):
        raise ValueError('Canonical source still contains a placeholder marker')
    source_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    register_fonts(args.font)
    styles = make_styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = ResearchDoc(str(output), source_hash,
                      title='台股趨勢與動能 v1：2025 年至今條件式回測',
                      author='Codex / reproducible research',
                      subject=f'Canonical Markdown SHA256: {source_hash}')
    doc.build(parse_markdown(source, styles), canvasmaker=NumberedCanvas)
    reader = PdfReader(output)
    pages = len(reader.pages)
    if not args.min_pages <= pages <= args.max_pages:
        raise RuntimeError(f'PDF has {pages} pages; expected {args.min_pages}-{args.max_pages}. Review layout.')
    if any(not page.extract_text().strip() for page in reader.pages):
        raise RuntimeError('A PDF page contains no extractable text')
    print(f'PDF generated: {output}\nPages: {pages}\nSource SHA256: {source_hash}')


if __name__ == '__main__':
    main()
