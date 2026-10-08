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
import sys
from pathlib import Path

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


# ── 인쇄용 HTML ───────────────────────────────────────────────────────
_CSS = """
body{font:14px/1.7 'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:32px;color:#1a1a1a}
h1{font-size:22px;margin:0 0 4px} .sub{color:#666;font-size:13px;margin-bottom:24px}
.sum{border:1px solid #ddd;padding:14px 16px;margin-bottom:28px;background:#fafafa;
 display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:6px 18px}
.sum div b{color:#555;font-weight:600;margin-right:8px}
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
           f"<h1>{e(title)}</h1>"]
    if whole:
        out.append(f"<div class='sub'>통제항목 {cov['control_total']}개 중 제출 "
                   f"{cov['submitted']}개 · 판정 {s['judged_result_total']}건 · "
                   f"증적 {s['judged_evidence_total']}건</div>")
    else:
        out.append(f"<div class='sub'>이 범위의 판정 {len(details)}건 · "
                   f"증적 {s['judged_evidence_total']}건</div>")

    out.append("<div class='sum'>")
    for k in ("적정", "부분적정", "부적정", "판단불가"):
        out.append(f"<div><b>{k}</b>{s['verdicts_wbs'][k]}건</div>")
    out.append(f"<div><b>검토 대기</b>{s['review_required']}건</div>")
    if whole:
        out.append(f"<div><b>제외</b>{s['excluded_total']}건</div>")
    out.append(f"<div><b>근거</b>{s['citation_total']}건</div>")
    out.append("</div>")

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

    miss = cov["not_submitted_controls"] if whole else []
    if miss:
        out.append(f"<div class='ctl'><h2>미제출 통제항목 {len(miss)}개</h2>"
                   "<div class='meta'>" +
                   ", ".join(e(m["control_id"]) for m in miss) + "</div></div>")
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


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="항목별 근거 리포트 출력")
    ap.add_argument("--out", default="reports", help="내보낼 폴더")
    ap.add_argument("--controls", help="phase1_검색/controls.json 경로")
    ap.add_argument("--control-id", help="이 통제항목만")
    ap.add_argument("--evidence-id", help="이 증적만")
    a = ap.parse_args(argv)

    out = Path(a.out)
    rows = report_rows(a.control_id, a.evidence_id)
    print("CSV  ", to_csv(out / "control_report.csv", rows), f"({len(rows)}줄)")
    print("HTML ", to_html(out / "control_report.html", a.controls,
                            control_id=a.control_id, evidence_id=a.evidence_id))
    p, cov = coverage_csv(out / "coverage.csv", a.controls)
    print("커버리지", p, f"(제출 {cov['submitted']} / {cov['control_total']})")
    text, r = reconcile_text(out / "reconcile.txt", a.controls)
    print(text)
    return 1 if r["실패"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
