"""Render the operator handbook. Run with Python and reportlab installed.

uv run --no-project --with reportlab python \
    docs/implementation/render_operator_handbook.py
"""
from pathlib import Path
import html
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
)

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "Studio_Operator_Handbook.md"
TARGET = HERE / "Mar_and_Kuku_Studio_MVP_Guide_and_Roadmap.pdf"
NAVY = colors.HexColor("#183459")
TEAL = colors.HexColor("#007E87")
INK = colors.HexColor("#263445")
GRAY = colors.HexColor("#64748B")
WIDTH = A4[0] - 88

styles = {
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9.5,
        leading=13.1, textColor=INK, spaceAfter=8),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=23,
        leading=27, textColor=NAVY, spaceAfter=14),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12,
        leading=16, textColor=TEAL, spaceBefore=8, spaceAfter=7),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.7,
        leading=11.7, textColor=INK, splitLongWords=True),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8.7,
        leading=11.7, textColor=NAVY, splitLongWords=True),
    "code": ParagraphStyle("code", fontName="Courier", fontSize=8.2,
        leading=11.2, textColor=NAVY, splitLongWords=True),
}

def inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8">\1</font>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return s

def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(TEAL)
    canvas.rect(0, A4[1]-9, A4[0], 9, fill=1, stroke=0)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(44, A4[1]-30, "MK STUDIO  /  OPERATOR HANDBOOK")
    canvas.setStrokeColor(colors.HexColor("#DCE4EB"))
    canvas.line(44, 40, A4[0]-44, 40)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRAY)
    canvas.drawString(44, 27, "23 OCT 2026  |  Reviewed baseline ff39fb7  |  Development MVP")
    canvas.drawRightString(A4[0]-44, 27, f"{doc.page} / 12")
    canvas.restoreState()

story=[]
pages=SOURCE.read_text().split("---PAGE---")
for pi, page in enumerate(pages):
    if pi:
        story.append(PageBreak())
    lines=page.strip().splitlines()
    i=0
    while i < len(lines):
        line=lines[i]
        if not line.strip():
            i+=1
            continue
        if line.startswith("```"):
            i+=1
            block=[]
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i]); i+=1
            p=Paragraph("<br/>".join(html.escape(x).replace(" ", "&#160;") for x in block), styles["code"])
            t=Table([[p]], colWidths=[WIDTH])
            t.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EFF5F8")),
                ("BOX",(0,0),(-1,-1),.5,colors.HexColor("#DCE4EB")),
                ("LEFTPADDING",(0,0),(-1,-1),10),
                ("RIGHTPADDING",(0,0),(-1,-1),10),
                ("TOPPADDING",(0,0),(-1,-1),8),
                ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ]))
            story.extend([t,Spacer(1,10)]);i+=1;continue
        if line.startswith("| "):
            rows=[]
            while i < len(lines) and lines[i].startswith("| "):
                cells=[c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append([Paragraph(inline(c),styles["label"] if n==0 else styles["cell"]) for n,c in enumerate(cells)])
                i+=1
            count=len(rows[0])
            widths=[WIDTH*.29,WIDTH*.71] if count==2 else [WIDTH*.18,WIDTH*.40,WIDTH*.42]
            if pi==0: widths=[WIDTH*.1,WIDTH*.9]
            t=Table(rows,colWidths=widths,hAlign="LEFT")
            t.setStyle(TableStyle([
                ("VALIGN",(0,0),(-1,-1),"TOP"),
                ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.HexColor("#F0F5F8"),colors.white]),
                ("LINEBELOW",(0,0),(-1,-1),.35,colors.HexColor("#DCE4EB")),
                ("LEFTPADDING",(0,0),(-1,-1),8),
                ("RIGHTPADDING",(0,0),(-1,-1),8),
                ("TOPPADDING",(0,0),(-1,-1),6),
                ("BOTTOMPADDING",(0,0),(-1,-1),6),
            ]))
            story.extend([t,Spacer(1,10)]);continue
        if line.startswith("# "):
            story.append(Paragraph(inline(line[2:]),styles["h1"]))
        elif line.startswith("## "):
            story.append(Paragraph(inline(line[3:]),styles["h2"]))
        else:
            story.append(Paragraph(inline(line),styles["body"]))
        i+=1

doc=SimpleDocTemplate(str(TARGET),pagesize=A4,rightMargin=44,leftMargin=44,
    topMargin=52,bottomMargin=52,title="Mar & Kuku Production Studio - Operator Handbook",
    author="Mar & Kuku Production Studio",subject="Startup, manual testing and readiness guide")
doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
print(TARGET)
