"""검색 점수의 관련/무관 구분 가능성과 현재 판단팀 NO_MATCH 정책을 점검합니다.
임계값은 분석용이며 검색 결과나 운영 정책을 변경하지 않습니다.
"""
from datetime import datetime
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/relevance_2026-09-25"
JUDGMENT = ROOT.parent / "판단"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(JUDGMENT / "src"), str(ROOT / "tests")]
from chunk_retriever import LocalDocumentRetriever
from evaluate_review_cases import payload
from judgment_adapter import to_mapping_input
from models import MappingInput
from retrieval_adapter import mapping_input_from_retriever
from prompts import build_prompt_package
from versions import build_version_info
from service import build_result
from enums import ErrorCode, ReviewReason
from review_policy import DEFAULT_THRESHOLDS


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    before = {str(p.relative_to(JUDGMENT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in JUDGMENT.rglob("*") if p.is_file() and not any(s in p.parts for s in ("__pycache__", ".pytest_cache"))}
    source = ROOT / "tests/relevance_cases.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cases = json.loads(source.read_text(encoding="utf-8"))["cases"]
    engine = LocalDocumentRetriever()
    results = {}
    rows = []
    # 기존 20건은 재현 조건이 같은 저장된 실측값을 사용하며 오늘 재검색한 것으로 표시하지 않습니다.
    labels = {c["id"]: c for c in json.loads((ROOT / "tests/review_cases.json").read_text(encoding="utf-8"))["cases"]}
    previous = json.loads((ROOT / "reports/review_case_outputs.json").read_text(encoding="utf-8"))
    records = []
    for record in previous:
        result = record["output"]
        assert result["index"]["kb_sha256"] == engine.kb_sha
        assert result["index"]["embedding_cache_key"] == engine.cache_key
        case = labels[record["case_id"]]
        records.append((case, result, "2026-09-23 저장된 검색 결과"))
    for case in cases:
        result = engine.search(payload(case))
        records.append((case, result, "2026-09-25 실제 추가 검색"))
    for case, result, origin in records:
        candidates = result["retrieval"]["candidates"]
        kind = "related" if case["expected"] else case["kind"]
        ids = [c["control_id"] for c in candidates]
        rows.append({"id": case["id"], "kind": kind, "origin": origin,
                     "expected": case["expected"], "top5": ids,
                     "top1": candidates[0]["similarity_score"],
                     "gap": round(candidates[0]["similarity_score"] - candidates[1]["similarity_score"], 6),
                     "missing_expected": sorted(set(case["expected"])-set(ids))})
        # 판단팀 새 어댑터와 검색팀 어댑터의 결과를 실제 모델 기본값까지 비교합니다.
        model = mapping_input_from_retriever(result)
        assert model.model_dump() == MappingInput.model_validate(to_mapping_input(result)).model_dump()
        package = build_prompt_package(model)
        results[case["id"]] = result
        if case["id"] in ("R01", "N01", "N04", "B01"):
            save(case["id"] + "_llm_request.json", {"llm_called": False, "prompt_version": package.prompt_version,
                  "ruleset_version": package.ruleset_version, "messages": package.openai_compatible_messages(),
                  "output_schema": package.output_schema})
    groups = {k: [r for r in rows if r["kind"] == k] for k in ("related", "unrelated", "ambiguous")}
    ranges = {k: {"n": len(rs), "min_top1": min(r["top1"] for r in rs), "max_top1": max(r["top1"] for r in rs)} for k, rs in groups.items()}
    # 사전에 지정한 값만 비교하며 최적 임계값으로 채택하지 않습니다.
    threshold_trials = []
    for cutoff in (0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
        threshold_trials.append({"cutoff": cutoff, "rule": "top1 < cutoff이면 후보 없음으로 처리하는 가정",
            "related_wrongly_removed": [r["id"] for r in groups["related"] if r["top1"] < cutoff],
            "unrelated_still_retained": [r["id"] for r in groups["unrelated"] if r["top1"] >= cutoff],
            "ambiguous_removed_observation_only": [r["id"] for r in groups["ambiguous"] if r["top1"] < cutoff]})
    policies = []
    for name, case_id, decision, failure in [("no_match", "U01", "NOT_RELATED", False),
                 ("uncertain", "B01", "UNCERTAIN", False), ("high_score_no_match", "S01", "NOT_RELATED", False),
                 ("call_failure", "U01", "NOT_RELATED", True)]:
        mapping = mapping_input_from_retriever(results[case_id])
        fixed = {"match_status": "NO_MATCH", "mapped_controls": [], "candidate_decisions": [
            {"control_id": c.control_id, "decision": decision, "llm_confidence": 0.8,
             "reason": "정책 분기 검사에 사용하는 고정 응답이며 실제 모델 판단이 아닙니다.", "citations": []}
            for c in mapping.candidate_controls]}
        versions = build_version_info(mapping, model_name="NO_LLM_POLICY_TEST")
        result = build_result(None if failure else json.dumps(fixed, ensure_ascii=False), mapping, versions,
                              call_error=ErrorCode.LLM_RETRY_EXHAUSTED if failure else None)
        reasons = result.human_review.reasons
        assert result.human_review.required
        expected = {"no_match": ReviewReason.NO_MATCH_RESULT, "uncertain": ReviewReason.UNCERTAIN_DECISION,
                    "high_score_no_match": ReviewReason.RETRIEVER_LLM_CONFLICT}.get(name)
        if expected:
            assert expected in reasons
            assert result.validation.passed
        if failure:
            assert result.processing_status.value == "FAILED"
        policies.append({"name": name, "status": result.processing_status.value,
                         "match_status": result.match_status.value, "review_reasons": [r.value for r in reasons]})
        save(name + "_fixed_policy_test.json", {"llm_called": False, "fixed_response": fixed if not failure else None,
                                                "result": result.model_dump(mode="json")})
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    after = {str(p.relative_to(JUDGMENT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in JUDGMENT.rglob("*") if p.is_file() and not any(s in p.parts for s in ("__pycache__", ".pytest_cache"))}
    assert before == after, "검사 중 판단팀 파일 변경됨"
    save("additional_search_outputs.json", {c["id"]: results[c["id"]] for c in cases})
    report = {"checked_at": datetime.now().astimezone().isoformat(), "status": "PASS", "kb_sha256": engine.kb_sha,
              "embedding_cache_key": engine.cache_key, "new_cases_sha256": digest, "label_status": "합성 사례·미검수 정답 초안, 배포 임계값 보정용 독립 평가 아님",
              "ranges": ranges, "cases": rows, "threshold_trials": threshold_trials, "current_policy": asdict(DEFAULT_THRESHOLDS),
              "policy_tests": policies, "adapters_equivalent_cases": len(rows), "judgment_files_unchanged": len(before),
              "production_filter_enabled": False, "llm_called": False}
    save("summary.json", report)
    lines = ["# 관련 없음 처리 검증", "", f"- 실행: {report['checked_at']}",
             "- 기존 실측 20건 + 새 경계 사례 검색 10건. 모든 라벨은 합성 사례의 검수 전 초안.",
             "- 관련 19건 / 무관 7건 / 애매 4건. 애매 사례는 정답 지표에서 제외.",
             "- 원본 Top-5 유지. 아래 점수 기준은 분석용이며 운영 필터를 적용하지 않음.", "",
             "| 분류 | 건수 | Top-1 최솟값 | Top-1 최댓값 |", "|---|---:|---:|---:|"]
    for kind, r in ranges.items():
        lines.append(f"| {kind} | {r['n']} | {r['min_top1']:.6f} | {r['max_top1']:.6f} |")
    lines += ["", "| 가상 제외 기준 | 관련 문서 잘못 제외 / 19 | 무관 문서 여전히 통과 / 7 |", "|---|---|---|"]
    for trial in threshold_trials:
        lines.append(f"| Top-1 < {trial['cutoff']} | {len(trial['related_wrongly_removed'])}: {', '.join(trial['related_wrongly_removed']) or '-'} | {len(trial['unrelated_still_retained'])}: {', '.join(trial['unrelated_still_retained']) or '-'} |")
    lines += ["", "## 확인한 현재 정책", "", "- 판단팀 프롬프트에는 후보가 검색됐다는 사실만으로 RELATED를 선택하지 않도록 명시됨.",
             "- NO_MATCH는 R204로 사람 검토. UNCERTAIN은 R107로 별도 검토. 호출 실패의 NO_MATCH는 정상적인 관련 없음과 구분.",
             f"- 검색 고득점과 NO_MATCH가 충돌하면 {ReviewReason.RETRIEVER_LLM_CONFLICT.value} 검토. 위 동작은 고정 응답 4종으로 검사했으며 실제 LLM의 정확도 검사가 아님.",
             "- 검색팀·판단팀 어댑터 30건 결과 일치 및 새 프롬프트 생성 확인. 요청 예제 4건 저장, 외부 전송·LLM 호출 없음.",
             "- 최신 판단팀은 Python 코드를 설정 원본으로, YAML을 생성물로 정리함. 9/24 YAML 관련 확인 사항 해소.",
             "", "## 다음 단계", "", "검색팀: 후보 포함률과 무관 증적 점수 분포를 제공. 판단팀: 실제 LLM의 NO_MATCH 오탐·누락 및 UNCERTAIN 구분을 검증.",
             "합의 전 정책 제안: Top-5는 유지하고 판단 단계에서 NO_MATCH/검토로 처리. 향후 저점수 신호가 필요하면 독립 실제 증적으로 임계값을 보정한 뒤 별도 필드 규격을 합의.",
             "선택한 일부 기준에서 분리돼 보여도 작은 합성 세트이므로 일반화할 수 없음. 짧은 관련 증적을 잘못 버리는 비율도 함께 평가해야 함."]
    (OUT / "summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"ranges": ranges, "threshold_trials": threshold_trials, "policy_tests": policies}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
