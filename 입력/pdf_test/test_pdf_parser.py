"""
test_pdf_parser.py : PDF 파서 테스트 (팀 규칙 v2 기준, 정상 + 예외)

실행:  python test_pdf_parser.py
→ samples/ 의 PDF 13개를 파싱해서 팀 규칙대로 나오는지 하나씩 확인하고 ✅/❌ 를 보여줍니다.
"""

import json
from pathlib import Path

from pdf_parser import parse_pdf

S = Path(__file__).resolve().parent / "samples"
passed, failed = 0, 0

TOP_KEYS = ["source_file", "file_type", "page_count", "blocks", "errors"]
BLOCK_KEYS = ["order", "block_type", "level", "page", "text", "table"]


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  {detail}")


ALLOWED_ERRORS = {"empty_document", "corrupted_file"}


def check_rules(r):
    """모든 결과가 지켜야 할 팀 규칙 1~10 + 에러 코드 2종을 한 번에 검사"""
    problems = []
    if any(e not in ALLOWED_ERRORS for e in r["errors"]):
        problems.append(f"허용 안 된 에러 코드 {r['errors']}")
    if r["errors"] and r["blocks"]:
        problems.append("errors 가 있으면 blocks 는 [] 여야 함")
    if list(r) != TOP_KEYS:
        problems.append(f"최상위 칸 {list(r)}")
    if not isinstance(r["errors"], list):
        problems.append("규칙10 errors 가 리스트가 아님")
    if r["errors"] and not r["blocks"] and r["blocks"] != []:
        problems.append("규칙9 실패 시 blocks 는 []")
    for i, b in enumerate(r["blocks"], 1):
        t = b.get("block_type")
        if list(b) != BLOCK_KEYS:
            problems.append(f"블록{i} 칸 {list(b)} (block_id 없어야 함)")
        if t not in ("paragraph", "table", "heading"):
            problems.append(f"규칙1 블록{i} block_type={t}")
        if b.get("order") != i:
            problems.append(f"규칙2 블록{i} order={b.get('order')} (1부터 연속)")
        if not isinstance(b.get("page"), int) or b["page"] < 1:
            problems.append(f"규칙3 블록{i} page={b.get('page')}")
        if t == "heading" and b.get("level") not in (1, 2, 3):
            problems.append(f"규칙4 블록{i} heading level={b.get('level')}")
        if t != "heading" and b.get("level") is not None:
            problems.append(f"규칙4 블록{i} {t} 인데 level={b.get('level')}")
        if t == "table":
            rows = (b.get("table") or {}).get("rows")
            if not (isinstance(rows, list) and all(isinstance(row, list) for row in rows)):
                problems.append(f"규칙5·6 블록{i} table.rows 가 2차원 배열 아님")
            elif not any(c for row in rows for c in row):
                problems.append(f"규칙8 블록{i} 빈 표")
            if b.get("text") != "":
                problems.append(f"규칙7 블록{i} table 인데 text 가 비어있지 않음")
        else:
            if b.get("table") is not None:
                problems.append(f"규칙5 블록{i} {t} 인데 table 이 null 아님")
            if not (b.get("text") or "").strip():
                problems.append(f"규칙8 블록{i} 빈 텍스트 블록")
    try:
        json.dumps(r, ensure_ascii=False)
    except Exception as e:
        problems.append(f"JSON 변환 실패 {e}")
    check("팀 규칙 1~10 모두 지킴", not problems, problems)


def summary(r):
    return [(b["block_type"], b["level"], b["page"]) for b in r["blocks"]]


print("\n[1] 정상 PDF (2페이지, 제목 3단계 + 문단)")
r = parse_pdf(S / "01_normal.pdf")
check_rules(r)
check("page_count = 2, errors = []", r["page_count"] == 2 and r["errors"] == [], (r["page_count"], r["errors"]))
check("순서·종류·단계·페이지", summary(r) == [
    ("heading", 1, 1), ("heading", 2, 1), ("paragraph", None, 1), ("paragraph", None, 1),
    ("heading", 2, 2), ("heading", 3, 2), ("paragraph", None, 2), ("paragraph", None, 2)], summary(r))
check("제목 글자", [b["text"] for b in r["blocks"] if b["block_type"] == "heading"]
      == ["계정관리 절차서", "제1장 총칙", "제2장 계정 관리", "제1절 계정 발급"])
check("두 줄짜리 제2조가 한 문단으로 합쳐짐",
      any(b["text"].startswith("제2조") and "동일하게 적용한다" in b["text"] for b in r["blocks"]))

print("\n[2] 문단 → 표 → 문단")
r = parse_pdf(S / "02_table.pdf")
check_rules(r)
check("순서가 paragraph, table, paragraph", [b["block_type"] for b in r["blocks"]] == ["paragraph", "table", "paragraph"],
      [b["block_type"] for b in r["blocks"]])
rows = r["blocks"][1]["table"]["rows"] if len(r["blocks"]) > 1 else []
check("표 3줄 × 3칸", rows == [["계정", "승인자", "승인일"], ["user01", "홍길동", "2026-09-01"], ["user02", "김철수", "2026-09-02"]], rows)
check("표 안 글자가 문단으로 또 나오지 않음", not any("홍길동" in b["text"] for b in r["blocks"]))

print("\n[3] 병합 셀이 있는 표")
r = parse_pdf(S / "03_merged_table.pdf")
check_rules(r)
tbl = next((b for b in r["blocks"] if b["block_type"] == "table"), None)
check("병합된 칸은 빈 문자열", tbl and tbl["table"]["rows"][0] == ["시스템 접근권한", "", "비고"],
      tbl and tbl["table"]["rows"][0])

print("\n[4] 빈 PDF")
r = parse_pdf(S / "04_blank.pdf")
check_rules(r)
check("blocks = [], errors = ['empty_document']", r["blocks"] == [] and r["errors"] == ["empty_document"], r["errors"])

print("\n[5] 스캔본 (그림만 있음)")
r = parse_pdf(S / "05_scanned.pdf")
check_rules(r)
check("blocks = [], errors = ['empty_document']", r["blocks"] == [] and r["errors"] == ["empty_document"], r["errors"])

print("\n[6] 일부 페이지만 빈 PDF (2페이지가 빔)")
r = parse_pdf(S / "06_partial_blank.pdf")
check_rules(r)
check("나머지 페이지는 뽑힘 (1쪽, 3쪽)", [b["page"] for b in r["blocks"]] == [1, 3], [b["page"] for b in r["blocks"]])
check("errors = [] (빈 페이지는 건너뛰기만 함)", r["errors"] == [], r["errors"])

print("\n[7] 제목 4단계 → 4번째는 level 3 으로")
r = parse_pdf(S / "11_deep_headings.pdf")
check_rules(r)
check("level 1, 2, 3, 3 + 본문 2개", summary(r) == [
    ("heading", 1, 1), ("heading", 2, 1), ("heading", 3, 1), ("heading", 3, 1),
    ("paragraph", None, 1), ("paragraph", None, 1)], summary(r))

print("\n[8] 워드/한글식 표 (칸 배경색·안쪽 여백 상자 때문에 칸이 쪼개져 보이는 표)")
r = parse_pdf(S / "12_shaded_table.pdf")
check_rules(r)
tbl = next((b for b in r["blocks"] if b["block_type"] == "table"), None)
rows = tbl["table"]["rows"] if tbl else None
check("빈 칸·어긋남 없이 3줄 × 3칸, 두 줄 칸은 한 칸으로", rows == [
    ["문서번호", "문서명", "보존"], ["POL-001", "정보보호 정책", "영구"], ["G-01", "정보보호 조직 운영지침", "5년"]], rows)

print("\n[9] 흐름도 줄 + 표가 붙은 경우 / 줄 간격 넓은 문단 / ● 목록")
r = parse_pdf(S / "13_flow_and_spacing.pdf")
check_rules(r)
tables = [b["table"]["rows"] for b in r["blocks"] if b["block_type"] == "table"]
check("흐름도와 표가 따로 나옴", tables == [
    [["접수", "▶", "검토", "▶", "승인"]],
    [["단계", "내용"], ["1", "신청서 접수"], ["2", "부서장 검토 후 승인"]]], tables)
paras = [b["text"] for b in r["blocks"] if b["block_type"] == "paragraph" and b["page"] == 2]
check("자동 줄바꿈된 문장은 한 문단, ● ㅇ 항목은 각각 한 문단", paras == [
    "정보보호 담당자는 매 분기 접근권한을 검토하고 그 결과를 부서장에게 보고하여야 하며 필요한 경우 조치계획을 수립한다.",
    "● 퇴직자의 계정은 퇴직 당일 삭제하고 삭제 결과를 인사총무부와 정보보호부에 즉시 통보하여야",
    "● 장기 미사용 계정은 잠금 처리한다.",
    "ㅇ (정보보호부) 접근권한 검토 결과를 반기마다 정보보호위원회에 보고하고 미흡 사항에 대한 개선",
    "ㅇ (IT운영부) 개선 조치 결과를 제출한다."], paras)

print("\n[10] 주소(URL) 중간 줄바꿈은 띄어쓰기 없이 붙임")
from pdf_parser import _join_lines
check("URL 이어붙임", _join_lines(["https://example.com/acc", "ount?id=1"]) == "https://example.com/account?id=1")
check("URL 두 개는 띄어쓰기 유지", _join_lines(["https://a.com/x", "https://b.com/y"]) == "https://a.com/x https://b.com/y")
check("URL 뒤 한글은 띄어쓰기 유지", _join_lines(["https://a.com/x", "참고 사이트"]) == "https://a.com/x 참고 사이트")
check("일반 문장은 띄어쓰기로 이음", _join_lines(["첫째 줄", "둘째 줄"]) == "첫째 줄 둘째 줄")

print("\n[11] 손상 / 잘림 / 암호 / 0바이트 / 없는 파일 → 예외 없이 blocks=[] + errors")
cases = [("07_corrupted.pdf", "corrupted_file"), ("08_truncated.pdf", "corrupted_file"),
         ("09_encrypted.pdf", "corrupted_file"), ("10_zero_byte.pdf", "corrupted_file"),
         ("없는파일.pdf", "corrupted_file")]
for fname, expected in cases:
    try:
        r = parse_pdf(S / fname)
        ok = r["blocks"] == [] and r["errors"] == [expected] and list(r) == TOP_KEYS
        check(f"{fname:20} → {expected}", ok, r["errors"])
    except Exception as e:
        check(f"{fname:20} → {expected}", False, f"예외 발생: {e!r}")

print(f"\n결과: ✅ {passed}개 통과, ❌ {failed}개 실패")
raise SystemExit(1 if failed else 0)
