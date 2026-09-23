"""원문 대조 기록에서 별도로 추출한 기준 목록과 현재 KB를 비교합니다."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    kb_bytes = (ROOT / "controls.json").read_bytes()
    controls = json.loads(kb_bytes)
    baseline = json.loads((ROOT / "tests/expected_controls.json").read_text(encoding="utf-8"))
    reference = {c["control_id"]: c["control_name"] for c in baseline["controls"]}
    assert len(reference) == 101, "독립 기준 목록은 101개여야 합니다."
    errors, warnings = [], []
    ids = [c.get("control_id") for c in controls]
    duplicates = [cid for cid, count in Counter(ids).items() if count > 1]
    missing = sorted(set(reference) - set(ids))
    extra = sorted(set(ids) - set(reference), key=str)
    if len(controls) != 101 or duplicates or missing or extra:
        errors.append({"count": len(controls), "duplicate_ids": duplicates, "missing_ids": missing, "extra_ids": extra})
    for c in controls:
        cid = c.get("control_id")
        for field in ("control_id", "control_name", "requirement"):
            if not isinstance(c.get(field), str) or not c[field].strip():
                errors.append({"control_id": cid, "field": field, "error": "필수 문자열 누락"})
        if cid in reference and c.get("control_name") != reference[cid]:
            errors.append({"control_id": cid, "error": "기준 목록 명칭과 불일치"})
        for field in ("keyword", "evidence_examples"):
            values = c.get(field)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                errors.append({"control_id": cid, "field": field, "error": "비어 있거나 잘못된 목록"})
            elif len(set(values)) != len(values):
                errors.append({"control_id": cid, "field": field, "error": "항목 내부 중복"})
    counts = {f: sum(len(c.get(f, [])) for c in controls) for f in ("keyword", "evidence_examples")}
    warnings.append(f"원문 대조 기록 작성 시 키워드 781개, 현재 {counts['keyword']}개. ID·명칭 대조에만 기록의 기준 목록을 사용했으며 최신 키워드의 의미 검수는 별도입니다.")
    report = {"status": "PASS" if not errors else "FAIL", "checked_at": datetime.now().astimezone().isoformat(),
              "kb_sha256": hashlib.sha256(kb_bytes).hexdigest(), "count": len(controls),
              "domains": dict(Counter(str(cid).split('.')[0] for cid in ids)),
              "duplicate_ids": duplicates, "missing_ids": missing, "extra_ids": extra,
              "field_counts": counts, "reference": baseline["reference"], "errors": errors, "warnings": warnings,
              "scope": "현재 KB 구조와 독립 원문 대조 기록의 ID·명칭 일치 검사. 원문 전체 수동 검수나 최종 KB 버전 고정은 아님."}
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "kb_validation_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# KB 데이터 검증", "", f"- 결과: **{report['status']}**", f"- 검사 일시: {report['checked_at']}",
             f"- 항목: {len(controls)}개. 중복 {len(duplicates)} / 누락 {len(missing)} / 추가 {len(extra)}",
             f"- 영역별: {report['domains']}", f"- 필드 합계: {counts}",
             "- ID·명칭 기준: 기존 controls_review.md의 원문 대조 목록을 별도 보존한 tests/expected_controls.json",
             "- 필수 필드, 빈 문자열·목록, 항목 내부 중복, 명칭 일치 검사", "", report['scope'], "",
             *[f"- {w}" for w in warnings], *[f"- 오류: {e}" for e in errors]]
    (out / "kb_validation_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
