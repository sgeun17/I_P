"""저장된 Phase 1 ``search.json``으로 판단 단계만 다시 실행한다.

OCR/파싱/BGE-M3 검색을 이미 통과한 시험 결과에서 프롬프트·Validator·재시도
변경만 빠르게 검증할 때 사용한다. 원본 결과 디렉터리는 수정하지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
import traceback


REPO = Path(__file__).resolve().parents[2]
SEARCH = REPO / "phase1_검색"
JUDGMENT_SRC = REPO / "phase1_판단" / "src"
sys.path[:0] = [str(SEARCH), str(JUDGMENT_SRC)]

from judgment_pipeline import JudgmentRequestError, run_judgment  # noqa: E402
from llm_config import GenerationConfig, LLMClientConfig  # noqa: E402
from review_policy import RetryPolicy  # noqa: E402


def _save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _value(value: object) -> object:
    return getattr(value, "value", value)


def _count(rows: list[dict], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows if row.get(field) is not None).items()))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="저장된 search.json을 재사용해 Phase 1 판단만 다시 실행합니다."
    )
    parser.add_argument("--source-dir", type=Path, required=True, help="기존 full 실행 결과 디렉터리")
    parser.add_argument("--out-dir", type=Path, required=True, help="새 판단 결과 디렉터리(기존 경로 불가)")
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    source = args.source_dir.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"source directory not found: {source}")
    search_files = sorted(source.glob("E*/search.json"))
    if not search_files:
        parser.error(f"no E*/search.json files under: {source}")
    out.mkdir(parents=True, exist_ok=False)

    client = LLMClientConfig(base_url=args.ollama_url, timeout_seconds=args.timeout)
    generation = GenerationConfig(temperature=0.0, max_tokens=4096, stream=False, thinking=False)
    retry = RetryPolicy(max_retries=1, timeout_seconds=args.timeout, backoff_seconds=2)
    rows: list[dict] = []

    for number, search_file in enumerate(search_files, 1):
        evidence_id = search_file.parent.name
        search_result = json.loads(search_file.read_text(encoding="utf-8"))
        file_name = search_result.get("source_file") or evidence_id
        item_dir = out / evidence_id
        item_dir.mkdir()
        row = {
            "evidence_id": evidence_id,
            "file_name": file_name,
            "judgment_status": "FAILED",
            "source_search": str(search_file),
        }
        print(f"JUDGE [{number:02d}/{len(search_files)}] {evidence_id} {file_name}", flush=True)

        try:
            run = run_judgment(
                search_result,
                model=args.model,
                client_config=client,
                generation=generation,
                retry_policy=retry,
                trace_id=f"judgment-rerun-{evidence_id.lower()}",
            )
            result = run.mapping_result.model_dump(mode="json")
            _save_json(item_dir / "judgment.json", result)
            if run.llm_run.provider_raw_response is not None:
                (item_dir / "provider_raw_response.txt").write_text(
                    run.llm_run.provider_raw_response, encoding="utf-8"
                )
            if run.llm_run.raw_response is not None:
                (item_dir / "normalized_response.json").write_text(
                    run.llm_run.raw_response, encoding="utf-8"
                )
            meta = {
                "attempts": run.llm_run.attempts,
                "retry_count": run.llm_run.retry_count,
                "call_error": _value(run.llm_run.call_error),
                "last_issue_codes": [_value(code) for code in run.llm_run.last_issue_codes],
                "request_error_status": run.llm_run.request_error_status,
                "request_error_message": run.llm_run.request_error_message,
                "elapsed_ms": run.elapsed_ms,
            }
            _save_json(item_dir / "llm_meta.json", meta)
            row.update(
                judgment_status=result["processing_status"],
                match_status=result["match_status"],
                mapped_controls=",".join(c["control_id"] for c in result["mapped_controls"]),
                validation_passed=result["validation"]["passed"],
                validation_issues=",".join(i["code"] for i in result["validation"]["issues"]),
                review_required=result["human_review"]["required"],
                review_reasons=",".join(result["human_review"]["reasons"]),
                attempts=run.llm_run.attempts,
                retry_count=run.llm_run.retry_count,
            )
            print(
                f"  {row['judgment_status']}: {row['match_status']} "
                f"issues={row['validation_issues'] or '-'} retry={row['retry_count']}",
                flush=True,
            )
        except JudgmentRequestError as exc:
            row["judgment_status"] = "REQUEST_FAILED"
            row["judgment_error"] = f"HTTP {exc.status_code}: {exc}"
        except Exception as exc:  # 한 건의 실패로 전체 배치를 중단하지 않는다.
            row["judgment_error"] = f"{type(exc).__name__}: {exc}"
            (item_dir / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            _save_json(item_dir / "row.json", row)
            rows.append(row)

    fields = sorted({key for row in rows for key in row})
    with (out / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    issue_counts = Counter(
        code
        for row in rows
        for code in str(row.get("validation_issues") or "").split(",")
        if code
    )
    summary = {
        "run_version": "phase1-judgment-rerun-v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "source_dir": str(source),
        "model": args.model,
        "ollama_url": args.ollama_url,
        "total": len(rows),
        "judgment": _count(rows, "judgment_status"),
        "validation_passed": sum(row.get("validation_passed") is True for row in rows),
        "review_required": sum(row.get("review_required") is True for row in rows),
        "retried": sum(int(row.get("retry_count") or 0) > 0 for row in rows),
        "validation_issue_counts": dict(sorted(issue_counts.items())),
        "request_or_runtime_failures": sum(bool(row.get("judgment_error")) for row in rows),
    }
    _save_json(out / "summary.json", summary)
    _save_json(out / "rows.json", rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 1 if summary["request_or_runtime_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
