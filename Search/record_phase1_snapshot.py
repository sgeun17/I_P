"""자동 검사된 Phase 1 KB의 검수용 스냅샷. 최종 승인이나 배포 버전 고정이 아닙니다."""
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "releases/phase1-review-2026-09-25"


def main():
    raw = (ROOT / "controls.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    kb_report = json.loads((ROOT / "reports/kb_validation_result.json").read_text(encoding="utf-8"))
    index_report = json.loads((ROOT / "reports/chroma_index_result.json").read_text(encoding="utf-8"))
    assert kb_report["status"] == index_report["status"] == "PASS"
    assert kb_report["kb_sha256"] == index_report["kb_sha256"] == digest
    assert index_report["count"] == len(json.loads(raw)) == 101
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / "controls.json"
    if target.exists() and target.read_bytes() != raw:
        raise ValueError("기존 스냅샷과 KB가 다릅니다. 새 검수 버전 이름을 사용하세요.")
    target.write_bytes(raw)
    snapshots = {}
    for name in ("kb_validation_result.json", "chroma_index_result.json"):
        content = (ROOT / "reports" / name).read_bytes()
        (DEST / name).write_bytes(content)
        snapshots[name] = hashlib.sha256(content).hexdigest()
    manifest = {"snapshot_id": DEST.name, "status": "REVIEW_SNAPSHOT_NOT_FINAL_RELEASE",
                "created_at": datetime.now().astimezone().isoformat(), "kb_sha256": digest, "controls_count": 101,
                "model": index_report["model"], "embedding_cache_key": index_report["embedding_cache_key"],
                "chromadb_version": index_report["chromadb_version"], "collection": index_report["collection"],
                "retriever_schema": "retriever-0.2", "top_k": 5, "aggregation": "max_chunk_similarity",
                "source": kb_report["reference"], "report_sha256": snapshots,
                "human_kb_review_complete": False, "independent_relevance_labels_approved": False,
                "actual_llm_integration_verified": False, "production_relevance_cutoff": None}
    (DEST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (DEST / "README.md").write_text(
        "# Phase 1 검수용 스냅샷\n\n"
        "이 파일 묶음은 자동 검사를 통과한 현재 KB와 색인 정보를 보존합니다. 사람의 최종 내용 검수나 Phase 1 완료 승인을 뜻하지 않습니다.\n\n"
        f"- 항목 수: 101\n- KB SHA-256: {digest}\n- 검색: 문서 Top-5, 청크별 최대 코사인 유사도\n"
        "- 모델 revision, 인덱스 캐시 식별자, 기준 원문 정보는 manifest.json에 기록\n"
        "- 모델 가중치와 ChromaDB 원본 자체는 이 폴더에 복사하지 않음\n\n"
        "재현 시 Search/controls.json이 스냅샷과 같은 해시인지 확인하고 Search에서 chroma_index.py를 실행합니다. "
        "재색인·재시작 검증의 PASS와 생성 보고서의 해시를 확인하세요. KB가 달라지면 별도 버전과 정답 회귀 검증이 필요합니다.\n\n"
        "남은 승인: KB 내용 최종 검수, 독립 정답 검수, 실제 LLM 및 전체 통합 검증. 운영 관련성 임계값은 미설정입니다.\n",
        encoding="utf-8")
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    print(f"PASS: 검수용 스냅샷 {DEST}")


if __name__ == "__main__":
    main()
