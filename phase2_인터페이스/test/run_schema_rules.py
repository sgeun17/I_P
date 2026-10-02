"""
run_schema_rules.py : 스키마가 '틀린 모양' 을 실제로 거르는지 검사한다

    python run_schema_rules.py

JSON Schema 는 쓰기는 쉬운데 안 걸리는 경우가 많다.
대표적으로 `then: {properties: {...}}` 는 **그 키가 있을 때만** 검사하므로,
키를 아예 지운 입력은 그냥 통과한다. 그런 구멍을 통째로 막아두는 검사다.

이 스크립트는 저장소를 찾지 않는다 — 인터페이스 폴더만으로 돈다.
"""
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    sys.exit("jsonschema 가 없습니다.  pip install jsonschema")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

INS = json.loads((ROOT / "phase2_input.schema.json").read_text(encoding="utf-8"))
OUT = json.loads((ROOT / "phase2_output.schema.json").read_text(encoding="utf-8"))

fails = []


def ok(schema, data):
    return jsonschema.Draft202012Validator(schema).is_valid(data)


def want(label, schema, data, expect):
    got = ok(schema, data)
    mark = "[O]" if got == expect else "[X]"
    if got != expect:
        fails.append(label)
    print(f"  {mark} {label:52} {'통과' if got else '거부'}"
          f"{'' if got == expect else '  ← ' + ('거부해야 함' if got else '통과해야 함')}")


def item(**kw):
    base = {"item_id": "2.5.1-Q01", "result": "MET",
            "reason": "충분히 긴 판정 근거 문장입니다",
            "reason_codes": [],
            "citations": [{"chunk_id": "E0001_v1_c0000", "page": 1, "quote": "근거"}]}
    base.update(kw)
    return base


def target(**kw):
    base = {"control_id": "2.5.1", "control_name": "사용자 접근권한 검토",
            "relation": "PRIMARY", "judge": True, "checklist_in_scope": True}
    base.update(kw)
    return base


def inp(**kw):
    base = {"schema_version": INS["properties"]["schema_version"]["const"],
            "evidence_id": "E0001", "version": 1, "judge_policy": "primary_only",
            "targets": [target()],
            "chunks": [{"chunk_id": "E0001_v1_c0000", "chunk_index": 0,
                        "chunk_type": "text", "text": "본문", "source": "parser"}],
            "source_versions": {"kb_sha256": "a" * 64}}
    base.update(kw)
    return base


ITEM = {**OUT["$defs"]["ItemResult"], "$defs": OUT["$defs"]}
HR = OUT["properties"]["human_review"]


def main():
    print("── ① 문항 결과 (ItemResult) ──")
    want("MET + 인용 1개", ITEM, item(), True)
    want("MET 인데 인용 0개", ITEM, item(citations=[]), False)
    want("NOT_MET + 사유코드 1개", ITEM,
         item(result="NOT_MET", reason_codes=["P2_NM_RULE_VIOLATED"]), True)
    want("NOT_MET 인데 사유코드 빈 배열", ITEM, item(result="NOT_MET"), False)
    want("NOT_MET 인데 사유코드 키가 아예 없음", ITEM,
         {k: v for k, v in item(result="NOT_MET").items() if k != "reason_codes"}, False)
    want("UNKNOWN 인데 사유코드 키가 아예 없음", ITEM,
         {k: v for k, v in item(result="UNKNOWN", citations=[]).items()
          if k != "reason_codes"}, False)
    want("MET 인데 사유코드가 붙음", ITEM,
         item(reason_codes=["P2_NM_RULE_VIOLATED"]), False)
    want("등록 안 된 사유코드", ITEM,
         item(result="UNKNOWN", citations=[], reason_codes=["P2_U_NOT_A_REAL_CODE"]), False)
    want("UNKNOWN + error_code", ITEM,
         item(result="UNKNOWN", citations=[],
              reason_codes=["P2_U_EVIDENCE_UNREADABLE"], error_code="E101"), True)
    want("MET 인데 error_code 가 붙음", ITEM, item(error_code="E101"), False)
    want("check_kind 가 null", ITEM, item(check_kind=None), False)

    print("\n── ② 사람 검토 (human_review) ──")
    want("검토 필요 + 사유 1개", HR, {"required": True, "reasons": ["P2R101"]}, True)
    want("검토 필요인데 사유 0개", HR, {"required": True, "reasons": []}, False)
    want("검토 필요인데 사유 키가 없음", HR, {"required": True}, False)
    want("검토 불필요 + 사유 0개", HR, {"required": False, "reasons": []}, True)
    want("검토 불필요인데 사유가 붙음", HR, {"required": False, "reasons": ["P2R101"]}, False)

    print("\n── ③ 입력 모양 (primary_only) ──")
    want("PRIMARY + judge=true", INS, inp(), True)
    want("RELATED + judge=false", INS,
         inp(targets=[target(relation="RELATED", judge=False)]), True)
    want("RELATED 인데 judge=true", INS,
         inp(targets=[target(relation="RELATED", judge=True)]), False)
    want("PRIMARY 인데 judge=false", INS,
         inp(targets=[target(judge=False)]), False)
    want("PRIMARY 가 2개", INS,
         inp(targets=[target(), target(control_id="2.5.6")]), False)
    want("PRIMARY 가 0개 (RELATED 만) — 정상", INS,
         inp(targets=[target(relation="RELATED", judge=False)]), True)
    want("judge_policy 가 모르는 값", INS, inp(judge_policy="whatever"), False)
    want("chunk_index 가 없음", INS,
         inp(chunks=[{"chunk_id": "E0001_v1_c0000", "chunk_type": "text",
                      "text": "본문", "source": "parser"}]), False)
    want("checklist_in_scope 가 없음", INS,
         inp(targets=[{k: v for k, v in target().items()
                       if k != "checklist_in_scope"}]), False)
    want("kb_sha256 가 빈 문자열", INS, inp(source_versions={"kb_sha256": ""}), False)
    want("kb_sha256 가 짧음", INS, inp(source_versions={"kb_sha256": "abc"}), False)

    print("\n── ④ 출력 전체 (증적 없음 묶기) ──")
    base = {"schema_version": OUT["properties"]["schema_version"]["const"],
            "evidence_id": "E0001", "version": 1, "control_id": "2.5.1",
            "rule_version": "overall-v0.1-checkkind",
            "critical_policy": {"mode": "check_kind",
                                "critical_kinds": ["procedure", "implementation"]},
            "human_review": {"required": False, "reasons": []}}
    want("문항 0개 + 증적 없음", OUT,
         {**base, "items": [], "overall_result": "증적 없음"}, True)
    want("문항 0개인데 충족", OUT,
         {**base, "items": [], "overall_result": "충족"}, False)
    want("문항 1개인데 증적 없음", OUT,
         {**base, "items": [item()], "overall_result": "증적 없음"}, False)

    print("\n── ⑤ 의미 검사 (overall_result.validate) ──")
    import overall_result as orule
    bad = {"items": [item(result="NOT_MET", check_kind="procedure",
                          reason_codes=["P2_NM_RULE_NOT_DEFINED"])],
           "overall_result": "충족", "critical_policy": {"mode": "check_kind"}}
    got = orule.validate(bad)
    print(f"  {'[O]' if 'P2E504' in got else '[X]'} NOT_MET 인데 충족 → {got}"
          f"   (스키마는 통과시키는 모순)")
    if "P2E504" not in got:
        fails.append("의미 검사")
    good = {**bad, "overall_result": orule.compute(bad["items"])["overall_result"]}
    got2 = orule.validate(good)
    print(f"  {'[O]' if not got2 else '[X]'} 규칙대로 → {got2 or '이상 없음'}"
          f"   ({good['overall_result']})")
    if got2:
        fails.append("의미 검사(정상)")

    print()
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        sys.exit(1)
    print("전부 통과.")


if __name__ == "__main__":
    main()
