"""
phase2_report.py : 항목별 근거 리포트 출력

    python phase2_report.py --out reports/

    reports/control_report.html   통제항목별 리포트 (인쇄용)
    reports/control_report.csv    문항 한 줄씩 (엑셀용)
    reports/coverage.csv          통제항목 101개 제출/미제출
    reports/reconcile.txt         집계와 상세 건수 대조

조회 응답(JSON)과 다르다. 저건 화면이 쓰는 값이고 이건 사람이 읽고 제출하는 문서다.
근거 문장은 원문 그대로 넣는다. 줄이거나 고치면 인용이 아니게 된다.
"""
from __future__ import annotations

import csv
import html
import json
import os
import sys
from pathlib import Path


class ReportError(Exception):
    """리포트를 못 만든 이유. code 는 화면이 분기할 때 쓴다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

try:
    from . import phase2_result_store as store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import phase2_result_store as store


CSV_HEADER = [
    "통제항목", "통제항목명", "구역", "종합판정", "WBS판정", "검토상태",
    "증적", "버전", "사람수정", "문항", "문항판정", "사유", "사유코드",
    "중요문항", "검사종류", "근거수", "근거문장", "청크", "쪽", "보류사유",
]


def report_rows(control_id: str | None = None, evidence_id: str | None = None):
    """문항 한 줄씩. 근거가 여러 개면 줄이 여러 개다. 근거가 없으면 한 줄."""
    if control_id:
        rows = store.by_control(control_id)
    elif evidence_id:
        rows = store.by_evidence(evidence_id)
    else:
        rows = store.all_results()

    out = []
    for r in rows:
        one = store._one(r)
        base = {
            "통제항목": one["control_id"],
            "통제항목명": one["control_name"],
            "구역": store._section(one["control_id"]),
            "종합판정": one["overall_result"],
            "WBS판정": one["overall_result_wbs"],
            "검토상태": one["review_state"],
            "증적": one["evidence_id"],
            "버전": one["version"],
            "사람수정": "O" if one["modified_by_human"] else "",
            "보류사유": " / ".join(b["code"] for b in one["approval_blockers"]),
        }
        items = one["items"] or [{}]
        for it in items:
            item = dict(base,
                        문항=it.get("item_id", ""),
                        문항판정=it.get("result", ""),
                        사유=it.get("reason", ""),
                        사유코드=",".join(it.get("reason_codes") or []),
                        중요문항="O" if it.get("critical") else "",
                        검사종류=it.get("check_kind", ""),
                        근거수=len(it.get("citations") or []))
            cits = it.get("citations") or [{}]
            for c in cits:
                out.append(dict(item,
                                근거문장=c.get("quote", ""),
                                청크=c.get("chunk_id", ""),
                                쪽=c.get("page") if c.get("page") is not None else ""))
    return out


def to_csv(path, rows=None):
    rows = report_rows() if rows is None else rows
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 엑셀이 한글을 깨뜨리지 않게 BOM 을 붙인다.
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_HEADER})
    return p


def coverage_csv(path, controls_path=None):
    cov = store.coverage(controls_path)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = ["통제항목", "통제항목명", "구역", "제출상태", "증적수", "증적",
            "종합판정", "WBS판정", "검토상태", "제외사유"]
    label = {"submitted": "제출", "excluded_only": "제외", "not_submitted": "미제출"}
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        for c in cov["controls"]:
            w.writerow([c["control_id"], c["control_name"] or "", c["section"],
                        label.get(c["state"], c["state"]), c["evidence_count"],
                        " ".join(c["evidence_ids"]),
                        c["overall_result"] or "", c["overall_result_wbs"] or "",
                        c["review_state"] or "",
                        " / ".join(x["reason_code"] for x in c["excluded"])])
    return p, cov


# ── 집계와 상세 건수 대조 ─────────────────────────────────────────────
def reconcile(controls_path=None) -> dict:
    """
    종합 화면 숫자와 상세 줄 수가 맞는지 본다.
    안 맞으면 둘 중 하나가 틀린 것이므로 어디가 어긋났는지 적어 돌려준다.
    """
    s = store.summary()
    cov = store.coverage(controls_path)
    rows = store.all_results()
    details = [store._one(r) for r in rows]

    checks = []

    def ck(name, got, want, note=""):
        checks.append({"항목": name, "집계": got, "상세": want,
                       "일치": got == want, "비고": note})

    ck("판정 건수", s["judged_result_total"], len(details))
    ck("판정이 나온 증적 수", s["judged_evidence_total"],
       len({d["evidence_id"] for d in details}))
    ck("통제항목 수", s["control_count"], len({d["control_id"] for d in details}))

    for v in store.OVERALL_VALUES:
        ck(f"종합판정 {v}", s["verdicts"][v],
           sum(1 for d in details if d["overall_result"] == v))
    for k in ("적정", "부분적정", "부적정", "판단불가"):
        ck(f"WBS {k}", s["verdicts_wbs"][k],
           sum(1 for d in details if d["overall_result_wbs"] == k))
    for st in store.REVIEW_STATES:
        ck(f"검토상태 {st}", s["review_states"][st],
           sum(1 for d in details if d["review_state"] == st))

    ck("검토 대기(PENDING)", s["review_required"],
       sum(1 for d in details if d["review_state"] == "PENDING"))
    ck("안 끝난 검토", s["unresolved_review_total"],
       sum(1 for d in details
           if d["review_state"] in ("PENDING", "REVALIDATING", "REJECTED")))

    item_eff = {}
    for d in details:
        for it in d["items"] or []:
            if it.get("result"):
                item_eff[it["result"]] = item_eff.get(it["result"], 0) + 1
    for v in store.ITEM_VALUES:
        ck(f"문항 {v}", s["item_results_effective"][v], item_eff.get(v, 0))

    ck("제외 건수", s["excluded_total"], len(store.excluded()))

    # 커버리지는 단위가 달라 같은 수가 아니다. 포함 관계만 본다.
    judged_controls = {d["control_id"] for d in details}
    cov_submitted = {c["control_id"] for c in cov["controls"]
                     if c["state"] == "submitted"}
    ck("커버리지 제출 통제항목", len(cov_submitted), len(judged_controls),
       "단위가 같아야 한다 — 판정이 있는 통제항목 = 제출")
    ck("커버리지 분모", cov["control_total"], len(store.load_control_catalog(controls_path)),
       "controls.json 전체")

    bad = [c for c in checks if not c["일치"]]
    return {"통과": len(checks) - len(bad), "실패": len(bad),
            "checks": checks, "어긋난 것": bad,
            "주의": "summary 는 증적×통제항목 단위, coverage 는 통제항목 단위다. "
                    "두 숫자를 같은 표에 나란히 놓지 않는다."}


def reconcile_text(path=None, controls_path=None):
    r = reconcile(controls_path)
    lines = ["집계와 상세 건수 대조", "=" * 60]
    for c in r["checks"]:
        mark = "OK  " if c["일치"] else "어긋남"
        lines.append(f"{mark}  {c['항목']:<28} 집계 {c['집계']:>5}   상세 {c['상세']:>5}"
                     + (f"   {c['비고']}" if c["비고"] else ""))
    lines += ["=" * 60, f"{r['통과']} 일치 / {r['실패']} 어긋남", "", r["주의"]]
    text = "\n".join(lines)
    if path:
        p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    return text, r



# ── 커버리지 장 (어디에 매핑됐고 어디에 안 됐는지) ────────────────────
_STATE_KO = {"submitted": "제출", "excluded_only": "제외만", "not_submitted": "미제출"}

# ISMS-P 인증기준 대분류. 번호만 있으면 읽는 사람이 어느 영역인지 모른다.
SECTION_KO = {"1": "관리체계 수립 및 운영",
              "2": "보호대책 요구사항",
              "3": "개인정보 처리단계별 요구사항"}


def _coverage_block(cov, e):
    """구역별 제출 현황 표 + 미제출 통제항목 목록(이름 포함)."""
    out = ["<div class='ctl'><h2>통제항목 커버리지</h2>"]
    out.append(f"<div class='meta'>전체 {cov['control_total']}개 중 "
               f"제출 <b>{cov['submitted']}</b>개 · "
               f"제외만 {cov['excluded_only']}개 · "
               f"미제출 <b>{cov['not_submitted']}</b>개 "
               f"({cov['coverage_rate']}%)</div>")

    out.append("<table class='cov'><thead><tr><th class='c1'>구역</th>"
               "<th>영역</th><th>통제항목</th>"
               "<th>제출</th><th>제외만</th><th>미제출</th><th>검토 열림</th>"
               "<th>제출률</th></tr></thead><tbody>")
    for s in cov["by_section"]:
        cls = " class='zero'" if s["submitted"] == 0 else ""
        big = SECTION_KO.get(s["section"].split(".")[0], "")
        out.append(f"<tr{cls}><td class='c1'>{e(s['section'])}</td>"
                   f"<td>{e(big)}</td>"
                   f"<td class='n'>{s['control_total']}</td>"
                   f"<td class='n'>{s['submitted']}</td>"
                   f"<td class='n'>{s['excluded_only']}</td>"
                   f"<td class='n'>{s['not_submitted']}</td>"
                   f"<td class='n'>{s['review_open']}</td>"
                   f"<td class='n'>{s['coverage_rate']}%</td></tr>")
    out.append(f"<tr><th class='c1'>합계</th><th></th>"
               f"<th class='n'>{cov['control_total']}</th>"
               f"<th class='n'>{cov['submitted']}</th>"
               f"<th class='n'>{cov['excluded_only']}</th>"
               f"<th class='n'>{cov['not_submitted']}</th>"
               f"<th class='n'>{cov['review_open_controls']}</th>"
               f"<th class='n'>{cov['coverage_rate']}%</th></tr>")
    out.append("</tbody></table>")
    out.append("</div>")
    return out


def _missing_block(cov, e):
    """미제출 통제항목을 구역별로 묶어 이름까지 적는다.

    번호만 늘어놓으면 '무엇을 더 내야 하는지' 를 읽는 사람이 알 수 없다.
    이 리포트를 받는 사람이 제일 먼저 보는 곳이라 이름을 같이 적는다.

    표로 그린다. inline-block 으로 늘어놓으면 PDF 변환기가 줄바꿈을 못 해
    오른쪽이 잘려 나간다.
    """
    miss = [c for c in cov["controls"] if c["state"] != "submitted"]
    if not miss:
        return ["<div class='ctl'><h2>미제출 통제항목</h2>"
                "<div class='meta'>없습니다. 통제항목 전체에 증적이 연결됐습니다.</div>"
                "</div>"]
    out = [f"<div class='ctl'><h2>미제출 통제항목 {len(miss)}개</h2>",
           "<div class='meta'>아래 항목에는 연결된 증적이 없습니다. "
           "증적을 올리지 않았거나, 올린 증적이 이 항목으로 매핑되지 않았습니다.</div>",
           "<table class='cov miss'><thead><tr>"
           "<th class='c1'>통제항목</th><th>이름</th><th class='c3'>상태</th>"
           "</tr></thead><tbody>"]
    by_sec = {}
    for c in miss:
        by_sec.setdefault(c["section"], []).append(c)
    for sec in sorted(by_sec, key=store._sort_key):
        out.append(f"<tr class='sec'><td colspan='3'>{e(sec)} "
                   f"{e(SECTION_KO.get(sec.split('.')[0], ''))} — "
                   f"{len(by_sec[sec])}개</td></tr>")
        for c in by_sec[sec]:
            if c["state"] == "excluded_only":
                codes = ", ".join(x["reason_code"] for x in c["excluded"])
                state = f"제외 · {e(codes)}"
            else:
                state = "증적 없음"
            out.append(f"<tr><td class='c1'>{e(c['control_id'])}</td>"
                       f"<td>{e(c['control_name'] or '')}</td>"
                       f"<td class='c3'>{state}</td></tr>")
    out.append("</tbody></table></div>")
    return out


# ── 보완 권고 ─────────────────────────────────────────────────────────
# 판정 결과를 임의로 바꾸지 않고, 이미 나온 문항 판정·사유코드와
# 체크리스트의 후보 증적을 이용해 "다음에 무엇을 보완해야 하는지"만 설명한다.
# UNKNOWN은 미흡 확정이 아니므로 "수정"이 아니라 "추가 확인/증적 보완"으로 안내한다.
_CHECKLIST_ITEM_INDEX = None

_REC_ACTION = {
    "P2_NM_RULE_NOT_DEFINED": (
        "수정 필요",
        "해당 요구사항을 충족하는 절차·기준을 수립하고, 적용 대상·책임·주기·수행 방법이 문서에서 확인되도록 보완하세요."
    ),
    "P2_NM_REQUIRED_ACTION_NOT_DONE": (
        "수정 필요",
        "필요한 실행·검토·기록을 실제로 수행하고, 수행일자·대상·결과가 확인되는 증적을 남기세요."
    ),
    "P2_NM_RULE_VIOLATED": (
        "수정 필요",
        "현재 설정 또는 처리 방식을 적용 기준에 맞게 수정하고, 변경된 상태와 적용 시점을 확인할 수 있는 증적을 남기세요."
    ),
    "P2_U_EVIDENCE_INSUFFICIENT": (
        "추가 확인 필요",
        "판단에 필요한 직접 근거가 부족합니다. 해당 요구사항의 이행 여부가 확인되는 자료를 추가 제출하세요."
    ),
    "P2_U_SCOPE_OR_RULE_UNCLEAR": (
        "추가 확인 필요",
        "증적의 적용 대상·범위와 적용 기준을 명확히 확인할 수 있는 자료를 추가하세요."
    ),
    "P2_U_SUBJECT_OR_EVENT_UNLINKED": (
        "추가 확인 필요",
        "제출한 자료가 검토 대상·사건과 동일한 것인지 연결할 수 있도록 시스템명·계정·자산·계약·사건 식별정보 등을 보완하세요."
    ),
    "P2_U_TIME_OR_VERSION_UNCLEAR": (
        "추가 확인 필요",
        "작성일·수행일·효력일·버전과 검토 대상 기간을 확인할 수 있는 정보를 추가하세요."
    ),
    "P2_U_EVIDENCE_UNREADABLE": (
        "추가 확인 필요",
        "필요한 부분을 판독할 수 있도록 원본 또는 더 선명한 자료를 제출하고, 핵심 내용이 가려지지 않았는지 확인하세요."
    ),
    "P2_U_EVIDENCE_CONFLICT": (
        "추가 확인 필요",
        "상충하는 자료의 대상·시점·효력 관계를 확인하고, 승인·시행 이력 등 어떤 자료가 해당 시점에 유효한지 판단할 근거를 추가하세요."
    ),
    "P2_U_NO_TRIGGER_EVENT": (
        "추가 확인 필요",
        "검토 기간에 해당 사건이 없었다는 공식 확인 또는 적용 제외 여부를 판단할 수 있는 근거를 추가하세요."
    ),
    "P2_U_EXCEPTION_UNRESOLVED": (
        "추가 확인 필요",
        "예외 적용 사유·승인 내역·책임추적 방법을 확인할 수 있는 자료를 추가하여 예외 적용의 적정성을 검토할 수 있게 하세요."
    ),
    "P2_U_NOT_YET_DUE": (
        "추가 확인 필요",
        "이행 기한과 현재 진행 상태를 확인할 수 있는 자료를 보완하고, 기한 도래 후 완료 증적을 다시 확인하세요."
    ),
    "P2_U_OBSERVATION_INCOMPLETE": (
        "추가 확인 필요",
        "일부 시점 자료만으로는 전체 기간을 확인할 수 없습니다. 검토 대상 기간 전체를 포괄하는 기록 또는 추가 표본을 제출하세요."
    ),
}


def _load_checklist_item_index():
    """full_checklist_draft.json 의 item_id -> 질문/권장 증적 표."""
    global _CHECKLIST_ITEM_INDEX
    if _CHECKLIST_ITEM_INDEX is not None:
        return _CHECKLIST_ITEM_INDEX

    root = _repo_root()
    candidates = [
        root / "phase2_기준" / "full_checklist_draft.json",
        root / "phase2_기준" / "chapter2_full_checklist_draft.json",
    ]
    idx = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            for ctl in raw.get("controls") or []:
                for it in ctl.get("items") or []:
                    iid = it.get("item_id")
                    if iid:
                        idx[iid] = it
            if idx:
                break
        except Exception:  # 리포트 생성 자체를 막지는 않는다.
            continue
    _CHECKLIST_ITEM_INDEX = idx
    return idx


def _recommendation_for_item(it: dict) -> dict | None:
    """문항 한 건의 판정 결과에서 사용자용 보완 문구를 만든다."""
    result = it.get("result")
    if result not in ("NOT_MET", "UNKNOWN"):
        return None

    codes = it.get("reason_codes") or []
    code = codes[0] if codes else ""
    kind, action = _REC_ACTION.get(
        code,
        ("수정 필요", "확인된 미흡 사항을 해당 문항 기준에 맞게 수정하고 조치 후 상태를 증적으로 남기세요.")
        if result == "NOT_MET"
        else ("추가 확인 필요", "판단에 필요한 대상·기간·적용 기준과 직접 근거를 추가 확인하세요.")
    )

    spec = _load_checklist_item_index().get(it.get("item_id"), {})
    question = spec.get("question") or spec.get("source_clause") or ""
    rule = spec.get("evidence_rule") or {}
    candidates = []
    for x in rule.get("candidate_evidence") or []:
        x = str(x).strip()
        if x and x not in candidates:
            candidates.append(x)
        if len(candidates) >= 3:
            break

    return {
        "item_id": it.get("item_id", ""),
        "kind": kind,
        "question": question,
        "action": action,
        "evidence": candidates,
    }


def _recommendation_block(detail: dict, e) -> str:
    """통제항목별로 보완/수정/추가확인 안내 박스를 만든다."""
    recs = []
    for it in detail.get("items") or []:
        rec = _recommendation_for_item(it)
        if rec:
            recs.append(rec)
    if not recs:
        return ""

    wbs = detail.get("overall_result_wbs")
    overall = detail.get("overall_result")
    if wbs == "부분적정" or overall == "충족(보완 권고)":
        title = "보완 권고"
    elif wbs == "부적정" or overall == "미충족":
        title = "수정 필요"
    else:
        title = "추가 확인 필요"

    out = ["<div class='recbox'>", f"<div class='rec-title'>{e(title)}</div>"]
    for r in recs:
        out.append("<div class='rec-item'>")
        out.append(f"<b>{e(r['item_id'])}</b> "
                   f"<span class='rec-tag'>{e(r['kind'])}</span>")
        if r["question"]:
            out.append(f"<div class='rec-question'>{e(r['question'])}</div>")
        out.append(f"<div class='rec-action'>{e(r['action'])}</div>")
        if r["evidence"]:
            out.append("<div class='rec-evidence'><b>권장 확인자료</b> · "
                       + " / ".join(e(x) for x in r["evidence"])
                       + "</div>")
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


# ── 인쇄용 HTML ───────────────────────────────────────────────────────
_CSS = """
body{font:14px/1.7 'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:32px;color:#1a1a1a}
h1{font-size:22px;margin:0 0 4px} .ai-note{color:#777;font-size:11px;margin:0 0 5px 0} .sub{color:#666;font-size:13px;margin-bottom:24px}
/* PDF 변환기(xhtml2pdf)가 CSS grid를 제대로 처리하지 못하므로
   요약 카드는 표 기반으로 렌더링한다. HTML/PDF 모두 동일하게 보인다. */
table.sum-grid{width:100%;border-collapse:separate;border-spacing:3px;margin:0 auto 4px auto}
table.sum-grid td.sum-card{border:1px solid #ddd;background:#fafafa;padding:6px 8px;
 text-align:center;vertical-align:middle;height:40px}
table.sum-grid .sum-label{color:#333;font-size:15px;font-weight:700;line-height:1.1}
table.sum-grid .sum-value{color:#555;font-size:11px;font-weight:600;line-height:1.1;margin-top:2px}

.ctl{border:1px solid #ddd;margin-bottom:20px;page-break-inside:avoid}
.ctl>h2{font-size:16px;margin:0;padding:10px 14px;background:#f2f4f7;border-bottom:1px solid #ddd}
.meta{padding:8px 14px;font-size:13px;color:#444;border-bottom:1px solid #eee}
.badge{display:inline-block;padding:1px 8px;border:1px solid #bbb;border-radius:10px;
 font-size:12px;margin-right:6px}
.v-충족{background:#e8f5e9;border-color:#a5d6a7}
.v-충족보완{background:#fff8e1;border-color:#ffe082}
.v-미충족{background:#ffebee;border-color:#ef9a9a}
.v-확인{background:#eceff1;border-color:#b0bec5}
.item{padding:10px 14px;border-top:1px solid #f0f0f0}
.item .id{font-weight:700}
.reason{color:#444;margin:4px 0}
blockquote{margin:6px 0 6px 12px;padding:6px 12px;border-left:3px solid #ccc;
 background:#fcfcfc;color:#222;white-space:pre-wrap}
blockquote .src{display:block;color:#888;font-size:12px;margin-top:4px}
.none{color:#999;font-style:italic}
.blockers{color:#b71c1c;font-size:12px;padding:6px 14px;background:#fff5f5}
.recbox{margin:8px 14px 10px;border:1px solid #e0c46c;background:#fffdf5;padding:8px 10px}
.rec-title{font-weight:700;font-size:13px;margin-bottom:4px;color:#6d5200}
.rec-item{padding:6px 0;border-top:1px solid #f0e4b8}
.rec-item:first-child{border-top:0}
.rec-tag{font-size:11px;color:#805f00;margin-left:5px}
.rec-question{font-size:12px;color:#555;margin-top:2px}
.rec-action{font-size:12px;color:#222;margin-top:2px}
.rec-evidence{font-size:11px;color:#666;margin-top:3px}
table.cov{border-collapse:collapse;width:100%;font-size:13px}
table.cov th,table.cov td{border:1px solid #ddd;padding:5px 8px;text-align:left}
table.cov th{background:#f2f4f7;font-weight:600}
table.cov td.n{text-align:right;width:62px}
table.cov th.c1,table.cov td.c1{width:72px;white-space:nowrap}
table.cov th.c3,table.cov td.c3{width:150px}
table.cov tr.sec td{background:#f7f8fa;font-weight:700;color:#555}
table.cov tr.zero td{color:#b71c1c}
.miss{padding:10px 14px;font-size:13px;line-height:1.9}
.miss .one{display:inline-block;margin:0 14px 2px 0}
.miss .cid{font-weight:700;margin-right:4px}
.secttl{margin:14px 14px 2px;font-weight:700;color:#555;font-size:13px}
@media print{body{margin:10mm} .ctl{border-color:#999}}
"""

_VCLASS = {"충족": "v-충족", "충족(보완 권고)": "v-충족보완", "미충족": "v-미충족",
           "확인 필요": "v-확인", "증적 없음": "v-확인"}


def to_html(path, controls_path=None, title="ISMS-P 증적 사전점검 — 항목별 근거 리포트",
            control_id=None, evidence_id=None):
    """
    control_id·evidence_id 를 주면 그것만 담는다.
    요약·미제출·제외는 전체 기준이므로 거를 때는 빼고, 제목에 범위를 적는다.
    """
    e = html.escape
    whole = control_id is None and evidence_id is None
    if control_id:
        rows = store.by_control(control_id)
        title += f" — {control_id}"
    elif evidence_id:
        rows = store.by_evidence(evidence_id)
        title += f" — {evidence_id}"
    else:
        rows = store.all_results()
    cov = store.coverage(controls_path)
    details = [store._one(r) for r in rows]
    if whole:
        s = store.summary()
    else:
        # 거를 때 전체 집계를 쓰면 상세는 2.5.1 하나인데 위 요약은 전체가 뜬다.
        # 이 범위만 다시 센다.
        s = {"judged_result_total": len(details),
             "judged_evidence_total": len({d["evidence_id"] for d in details}),
             "verdicts_wbs": {k: sum(1 for d in details
                                     if d["overall_result_wbs"] == k)
                              for k in ("적정", "부분적정", "부적정", "판단불가")},
             "review_required": sum(1 for d in details
                                    if d["review_state"] == "PENDING"),
             "excluded_total": 0,
             "citation_total": sum(len(it.get("citations") or [])
                                   for d in details for it in (d["items"] or []))}

    by_control = {}
    for d in details:
        by_control.setdefault(d["control_id"], []).append(d)

    out = [f"<!doctype html><html lang='ko'><meta charset='utf-8'>",
           f"<title>{e(title)}</title><style>{_CSS}</style>",
           "<div class='ai-note'>본 리포트는 AI 분석 결과를 바탕으로 작성되었습니다.</div>",
           f"<h1>{e(title)}</h1>"]
    if whole:
        out.append(f"<div class='sub'>통제항목 {cov['control_total']}개 중 제출 "
                   f"{cov['submitted']}개 · 판정 {s['judged_result_total']}건 · "
                   f"증적 {s['judged_evidence_total']}건</div>")
    else:
        out.append(f"<div class='sub'>이 범위의 판정 {len(details)}건 · "
                   f"증적 {s['judged_evidence_total']}건</div>")

    # xhtml2pdf에서 위/아래 요약 줄의 전체 폭을 정확히 맞추기 위해
    # 각 줄을 별도의 table로 렌더링한다. 두 table은 같은 width를 사용하고,
    # 첫 줄은 4등분, 둘째 줄은 남은 2~3개를 같은 폭으로 나눈다.
    # 따라서 개별 카드 폭은 달라도 위/아래 줄의 '합친 폭'은 항상 동일하다.
    summary_cards = [
        ("적정", s["verdicts_wbs"]["적정"]),
        ("부분적정", s["verdicts_wbs"]["부분적정"]),
        ("부적정", s["verdicts_wbs"]["부적정"]),
        ("판단불가", s["verdicts_wbs"]["판단불가"]),
        ("검토 대기", s["review_required"]),
    ]
    if whole:
        summary_cards.append(("제외", s["excluded_total"]))
    summary_cards.append(("근거", s["citation_total"]))

    def add_summary_matrix(cards):
        rows = [cards[:4], cards[4:]]
        for row in rows:
            if not row:
                continue
            out.append("<table class='sum-grid' width='100%' align='center' cellspacing='3' cellpadding='0'>")
            out.append("<tr>")
            cell_width = 100.0 / len(row)
            for label, value in row:
                out.append(
                    f"<td class='sum-card' width='{cell_width:.4f}%'>"
                    f"<div class='sum-label'>{e(label)}</div>"
                    f"<div class='sum-value'>{value}건</div>"
                    "</td>"
                )
            out.append("</tr></table>")

    add_summary_matrix(summary_cards)

    # 커버리지를 먼저 보여준다. 읽는 사람이 제일 먼저 묻는 것이
    # '어디가 비었나' 이기 때문이다. 범위를 걸러 뽑을 때는 분모가 달라지므로 뺀다.
    if whole:
        out += _coverage_block(cov, e)
        out += _missing_block(cov, e)

    for cid in sorted(by_control, key=store._sort_key):
        for d in by_control[cid]:
            cls = _VCLASS.get(d["overall_result"], "")
            out.append("<div class='ctl'>")
            out.append(f"<h2>{e(cid)} {e(d['control_name'] or '')}</h2>")
            out.append("<div class='meta'>"
                       f"<span class='badge {cls}'>{e(d['overall_result'])}"
                       f" / {e(d['overall_result_wbs'])}</span>"
                       f"<span class='badge'>{e(d['evidence_id'])} v{d['version']}</span>"
                       f"<span class='badge'>{e(d['review_state'])}</span>"
                       + ("<span class='badge'>사람 수정</span>"
                          if d["modified_by_human"] else "")
                       + "</div>")
            if d["approval_blockers"]:
                out.append("<div class='blockers'>승인 보류 — " +
                           " / ".join(e(b["message"]) for b in d["approval_blockers"])
                           + "</div>")
            rec_html = _recommendation_block(d, e)
            if rec_html:
                out.append(rec_html)
            for it in d["items"] or []:
                out.append("<div class='item'>")
                out.append(f"<span class='id'>{e(it.get('item_id',''))}</span> "
                           f"<span class='badge'>{e(it.get('result',''))}</span>"
                           + ("<span class='badge'>중요</span>" if it.get("critical") else "")
                           + (f"<span class='badge'>{e(','.join(it.get('reason_codes') or []))}</span>"
                              if it.get("reason_codes") else ""))
                if it.get("reason"):
                    out.append(f"<div class='reason'>{e(it['reason'])}</div>")
                cits = it.get("citations") or []
                if cits:
                    for c in cits:
                        src = e(c.get("chunk_id", ""))
                        if c.get("page") is not None:
                            src += f" · {c['page']}쪽"
                        if c.get("source"):
                            src += f" · {e(c['source'])}"
                        out.append(f"<blockquote>{e(c.get('quote',''))}"
                                   f"<span class='src'>{src}</span></blockquote>")
                else:
                    out.append("<div class='none'>근거 인용 없음</div>")
                out.append("</div>")
            out.append("</div>")

    # 미제출 목록은 위 커버리지 장으로 옮겼다 (번호만 나열하면 읽히지 않는다).
    ex = store.excluded() if whole else [
        x for x in store.excluded()
        if (control_id and x["control_id"] == control_id)
        or (evidence_id and x["evidence_id"] == evidence_id)]
    if ex:
        out.append(f"<div class='ctl'><h2>제외 {len(ex)}건</h2>")
        for x in ex:
            out.append(f"<div class='item'>{e(x['evidence_id'])}"
                       f"{' v' + str(x.get('evidence_version','')) if x.get('evidence_version') else ''}"
                       f"{' · ' + e(x['control_id']) if x['control_id'] else ' · 증적 전체'}"
                       f" — <b>{e(x['reason_code'])}</b> {e(x.get('reason') or '')}</div>")
        out.append("</div>")
    out.append("</html>")

    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out), encoding="utf-8")
    return p



# ── PDF ───────────────────────────────────────────────────────────────
# 폐쇄망이라 브라우저(Chromium)나 GTK 같은 시스템 의존이 있는 변환기는 못 쓴다.
# xhtml2pdf 는 순수 파이썬(reportlab)이라 pip 하나로 끝나고 DLL 을 안 깐다.
#     pip install xhtml2pdf
# 인터넷이 막힌 PC 라면 미리 받아 두었다가 넣는다:
#     (망 되는 PC) pip download xhtml2pdf -d wheels
#     (망 막힌 PC) pip install --no-index --find-links wheels xhtml2pdf
#
# 한글 글꼴은 저장소에 이미 있는 것을 쓴다 (IS_SO_UI/dist/fonts). 글꼴을 안
# 심으면 한글이 전부 네모로 나온다 — reportlab 기본 글꼴에 한글이 없다.

_FONT_DIRS = ["IS_SO_UI/dist/fonts"]
_FONT_FILES = ("GmarketSansTTFMedium.ttf", "MalgunGothic.ttf", "malgun.ttf",
               "NanumGothic.ttf")


def _repo_root():
    here = Path(__file__).resolve().parent
    for base in [here, *here.parents]:
        if (base / "IS_SO_UI").is_dir() or (base / "phase1_입력").is_dir():
            return base
    return here.parent


def find_font():
    """한글 TTF 하나를 찾는다. 저장소 안 → 윈도우 글꼴 폴더 순."""
    root = _repo_root()
    for d in _FONT_DIRS:
        for name in _FONT_FILES:
            f = root / d / name
            if f.is_file():
                return f
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("malgun.ttf", "NanumGothic.ttf", "gulim.ttc"):
        f = win / name
        if f.is_file() and f.suffix.lower() == ".ttf":
            return f
    return None


_PDF_CSS = """
@page {{ size: A4 portrait; margin: 14mm 12mm; }}
@font-face {{ font-family: ko; src: url("{font}"); }}
body {{ font-family: ko; font-size: 9pt; margin: 0; line-height: 1.5; }}
h1 {{ font-size: 15pt; }}
.ctl>h2 {{ font-size: 11pt; }}
/* 요약 영역은 HTML 단계부터 표 기반이라 xhtml2pdf에서도 겹치지 않는다. */
table.sum-grid {{ width:100%; margin:0 auto 1.2mm auto; }}
table.sum-grid td.sum-card {{ height:12mm; padding:1.0mm 1.2mm; text-align:center; vertical-align:middle; }}
table.sum-grid .sum-label {{ font-size:10.5pt; font-weight:bold; color:#333333; line-height:1.05; }}
table.sum-grid .sum-value {{ font-size:8pt; font-weight:normal; color:#555555; margin-top:0.6mm; line-height:1.05; }}
table.cov {{ font-size: 8.5pt; }}
blockquote {{ font-size: 8.5pt; }}
"""


def to_pdf(path, controls_path=None, control_id=None, evidence_id=None,
           title="ISMS-P 증적 사전점검 — 항목별 근거 리포트", html_text=None):
    """
    같은 내용의 PDF. HTML 을 먼저 만들고 그걸 변환한다 — 레이아웃이 한 벌이라
    둘이 달라질 일이 없다.

    xhtml2pdf 가 없으면 ReportError 를 낸다. 화면 쪽에서 그 메시지를 그대로
    보여주면 된다 (조용히 빈 PDF 를 주는 것보다 낫다).
    """
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:
        raise ReportError(
            "PDF_BACKEND_MISSING",
            "PDF 를 만들려면 xhtml2pdf 가 필요합니다.  pip install xhtml2pdf"
        ) from exc

    font = find_font()
    if font is None:
        raise ReportError(
            "PDF_FONT_MISSING",
            "한글 글꼴을 못 찾았습니다. IS_SO_UI/dist/fonts 안에 "
            "GmarketSansTTFMedium.ttf 가 있어야 합니다.")

    if html_text is None:
        tmp = Path(path).with_suffix(".html")
        to_html(tmp, controls_path, title=title,
                control_id=control_id, evidence_id=evidence_id)
        html_text = tmp.read_text(encoding="utf-8")

    css = _PDF_CSS.format(font=font.as_posix())
    html_text = html_text.replace("</style>", "</style><style>" + css + "</style>", 1)

    # 글꼴이 저장소 안 다른 폴더에 있어서 기본 정책에 막힌다. 그 폴더만 열어 준다.
    # 바깥 망은 닫아 둔다 — 리포트가 인터넷을 볼 이유가 없다.
    kw = {}
    try:
        from xhtml2pdf.files import ResourceAccessPolicy
        kw["resource_policy"] = ResourceAccessPolicy(
            allow_remote=False, extra_roots=(font.parent,))
    except Exception:                                         # noqa: BLE001
        # 낡은 xhtml2pdf. 글꼴을 결과물 옆에 복사해 두고 상대경로로 쓴다.
        import shutil
        side = Path(path).parent / font.name
        side.parent.mkdir(parents=True, exist_ok=True)
        if not side.is_file():
            shutil.copy2(font, side)
        html_text = html_text.replace(font.as_posix(), font.name)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        res = pisa.CreatePDF(html_text, dest=f, encoding="utf-8", **kw)
    if res.err:
        raise ReportError("PDF_RENDER_FAILED",
                          f"PDF 변환 중 오류 {res.err}건이 났습니다.")
    return p


def to_pdf_bytes(**kw):
    """API 가 바로 내려보낼 수 있게 바이트로 돌려준다."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = to_pdf(Path(d) / "report.pdf", **kw)
        return p.read_bytes()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="항목별 근거 리포트 출력")
    ap.add_argument("--out", default="reports", help="내보낼 폴더")
    ap.add_argument("--controls", help="phase1_검색/controls.json 경로")
    ap.add_argument("--control-id", help="이 통제항목만")
    ap.add_argument("--evidence-id", help="이 증적만")
    ap.add_argument("--no-pdf", action="store_true", help="PDF 는 안 만든다")
    a = ap.parse_args(argv)

    out = Path(a.out)
    rows = report_rows(a.control_id, a.evidence_id)
    print("CSV  ", to_csv(out / "control_report.csv", rows), f"({len(rows)}줄)")
    print("HTML ", to_html(out / "control_report.html", a.controls,
                            control_id=a.control_id, evidence_id=a.evidence_id))
    p, cov = coverage_csv(out / "coverage.csv", a.controls)
    print("커버리지", p, f"(제출 {cov['submitted']} / {cov['control_total']})")
    if not a.no_pdf:
        try:
            print("PDF  ", to_pdf(out / "control_report.pdf", a.controls,
                                  control_id=a.control_id,
                                  evidence_id=a.evidence_id))
        except ReportError as exc:
            print(f"PDF   건너뜀 — {exc.code}: {exc.message}")
    text, r = reconcile_text(out / "reconcile.txt", a.controls)
    print(text)
    return 1 if r["실패"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
