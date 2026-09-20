"""
make_samples.py : PDF 파서 테스트용 연습 파일 만들기

실행:  python make_samples.py
→ samples/ 폴더에 테스트용 PDF 13개가 만들어집니다.
  (zip에 이미 만들어진 samples/ 가 들어 있어서, 보통은 실행할 필요 없어요)

필요 도구: reportlab, pypdf, pillow  (파서 자체에는 필요 없고, 샘플 만들 때만 필요)
"""

import io
import os
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = Path(__file__).resolve().parent / "samples"
OUT.mkdir(exist_ok=True)

# ── 한글 폰트 찾기 (윈도우: 맑은고딕 / 리눅스: 나눔고딕) ──
FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/Library/Fonts/AppleGothic.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
if FONT_PATH is None:
    raise SystemExit("한글 폰트를 찾지 못했습니다. FONT_CANDIDATES 에 폰트 경로를 추가하세요.")
pdfmetrics.registerFont(TTFont("KR", FONT_PATH))

BODY = ParagraphStyle("body", fontName="KR", fontSize=11, leading=16, spaceAfter=14)
TITLE = ParagraphStyle("title", fontName="KR", fontSize=16, leading=22, spaceAfter=18)


def build(name, story):
    SimpleDocTemplate(str(OUT / name), pagesize=A4).build(story)


def grid_table(rows, spans=()):
    t = Table(rows, colWidths=[120, 120, 160])
    style = [("FONTNAME", (0, 0), (-1, -1), "KR"), ("FONTSIZE", (0, 0), (-1, -1), 10),
             ("GRID", (0, 0), (-1, -1), 0.8, colors.black)]
    for s in spans:
        style.append(("SPAN",) + s)
    t.setStyle(TableStyle(style))
    return t


H1 = ParagraphStyle("h1", fontName="KR", fontSize=18, leading=24, spaceAfter=16)
H2 = ParagraphStyle("h2", fontName="KR", fontSize=15, leading=20, spaceAfter=12)
H3 = ParagraphStyle("h3", fontName="KR", fontSize=13, leading=18, spaceAfter=10)

# 01 정상 : 2페이지, 제목 3단계 + 문단
build("01_normal.pdf", [
    Paragraph("계정관리 절차서", H1),
    Paragraph("제1장 총칙", H2),
    Paragraph("제1조 (목적) 본 절차는 사용자 계정의 등록, 변경, 삭제에 관한 사항을 정한다.", BODY),
    Paragraph("제2조 (적용범위) 본 절차는 회사의 모든 정보시스템 사용자에게 적용한다. "
              "외부 협력업체 직원이 정보시스템을 사용하는 경우에도 동일하게 적용한다.", BODY),
    PageBreak(),
    Paragraph("제2장 계정 관리", H2),
    Paragraph("제1절 계정 발급", H3),
    Paragraph("제3조 (계정 발급) 사용자 계정은 소속 부서장의 승인을 받은 후 생성한다.", BODY),
    Paragraph("제4조 (계정 삭제) 퇴직자의 계정은 퇴직 당일 삭제한다.", BODY),
])

# 11 제목 4단계 : 4번째 단계는 level 3 으로 묶임
build("11_deep_headings.pdf", [
    Paragraph("정보보호 정책", ParagraphStyle("a", fontName="KR", fontSize=20, leading=26, spaceAfter=12)),
    Paragraph("제1장 일반", ParagraphStyle("b", fontName="KR", fontSize=17, leading=22, spaceAfter=12)),
    Paragraph("제1절 목적", ParagraphStyle("c", fontName="KR", fontSize=14, leading=19, spaceAfter=12)),
    Paragraph("1. 세부 목적", ParagraphStyle("d", fontName="KR", fontSize=13, leading=17, spaceAfter=12)),
    Paragraph("본 정책은 회사의 정보자산을 보호하기 위해 필요한 사항을 정한다.", BODY),
    Paragraph("정보보호 책임자는 매년 정책을 검토한다.", BODY),
])

# 02 표 : 문단 → 표 → 문단 (순서 확인용)
build("02_table.pdf", [
    Paragraph("계정 관리대장은 아래와 같다.", BODY),
    grid_table([["계정", "승인자", "승인일"],
                ["user01", "홍길동", "2026-09-01"],
                ["user02", "김철수", "2026-09-02"]]),
    Spacer(1, 16),
    Paragraph("관리대장은 매 분기 검토한다.", BODY),
])

# 03 병합 셀 : 첫 줄 두 칸 병합
build("03_merged_table.pdf", [
    Paragraph("접근권한 현황", BODY),
    grid_table([["시스템 접근권한", "", "비고"],
                ["서버", "관리자", "승인 필요"],
                ["DB", "개발자", "읽기 전용"]], spans=[((0, 0), (1, 0))]),
])

# 04 빈 문서 : 아무것도 없는 페이지
c = canvas.Canvas(str(OUT / "04_blank.pdf"), pagesize=A4)
c.showPage()
c.save()

# 05 스캔본 흉내 : 글자가 그림으로만 들어 있음
img = Image.new("RGB", (800, 300), "white")
ImageDraw.Draw(img).text((40, 130), "SCANNED DOCUMENT (image only)", fill="black")
img_path = OUT / "_tmp_scan.png"
img.save(img_path)
c = canvas.Canvas(str(OUT / "05_scanned.pdf"), pagesize=A4)
c.drawImage(str(img_path), 50, 500, width=500, height=190)
c.showPage()
c.save()
img_path.unlink()

# 06 일부 페이지만 빈 문서 : 1쪽 글자, 2쪽 빈 페이지, 3쪽 글자
build("06_partial_blank.pdf", [
    Paragraph("1쪽 내용입니다. 정보보호 정책을 수립한다.", BODY),
    PageBreak(), Spacer(1, 1), PageBreak(),
    Paragraph("3쪽 내용입니다. 정책은 연 1회 검토한다.", BODY),
])

# 07 손상 : PDF가 아닌 쓰레기 값
(OUT / "07_corrupted.pdf").write_bytes(b"this is not a pdf" * 50)

# 08 잘린 파일 : 정상 PDF의 앞부분만
data = (OUT / "01_normal.pdf").read_bytes()
(OUT / "08_truncated.pdf").write_bytes(data[: len(data) // 3])

# 09 암호 : 열람 비밀번호 걸린 PDF
reader = PdfReader(str(OUT / "01_normal.pdf"))
writer = PdfWriter()
for p in reader.pages:
    writer.add_page(p)
writer.encrypt(user_password="1234", owner_password="owner")
buf = io.BytesIO()
writer.write(buf)
(OUT / "09_encrypted.pdf").write_bytes(buf.getvalue())

# 10 0바이트 파일
(OUT / "10_zero_byte.pdf").write_bytes(b"")

# 12 워드/한글식 표 : 테두리는 가는 검은 사각형, 칸마다 배경색 상자 + 안쪽 여백 상자(줄마다)를 따로 그림
#    → 한 칸이 가는 칸 여러 개로 쪼개져 보이는 문제 재현용
c = canvas.Canvas(str(OUT / "12_shaded_table.pdf"), pagesize=A4)
c.setFont("KR", 11)
c.drawString(60, 780, "문서 목록은 아래와 같다.")
cols = [60, 160, 360, 480]                 # 칸 경계 x
rows_y = [750, 720, 690, 650]              # 칸 경계 y (위→아래, 마지막 줄은 두 줄짜리 칸)
data = [["문서번호", "문서명", "보존"],
        ["POL-001", "정보보호 정책", "영구"],
        ["G-01", ["정보보호 조직", "운영지침"], "5년"]]
BG = (0.85, 0.89, 0.95)
for r in range(3):
    for k in range(3):
        x0, x1, y_top, y_bot = cols[k], cols[k + 1], rows_y[r], rows_y[r + 1]
        lines = data[r][k] if isinstance(data[r][k], list) else [data[r][k]]
        c.setFillColorRGB(*BG)
        c.rect(x0 + 0.5, y_bot + 0.5, x1 - x0 - 1, y_top - y_bot - 1, stroke=0, fill=1)   # 칸 배경색
        c.rect(x0 + 0.5, y_top - 3.6, x1 - x0 - 1, 3.1, stroke=0, fill=1)                  # 위 여백 띠
        c.rect(x0 + 0.5, y_bot + 0.5, x1 - x0 - 1, 3.1, stroke=0, fill=1)                  # 아래 여백 띠
        box_h = (y_top - y_bot - 7.2) / len(lines)                                     # 글자 상자(줄마다)
        for i in range(len(lines)):
            c.rect(x0 + 4.3, y_top - 3.6 - (i + 1) * box_h, x1 - x0 - 8.6, box_h - 0.1, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("KR", 10)
        for i, text in enumerate(lines):
            c.drawString(x0 + 6, y_top - 3.6 - (i + 1) * box_h + 4, text)
        c.rect(x0 - 0.25, y_top - 3.6, 0.5, 3.6, stroke=0, fill=1)                        # 테두리 조각
        c.rect(x1 - 0.25, y_top - 3.6, 0.5, 3.6, stroke=0, fill=1)
c.setFillColorRGB(0, 0, 0)
for x in cols:                                                                         # 세로 테두리 (가는 사각형)
    c.rect(x - 0.25, rows_y[-1], 0.5, rows_y[0] - rows_y[-1], stroke=0, fill=1)
for y in rows_y:                                                                       # 가로 테두리
    c.rect(cols[0], y - 0.25, cols[-1] - cols[0], 0.5, stroke=0, fill=1)
c.showPage()
c.save()

# 13 흐름도 줄 + 표가 딱 붙은 경우, 줄 간격이 넓은 문단, 목록(●) 항목
#    → 흐름도와 표가 따로 나와야 하고, 자동 줄바꿈된 문장은 한 문단, ● 항목은 각각 한 문단
flow = Table([["접수", "▶", "검토", "▶", "승인"]], colWidths=[100, 20, 100, 20, 160])
flow.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "KR"), ("FONTSIZE", (0, 0), (-1, -1), 10),
                          ("GRID", (0, 0), (-1, -1), 0.8, colors.black)]))
steps = Table([["단계", "내용"], ["1", "신청서 접수"], ["2", "부서장 검토 후 승인"]], colWidths=[60, 340])
steps.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "KR"), ("FONTSIZE", (0, 0), (-1, -1), 10),
                           ("GRID", (0, 0), (-1, -1), 0.8, colors.black)]))
build("13_flow_and_spacing.pdf", [flow, steps])

# 같은 PDF 두 번째 페이지는 canvas 로 직접 그림 (줄 간격 21pt = 넓은 줄 간격)
first = PdfReader(str(OUT / "13_flow_and_spacing.pdf")).pages[0]
buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=A4)
c.setFont("KR", 10)
lines13 = ["정보보호 담당자는 매 분기 접근권한을 검토하고 그 결과를 부서장에게 보고하여야 하며 필요한 경우",
           "조치계획을 수립한다.",
           "● 퇴직자의 계정은 퇴직 당일 삭제하고 삭제 결과를 인사총무부와 정보보호부에 즉시 통보하여야",
           "● 장기 미사용 계정은 잠금 처리한다.",
           "ㅇ (정보보호부) 접근권한 검토 결과를 반기마다 정보보호위원회에 보고하고 미흡 사항에 대한 개선",
           "ㅇ (IT운영부) 개선 조치 결과를 제출한다."]
for i, text in enumerate(lines13):
    c.drawString(60, 700 - i * 21, text)
c.showPage()
c.save()
writer = PdfWriter()
writer.add_page(first)
writer.add_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
with open(OUT / "13_flow_and_spacing.pdf", "wb") as f:
    writer.write(f)

print("샘플 생성 완료:", sorted(p.name for p in OUT.glob("*.pdf")))
