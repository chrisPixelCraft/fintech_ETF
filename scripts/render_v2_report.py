"""Render the audited v2 Markdown with correct version metadata."""
from pathlib import Path
import hashlib
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.render_report import (ResearchDoc,register_fonts,DEFAULT_FONT,make_styles,parse_markdown,
                                    NumberedCanvas,PAGE_W,PAGE_H,MARGIN_X,TEAL,MUTED,GRID)
from pypdf import PdfReader

class V2Doc(ResearchDoc):
    def chrome(self,canv,doc):
        canv.saveState();canv.setFillColor(TEAL);canv.rect(MARGIN_X,PAGE_H-26,23,3,fill=1,stroke=0)
        canv.setFont('ReportCJK',7.4);canv.setFillColor(MUTED)
        canv.drawString(MARGIN_X+32,PAGE_H-27,'台股 v2 | A / B / C / D 平行研究')
        canv.drawRightString(PAGE_W-MARGIN_X,PAGE_H-27,'2025-01 - 2026-09')
        canv.setStrokeColor(GRID);canv.setLineWidth(.5);canv.line(MARGIN_X,39,PAGE_W-MARGIN_X,39)
        canv.setFont('ReportCJK',7);canv.drawString(MARGIN_X,25,'已驗證研究帳務 | 非官方競賽成績 | BLOCK_SUBMISSION');canv.restoreState()

def main():
    source=ROOT/'reports/backtest_v2_final.md';output=ROOT/'reports/backtest_v2_final.pdf'
    raw=source.read_text();digest=hashlib.sha256(raw.encode()).hexdigest();register_fonts(DEFAULT_FONT)
    doc=V2Doc(str(output),digest,title='v2 策略與歷史回測比較報告',author='Codex',subject='Source SHA256: '+digest)
    doc.build(parse_markdown(source,make_styles()),canvasmaker=NumberedCanvas)
    pdf=PdfReader(output)
    if any(not p.extract_text().strip() for p in pdf.pages):raise ValueError('Empty PDF page')
    print(output);print('Pages:',len(pdf.pages))

if __name__=='__main__':main()
