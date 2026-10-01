from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "phase1_입력"
SEARCH_DIR = ROOT / "phase1_검색"
JUDGMENT_DIR = ROOT / "phase1_판단"
RESULT_DIR = Path(__file__).resolve().parent / "results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# 팀별 구현을 수정하지 않고 adapter 계층에서 재사용한다.
for path in (INPUT_DIR, INPUT_DIR / "database", INPUT_DIR / "chunking", SEARCH_DIR):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)

from evidence_store import get_evidence, update_status  # noqa: E402
from chunk_store import get_chunks  # noqa: E402
from pipeline import process_one  # noqa: E402
from chunk_retriever import retrieve  # noqa: E402
from judgment_pipeline import run_judgment  # noqa: E402


class IntegrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str):
        self.code = code
        self.stage = stage
        super().__init__(message)


_lock = threading.Lock()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _persist(result: dict[str, Any]) -> Path:
    target = RESULT_DIR / f"{result['evidence_id']}_v{result['version']}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_result(evidence_id: str, version: int | None = None) -> dict[str, Any] | None:
    if version is not None:
        target = RESULT_DIR / f"{evidence_id}_v{version}.json"
        return json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None
    matches = list(RESULT_DIR.glob(f"{evidence_id}_v*.json"))
    if not matches:
        return None

    def version_of(path: Path) -> int:
        try:
            return int(path.stem.rsplit("_v", 1)[1])
        except (IndexError, ValueError):
            return -1

    target = max(matches, key=version_of)
    return json.loads(target.read_text(encoding="utf-8"))


def evidence_to_search_payload(evidence_id: str) -> dict[str, Any]:
    """입력팀 DB → 검색팀 문서 입력 계약으로 변환한다."""
    info = get_evidence(evidence_id)
    chunks = get_chunks(evidence_id, info["version"])
    if not chunks:
        raise IntegrationError("NO_CHUNKS", "전처리된 청크가 없습니다.", stage="preprocess")
    return {
        "evidence_id": info["evidence_id"],
        "version": info["version"],
        "source_file": info["file_name"],
        "file_type": info["file_type"],
        "chunks": chunks,
    }


def _screen_view(mapping_result: dict[str, Any], source_file: str) -> dict[str, Any]:
    """판단팀 확정 필드를 화면에서 바로 소비할 수 있는 형태로만 얇게 정리한다."""
    mapped = []
    for item in mapping_result.get("mapped_controls", []):
        citations = []
        for c in item.get("citations", []):
            citations.append({
                "chunk_id": c.get("chunk_id"),
                "page": c.get("page"),
                "quote": c.get("quote", ""),
                "source_file": source_file,
            })
        mapped.append({
            "control_id": item.get("control_id"),
            "control_name": item.get("control_name"),
            "relation": item.get("relation"),
            "similarity_score": item.get("similarity_score"),
            "llm_confidence": item.get("llm_confidence"),
            "reason": item.get("reason"),
            "citations": citations,
        })
    review = mapping_result.get("human_review", {})
    validation = mapping_result.get("validation", {})
    review_reasons = [
        (r.get("code") if isinstance(r, dict) else str(r))
        for r in review.get("reasons", [])
    ]
    match_status = mapping_result.get("match_status")
    # 판단팀 정책: R107(UNCERTAIN)이 붙은 NO_MATCH는 "관련 없음"이 아니라 판단 보류다.
    display_match_status = "판단 보류" if match_status == "NO_MATCH" and "R107" in review_reasons else match_status
    return {
        "summary": {
            "processing_status": mapping_result.get("processing_status"),
            "match_status": match_status,
            "display_match_status": display_match_status,
            "mapping_count": len(mapped),
            "review_required": bool(review.get("required")),
            "review_status": review.get("status"),
            "validation_passed": validation.get("passed"),
            "processing_time_ms": mapping_result.get("processing_time_ms"),
        },
        "mapped_controls": mapped,
        "human_review": review,
        "validation": validation,
    }


def inspect_evidence(
    evidence_id: str,
    *,
    top_k: int = 5,
    model: str | None = None,
    preprocess_if_needed: bool = True,
) -> dict[str, Any]:
    """업로드된 증적 하나를 전처리 → 검색 → 판단 → 화면 응답까지 한 번에 실행한다."""
    if type(top_k) is not int or not 1 <= top_k <= 101:
        raise IntegrationError("INVALID_TOP_K", "top_k는 1~101 사이의 정수여야 합니다.", stage="request")

    trace_id = f"phase1-{uuid.uuid4().hex[:12]}"
    model = (model or os.getenv("PHASE1_JUDGMENT_MODEL") or "").strip()
    if not model:
        raise IntegrationError(
            "MODEL_NOT_CONFIGURED",
            "PHASE1_JUDGMENT_MODEL 환경변수 또는 API model 값을 지정해야 합니다.",
            stage="judgment",
        )

    # 같은 증적의 상태와 결과 파일이 서로 엇갈리지 않게 직렬화한다.
    with _lock:
        info = get_evidence(evidence_id)
        if preprocess_if_needed and info["status"] in {"UPLOADED", "FAILED"}:
            state, detail = process_one(evidence_id, verbose=False)
            if state != "ok":
                raise IntegrationError("PREPROCESS_FAILED", str(detail), stage="preprocess")
            info = get_evidence(evidence_id)

        payload = evidence_to_search_payload(evidence_id)
        search_result = retrieve(payload, top_k=top_k)
        if not search_result.get("success"):
            err = search_result.get("error", {})
            raise IntegrationError(
                err.get("code", "SEARCH_FAILED"),
                err.get("message", "검색에 실패했습니다."),
                stage="search",
            )

        try:
            judged = run_judgment(
                search_result,
                model=model,
                trace_id=trace_id,
            )
        except Exception as exc:  # 판단팀 HTTP 4xx 등 명시적 중단을 API 오류로 전달
            raise IntegrationError("JUDGMENT_FAILED", str(exc), stage="judgment") from exc

        result = _jsonable(judged.mapping_result)
        evidence_status = _jsonable(judged.evidence_status)
        update_status(evidence_id, evidence_status)
        result_path = _persist(result)
        return {
            "success": True,
            "trace_id": trace_id,
            "evidence_id": evidence_id,
            "version": result["version"],
            "source_file": payload["source_file"],
            "evidence_status": evidence_status,
            "search": {
                "top_k": search_result["retrieval"]["top_k"],
                "candidates": search_result["retrieval"]["candidates"],
                "warnings": search_result.get("warnings", []),
            },
            "judgment": result,
            "screen": _screen_view(result, payload["source_file"]),
            "result_file": str(result_path),
            "completed_at": datetime.now().astimezone().isoformat(),
        }
