"""대표 합성 9사례를 기존 판단팀 하네스와 로컬 LLM으로 검수한다.

운영 판정/팀 승인을 부여하지 않는다. 기대값은 모델 입력에서 제외한다.
실행 예: python run_phase2_llm_smoke.py --output-dir reports/phase2_llm_smoke_2026-10-01
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys
from tempfile import TemporaryDirectory
import time

import httpx
from pydantic import ValidationError

HERE = Path(__file__).resolve().parent
JUDGMENT_SRC = HERE.parent / "phase2_판단" / "src"
sys.path.insert(0, str(JUDGMENT_SRC))
from checklist_adapter import compact_reason_codes
from context_builder import build_context_from_phase1
from grounding_prompts import build_prompt_package, PROMPT_VERSION
from judgment_harness import run_item_judgment
from phase1_runtime import DEFAULT_RETRY_POLICY, GenerationConfig, LLMClientConfig
from llm_client import build_request_body, call_llm
from checklist_store import ChecklistStore
from judgment_review import ItemJudgment, check_review_output
from reason_codes import ReasonCatalog
from verify_chapter2_review_flow import make_request


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_unique(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def compact_prompt_json(user):
    """JSON 값은 유지하고 표시용 공백만 줄인다."""
    pattern = r"<(checklist_item|reason_codes|evidence_context)>\s*(.*?)\s*</\1>"
    def replace(match):
        value = parse_unique(match.group(2))
        return f"<{match.group(1)}>\n" + json.dumps(
            value, ensure_ascii=False, separators=(",", ":")) + f"\n</{match.group(1)}>"
    return re.sub(pattern, replace, user, flags=re.DOTALL)


def protected_hashes():
    paths = [HERE / name for name in (
        "chapter2_full_checklist_draft.json", "chapter2_reason_codes_draft.json",
        "chapter2_review_cases.json", "run_phase2_llm_smoke.py",
        "checklist_draft.json", "reason_codes_draft.json",
        "judgment_review.py", "reason_codes.py", "checklist_store.py")]
    paths += list(JUDGMENT_SRC.glob("*.py"))
    paths += list((HERE.parent / "phase1_판단" / "src").glob("*.py"))
    return {str(path.relative_to(HERE.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def prepare_case(case, document, store, catalog):
    control = next(c for c in document["controls"] if c["control_id"] == case["control_id"])
    request = make_request(control, document, store, catalog, case["evidence_text"],
                           item_ids=[case["item_id"]])
    context = build_context_from_phase1(
        [c.model_dump(mode="json") for c in request.chunks],
        request.source_phase1_result, case["control_id"], neighbor_count=0)
    context["review_context"] = request.context.model_dump(mode="json")
    item = request.questions[0].model_dump(mode="json", exclude_none=True)
    codes = compact_reason_codes({"codes": request.reason_definitions})
    return request, item, context, codes


def wrap_output(request, item_output):
    return {"checklist_version": request.checklist_version,
            "catalog_version": request.catalog_version,
            "evidence_id": request.evidence_id, "version": request.version,
            "item_results": [item_output]}


def strict_item_validator(request):
    def validate(payload):
        try:
            parsed = ItemJudgment.model_validate(payload)
        except ValidationError as error:
            return [str(error)]
        if (parsed.item_id, parsed.control_id) != (
                request.questions[0].item_id, request.questions[0].control_id):
            return ["item_id/control_id mismatch"]
        return []
    return validate


def summarize(rows, metadata):
    successful = [r for r in rows if r["harness_succeeded"]]
    latencies = [r["elapsed_seconds"] for r in rows]
    metadata.update({
        "status": "COMPLETED", "case_count": len(rows),
        "harness_success_count": len(successful),
        "validated_count": sum(r["review_validation_passed"] for r in rows),
        "expected_result_match_count": sum(r["expected_result_match"] for r in rows),
        "validated_and_expected_match_count": sum(
            r["review_validation_passed"] and r["expected_result_match"] for r in rows),
        "expected_reason_present_count": sum(r["expected_reason_present"] for r in rows),
        "retry_count": sum(r["retry_count"] for r in rows),
        "latency_seconds": {"mean": round(statistics.mean(latencies), 3),
                            "min": min(latencies), "max": max(latencies)},
        "result_counts": dict(Counter(r["actual_result"] for r in successful)),
        "cases": rows})
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11435/v1")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--explicit-reason-rules", action="store_true",
                        help="기존 사유 사용 규칙을 명시하고 JSON 표시용 공백을 줄인 비교 조건")
    args = parser.parse_args()
    if not args.prepare_only and args.output_dir is None:
        parser.error("실제 실행은 --output-dir이 필요합니다.")
    output = args.output_dir.resolve() if args.output_dir else None
    if output and (output.parent != (HERE / "reports").resolve() or output.exists()):
        parser.error("reports 바로 아래의 새 디렉터리만 사용할 수 있습니다.")
    before = protected_hashes()
    document = json.loads((HERE / "chapter2_full_checklist_draft.json").read_text(encoding="utf-8"))
    catalog = ReasonCatalog(HERE / "chapter2_reason_codes_draft.json")
    cases = json.loads((HERE / "chapter2_review_cases.json").read_text(encoding="utf-8"))["cases"]
    schema = ItemJudgment.model_json_schema()
    generation = GenerationConfig(temperature=0, thinking=False, stream=False, max_tokens=1024)
    config = LLMClientConfig(base_url=args.base_url)
    metadata = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                "model": args.model, "generation": asdict(generation),
                "retry_policy": {"max_retries": DEFAULT_RETRY_POLICY.max_retries,
                                 "timeout_seconds": DEFAULT_RETRY_POLICY.timeout_seconds},
                "base_url": args.base_url, "prompt_version": PROMPT_VERSION,
                "explicit_reason_rules": args.explicit_reason_rules,
                "json_prompt_serialization": "compact" if args.explicit_reason_rules else "team_default_pretty",
                "output_schema_origin": "ItemJudgment.model_json_schema()",
                "review_only": True, "human_approved": False, "synthetic": True,
                "actual_evidence_used": False, "llm_executed": not args.prepare_only,
                "checklist_version": document["draft_version"],
                "catalog_version": catalog.list_codes(allow_draft=True)["catalog_version"],
                "source_sha256": before,
                "expected_label_origin": "AI_DRAFT_NOT_HUMAN_APPROVED",
                "limits": ["대표 3문항·가상 9사례이며 700문항 정확도나 실제 증적 성능이 아니다.",
                           "기대 판정·기대 사유·기대 사유 설명은 모델 입력에 포함하지 않는다.",
                           "사유 코드 기대값 포함 여부는 진단 지표이며 복수 유효 사유를 오답으로 단정하지 않는다.",
                           "정확한 Qwen tokenizer 전체 예산 검사는 미연결이다.",
                           "Phase1 확정 매핑은 합성 fixture이며 실제 검색·매핑 성능을 검사하지 않는다.",
                           "검증기의 llm_executed=false는 검증 함수 자체의 역할 표시이며 실제 호출은 이 보고서에 기록한다."]}
    rows = []
    with TemporaryDirectory(prefix="phase2-llm-smoke-") as directory:
        store = ChecklistStore(Path(directory) / "checklist.sqlite3")
        store.import_draft(HERE / "chapter2_full_checklist_draft.json")
        if not args.prepare_only:
            native = args.base_url.rstrip("/").removesuffix("/v1")
            with httpx.Client(timeout=8, trust_env=False) as client:
                metadata["server_version"] = client.get(native + "/api/version").raise_for_status().json()
                tags = client.get(native + "/api/tags").raise_for_status().json()
                metadata["model_metadata"] = next(m for m in tags["models"] if m["name"] == args.model)
                metadata["server_ps_before"] = client.get(native + "/api/ps").raise_for_status().json()
            output.mkdir()
            write_json(output / "metadata.json", metadata)
            write_json(output / "output_schema.json", schema)
        for case in cases:
            request, item, context, codes = prepare_case(case, document, store, catalog)
            # 기존 카탈로그/검증기의 제약만 명시한다. 새 판정·예외 정책을 만들지 않는다.
            rules = ("검수용 기존 사유 규칙: MET은 reason_codes=[]로 출력한다. "
                     "NOT_MET과 UNKNOWN은 해당 result로 정의된 사유 코드만 1개 이상 출력한다."
                     if args.explicit_reason_rules else None)
            package = build_prompt_package(item, context, reason_codes=codes, output_schema=schema,
                                           global_rules=rules)
            actual_user = compact_prompt_json(package.user) if args.explicit_reason_rules else package.user
            body = build_request_body(package.system, actual_user, args.model, schema, generation=generation)
            if args.prepare_only:
                # Exercise the real prompt/schema/input path without sending gold labels to the model.
                assert "expected_result_draft" not in package.user
                print(json.dumps({"prepared": case["case_id"],
                                  "prompt_chars": len(package.system) + len(package.user)}, ensure_ascii=False))
                continue
            case_path = output / case["case_id"]
            case_path.mkdir()
            write_json(case_path / "request.json", body)
            write_json(case_path / "review_input.json", request.model_dump(mode="json"))
            exchanges = []
            def record_exchange(response):
                response.read()
                exchanges.append({"request_body": json.loads(response.request.content),
                                  "http_status": response.status_code,
                                  "response_text": response.text})
            def transport(system, user, *positional, **kwargs):
                if args.explicit_reason_rules:
                    user = compact_prompt_json(user)
                return call_llm(system, user, *positional, **kwargs)
            started = time.perf_counter()
            with httpx.Client(trust_env=False, event_hooks={"response": [record_exchange]}) as client:
                result = run_item_judgment(item, context, model=args.model, reason_codes=codes,
                                          output_schema=schema, output_validator=strict_item_validator(request),
                                          global_rules=rules, client_config=config, generation=generation,
                                          http_client=client, llm_call=transport)
            elapsed = round(time.perf_counter() - started, 3)
            write_json(case_path / "http_exchanges.json", exchanges)
            write_json(case_path / "harness_result.json", asdict(result))
            parsed = None
            validation = None
            raw_error = None
            if result.raw_response:
                try:
                    parsed = parse_unique(result.raw_response)
                    validation = check_review_output(request, wrap_output(request, parsed), store,
                                                     catalog=catalog, allow_draft=True)
                    write_json(case_path / "review_validation.json", validation)
                except (ValueError, TypeError) as error:
                    raw_error = str(error)
            actual = parsed.get("result") if isinstance(parsed, dict) else None
            selected = parsed.get("reason_codes", []) if isinstance(parsed, dict) else []
            expected_code = case["reason_code"]
            row = {"case_id": case["case_id"], "item_id": case["item_id"],
                   "expected_result_draft": case["expected_result_draft"], "actual_result": actual,
                   "expected_result_match": actual == case["expected_result_draft"],
                   "expected_reason_code_draft": expected_code, "actual_reason_codes": selected,
                   "expected_reason_present": expected_code in selected if expected_code else selected == [],
                   "harness_succeeded": result.succeeded, "attempts": result.attempts,
                   "retry_count": result.retry_count, "elapsed_seconds": elapsed,
                   "final_error_code": result.final_error_code,
                   "request_error_status": result.request_error_status,
                   "raw_json_error": raw_error,
                   "review_validation_passed": bool(validation and validation["validation"]["passed"])}
            rows.append(row)
            with (output / "results.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)
        if not args.prepare_only:
            with httpx.Client(timeout=8, trust_env=False) as client:
                metadata["server_ps_after"] = client.get(native + "/api/ps").raise_for_status().json()
    assert before == protected_hashes(), "protected source changed during run"
    if args.prepare_only:
        print(json.dumps({"status": "PREPARED", "case_count": len(cases),
                          "llm_executed": False, "sources_unchanged": True}, ensure_ascii=False))
    else:
        metadata["sources_unchanged"] = True
        summary = summarize(rows, metadata)
        write_json(output / "evaluation.json", summary)
        (output / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
        print(json.dumps({k: summary[k] for k in (
            "status", "case_count", "harness_success_count", "validated_count",
            "expected_result_match_count", "retry_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
