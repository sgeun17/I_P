"""명시적 선택 실행: 초안 정답을 고정한 뒤 실제 로컬 모델로 개발 샘플을 평가합니다."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jsonschema import Draft202012Validator
from chunk_retriever import LocalDocumentRetriever


def payload(case):
    eid = f"review_{case['id']}"
    document = {"evidence_id": eid, "version": 1, "source_file": f"{eid}.txt", "file_type": "txt"}
    document["chunks"] = [{**document, "chunk_id": f"{eid}_v1_c{i:04d}", "chunk_index": i,
                           "source": "parser", "chunk_type": "text", "page_start": None, "page_end": None,
                           "heading": None, "text": text, "block_orders": [i+1]} for i, text in enumerate(case["texts"])]
    return document


def main():
    raw = (ROOT / "tests/review_cases.json").read_bytes()
    dataset = json.loads(raw)
    dataset_sha = hashlib.sha256(raw).hexdigest()
    validator = Draft202012Validator(json.loads((ROOT / "schemas/chunk_output.schema.json").read_text(encoding="utf-8")))
    input_validator = Draft202012Validator(json.loads((ROOT / "schemas/chunk_input.schema.json").read_text(encoding="utf-8")))
    engine = LocalDocumentRetriever()
    controls = {c["control_id"] for c in engine.controls}
    rows, outputs = [], []
    for case in dataset["cases"]:
        expected = set(case["expected"])
        assert expected <= controls, case
        assert case["kind"] not in ("single", "multiple") or expected, case
        document = payload(case)
        input_validator.validate(document)
        result = engine.search(document)
        validator.validate(result)
        candidates = result["retrieval"]["candidates"]
        ids = [c["control_id"] for c in candidates]
        hit = sorted(expected & set(ids))
        row = {"id": case["id"], "kind": case["kind"], "expected": case["expected"],
               "top5": ids, "top1_score": candidates[0]["similarity_score"],
               "matched_expected": hit, "missing_expected": sorted(expected - set(ids)),
               "recall_at_5": len(hit)/len(expected) if expected else None,
               "top1_hit": ids[0] in expected if expected else None}
        rows.append(row)
        outputs.append({"case_id": case["id"], "output": result})
        print(f"{case['id']}: {ids}; expected={case['expected']}", flush=True)
    assert hashlib.sha256((ROOT / "tests/review_cases.json").read_bytes()).hexdigest() == dataset_sha
    labeled = [r for r in rows if r["expected"]]
    singles = [r for r in rows if r["kind"] == "single"]
    unrelated = [r for r in rows if r["kind"] == "unrelated"]
    metrics = {"labeled_documents": len(labeled), "all_expected_in_top5": sum(not r["missing_expected"] for r in labeled),
               "macro_recall_at_5": sum(r["recall_at_5"] for r in labeled)/len(labeled),
               "single_top1_hits": sum(r["top1_hit"] for r in singles), "single_documents": len(singles),
               "unrelated_documents": len(unrelated), "unrelated_returned_candidates": sum(bool(r["top5"]) for r in unrelated)}
    report = {"checked_at": datetime.now().astimezone().isoformat(), "execution_status": "PASS",
              "label_status": dataset["label_status"], "dataset_sha256": dataset_sha,
              "kb_sha256": engine.kb_sha, "schema_version": outputs[0]["output"]["schema_version"],
              "metrics": metrics, "cases": rows,
              "limitations": ["합성 문장 20건의 개발 점검. 독립 정답 검수·실제 운영 품질 평가 전.",
                              "무관·애매한 문서는 정답 재현율 계산에서 제외. 무관 문서도 K개를 반환하므로 판단팀에서 관련성을 판단해야 함.",
                              "이 점수 분포만으로 임계값을 결정하지 않음. 예상 정답 누락은 결과에 그대로 기록."]}
    out = ROOT / "reports"
    (out / "review_evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "review_case_outputs.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 검색 검토용 샘플 실행 결과", "", f"- 실행: {report['checked_at']} / 실제 BGE-M3 + 101개 ChromaDB",
             f"- 데이터: {dataset['label_status']}", f"- 입력 고정 SHA-256: {dataset_sha}",
             f"- 정답 포함 문서 {len(labeled)}건 중 모든 예상 정답이 Top-5에 포함: {metrics['all_expected_in_top5']}건",
             f"- 문서별 정답 Recall@5 평균: {metrics['macro_recall_at_5']:.1%}",
             f"- 단일 정답 Top-1: {metrics['single_top1_hits']}/{len(singles)}",
             f"- 무관 문서 후보 반환: {metrics['unrelated_returned_candidates']}/{len(unrelated)} (현 규격상 항상 반환)", "",
             "| 샘플 | 유형 | 예상 ID | 실제 Top-5 | 누락 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['kind']} | {', '.join(r['expected']) or '확정하지 않음'} | {', '.join(r['top5'])} | {', '.join(r['missing_expected']) or '-'} |")
    lines += ["", "## 해석과 다음 검수", "", *[f"- {s}" for s in report['limitations']],
              "- 검토자는 tests/review_cases.json의 본문·예상 ID·rationale를 먼저 확인하고 정답을 승인 또는 수정해 주세요.",
              "- 새 실제 문서는 이 개발 세트와 분리해 평가하고, 최종 정답 승인 후 KB 변경 전후 결과를 비교하세요.",
              "- 자동 생성 정답을 검색 결과에 맞춰 변경하지 않았습니다. 입력 전체와 후보별 근거 청크는 review_case_outputs.json에 있습니다."]
    (out / "review_evaluation.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
