"""현재 KB 101개를 ChromaDB에 색인하고 새 프로세스에서 검증합니다."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

from retriever import ROOT, Retriever, load_controls, control_text, normalized_vectors
from chroma_sample import require, query_rows

DB_DIR = ROOT / "data" / "chroma_kb"
COLLECTION = "isms_p_controls"
MANIFEST = ROOT / "data" / "chroma_kb_manifest.json"
SNAPSHOT = ROOT / "data" / "chroma_kb_validation.npz"
QUERIES = [
    "퇴사자의 사용자 계정을 삭제하고 승인자와 처리 일자를 계정 관리대장에 기록했다.",
    "백업 데이터를 정기적으로 복구하여 정상 복원되는지 시험하고 결과를 기록했다.",
    "보유기간이 만료된 개인정보를 파기하고 파기 일자와 담당자를 대장에 기록했다.",
]


def kb_hash():
    return hashlib.sha256((ROOT / "controls.json").read_bytes()).hexdigest()


def client():
    import chromadb
    from chromadb.config import Settings
    # Windows의 Chroma 1.5.9 HNSW 저장부가 한글 절대 경로에서 인덱스
    # 파일을 남기지 못하는 현상을 재현했습니다. 고정한 작업 폴더 아래
    # ASCII 상대 경로를 사용하고, 다른 위치에 DB를 여는 실수를 막습니다.
    require(Path.cwd().resolve() == ROOT.resolve(), "DB 실행 기준 폴더는 Search여야 합니다.")
    return chromadb.PersistentClient(path="data/chroma_kb", settings=Settings(anonymized_telemetry=False))


def build():
    print("[1/4] 현재 KB·모델과 기존 벡터 캐시가 일치하는지 확인합니다.", flush=True)
    initial_hash = kb_hash()
    retriever = Retriever()  # 내용·순서·모델·라이브러리 불일치 시 자동 재임베딩
    require(kb_hash() == initial_hash, "실행 중 KB가 변경됐습니다. 다시 실행해 주세요.")
    ids = [c["control_id"] for c in retriever.controls]
    require(len(ids) == len(set(ids)) == 101, "101개 고유 ID가 필요합니다.")
    require(retriever.vectors.shape == (101, 1024), "BGE-M3 벡터 차원이 예상과 다릅니다.")
    model_info = json.loads((ROOT / "models" / "bge-m3" / "download_complete.json").read_text(encoding="utf-8"))
    metadata = [{"control_id": c["control_id"], "control_name": c["control_name"],
                 "kb_sha256": initial_hash, "embedding_cache_key": retriever.cache_key,
                 "model_id": model_info["model_id"], "model_revision": model_info["revision"]}
                for c in retriever.controls]
    db = client()
    collection = db.get_or_create_collection(name=COLLECTION, embedding_function=None,
        configuration={"hnsw": {"space": "cosine", "ef_search": 200,
                                  "batch_size": 100, "sync_threshold": 100}})
    require(collection.configuration["hnsw"]["space"] == "cosine", "DB 거리 설정이 cosine이 아닙니다.")
    require(set(collection.get()["ids"]) <= set(ids), "DB에 현재 KB에 없는 ID가 있습니다. 확인이 필요합니다.")
    print("[2/4] 101개 벡터·ID·명칭·원문을 ChromaDB에 저장하고 색인합니다.", flush=True)
    payload = dict(ids=ids, embeddings=retriever.vectors.tolist(),
                   metadatas=metadata, documents=retriever.texts)
    collection.upsert(**payload)
    require(collection.count() == 101, "저장 개수가 101개가 아닙니다.")
    # 같은 데이터로 다시 색인해도 중복이 늘어나지 않는지 실제 DB에서 확인합니다.
    collection.upsert(**payload)
    require(collection.count() == 101, "재색인 후 중복이 발생했습니다.")
    queries = retriever._encode(QUERIES)
    before = [query_rows(collection, vector.tolist()) for vector in queries]
    np.savez_compressed(SNAPSHOT, vectors=retriever.vectors, query_vectors=queries,
                        cache_key=retriever.cache_key)
    manifest = {"kb_sha256": initial_hash, "embedding_cache_key": retriever.cache_key,
                "ids": ids, "documents": retriever.texts, "metadatas": metadata,
                "model": model_info, "queries": QUERIES, "before_restart": before,
                "snapshot_sha256": hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장·재색인 성공. 저장 프로세스를 종료합니다.", flush=True)


def verify():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require(kb_hash() == manifest["kb_sha256"], "색인 후 KB가 바뀌었습니다. 전체 실행으로 재색인해 주세요.")
    controls = load_controls(ROOT / "controls.json")
    require(manifest["ids"] == [c["control_id"] for c in controls], "현재 KB와 ID 연결이 다릅니다.")
    require(manifest["documents"] == [control_text(c) for c in controls], "현재 검색 원문과 색인 원문이 다릅니다.")
    require(hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == manifest["snapshot_sha256"], "검증 벡터 사본이 변경됐습니다.")
    with np.load(SNAPSHOT, allow_pickle=False) as saved:
        vectors, queries = saved["vectors"], saved["query_vectors"]
        require(str(saved["cache_key"].item()) == manifest["embedding_cache_key"], "벡터 캐시 식별자가 다릅니다.")
    for filename in ("header.bin", "data_level0.bin", "length.bin", "link_lists.bin"):
        require(any(p.stat().st_size > 0 for p in DB_DIR.glob(f"*/{filename}")),
                f"저장된 검색 인덱스 파일을 확인할 수 없습니다: {filename}")
    collection = client().get_collection(COLLECTION, embedding_function=None)
    require(collection.configuration["hnsw"]["space"] == "cosine", "DB 거리 설정 불일치")
    print("[3/4] 새 프로세스에서 101개 ID·원문·메타데이터·벡터를 대조합니다.", flush=True)
    stored = collection.get(include=["embeddings", "documents", "metadatas"])
    require(collection.count() == 101 and set(stored["ids"]) == set(manifest["ids"]), "저장 개수 또는 ID 불일치")
    positions = {cid: i for i, cid in enumerate(stored["ids"])}
    exports = []
    for i, cid in enumerate(manifest["ids"]):
        j = positions[cid]
        require(stored["documents"][j] == manifest["documents"][i], f"{cid}: 원문 불일치")
        require(stored["metadatas"][j] == manifest["metadatas"][i], f"{cid}: 메타데이터 불일치")
        require(np.allclose(stored["embeddings"][j], vectors[i], atol=1e-6, rtol=0), f"{cid}: 벡터 불일치")
        exports.append({"control_id": cid, "control_name": stored["metadatas"][j]["control_name"],
                        "dimension": len(stored["embeddings"][j]), "document": stored["documents"][j],
                        "vector": stored["embeddings"][j].tolist()})
    self_results = collection.query(query_embeddings=vectors.tolist(), n_results=1, include=["distances"])
    require([row[0] for row in self_results["ids"]] == manifest["ids"], "자기 벡터 검색 시 원래 ID 불일치")
    print("[4/4] 101개 자기 벡터 검색 및 예시 3건의 Top-5를 검증합니다.", flush=True)
    results = []
    kb = normalized_vectors(vectors)
    for i, vector in enumerate(queries):
        rows = query_rows(collection, vector.tolist())
        before = manifest["before_restart"][i]
        require([r["control_id"] for r in rows] == [r["control_id"] for r in before], "재시작 후 순위 불일치")
        require(np.allclose([r["distance"] for r in rows], [r["distance"] for r in before], atol=1e-6, rtol=0), "재시작 후 점수 불일치")
        scores = np.clip(kb @ normalized_vectors([vector])[0], -1, 1)
        order = np.argsort(-scores, kind="stable")[:5]
        require([r["control_id"] for r in rows] == [manifest["ids"][j] for j in order], "직접 계산과 DB 검색 순위 불일치")
        require(np.allclose([r["similarity_score"] for r in rows], scores[order], atol=1e-5, rtol=0), "직접 계산과 DB 점수 불일치")
        results.append({"text": manifest["queries"][i], "candidates": rows})
    report = {"status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
              "kb_sha256": manifest["kb_sha256"], "embedding_cache_key": manifest["embedding_cache_key"],
              "model": manifest["model"], "chromadb_version": version("chromadb"),
              "db_path": str(DB_DIR), "collection": COLLECTION, "count": 101, "dimension": 1024,
              "checks": {"hnsw_files_persisted": True, "all_records_after_restart": True, "repeat_upsert_count": 101,
                         "self_vector_top1_matches": 101, "query_top5_matches_direct_cosine": 3},
              "queries": results,
              "scope": "101개 KB 색인·저장·검색 동작 검증. 실제 증적 정답률 평가와 KB 최종 버전 고정은 별도."}
    report_dir = ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "chroma_index_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data" / "chroma_kb_vectors_view.json").write_text(json.dumps(
        {"source": "전체 ChromaDB 조회 사본 (자동 동기화되지 않음)", "exported_at": report["checked_at"],
         "collection": COLLECTION, "count": 101, "controls": exports}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 101개 인증기준 ChromaDB 색인 검증 결과", "", f"- 결과: **PASS** ({report['checked_at']})",
             f"- ChromaDB: {report['chromadb_version']} / 컬렉션: `{COLLECTION}`",
             "- BGE-M3: 인증기준 101개 × 1,024차원", f"- DB 저장 위치: `{DB_DIR}`",
             f"- KB SHA-256: `{manifest['kb_sha256']}`",
             "- 현재 KB·모델·라이브러리 기준의 임베딩 캐시 사용 (불일치 시 재생성)",
             "- 101개 ID·원문·메타데이터·벡터: 새 프로세스에서 전체 대조 통과",
             "- 검색 인덱스(HNSW) 파일의 디스크 저장 확인: 통과",
             "- 동일 자료 재색인 후 개수: 101개 (중복 없음)",
             "- 자기 벡터 검색 시 원래 ID가 1위: 101/101 (색인 무결성 검사)",
             "- 예시 3건: 재시작 전후 및 직접 코사인 계산과 Top-5 순위·점수 일치", ""]
    for result in results:
        lines += [f"검색 문장: {result['text']}", "", "| 순위 | ID | 명칭 | 유사도 |", "|---|---|---|---|"]
        lines += [f"| {r['rank']} | {r['control_id']} | {r['control_name']} | {r['similarity_score']:.6f} |" for r in result["candidates"]]
        lines.append("")
    lines += ["이 Windows 환경에서는 한글 절대 경로로 검색 인덱스를 재로딩하지 못하는 현상이 있어, Search를 실행 기준 폴더로 고정하고 data/chroma_kb 상대 경로로 DB를 엽니다.", "",
              "이는 DB 색인·검색 동작 검증입니다. 실제 증적의 정답률, 무관 증적 판별, KB 최종 검수·버전 고정은 완료한 것이 아닙니다.",
              "기존 02_search.cmd는 벡터 파일 직접 비교 방식이며, 전처리 청크·상시 검색의 DB 연결은 후속 작업입니다."]
    (report_dir / "chroma_index_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"PASS: 101개 ChromaDB 색인 검증 완료\n결과: {report_dir / 'chroma_index_result.md'}", flush=True)


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["all", "build", "verify"], default="all")
    args = parser.parse_args()
    try:
        if args.stage == "all":
            for stage in ("build", "verify"):
                subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--stage", stage], check=True)
        elif args.stage == "build":
            build()
        else:
            verify()
        return 0
    except Exception as error:
        print(f"FAIL: {error}\n이전 보고서는 이번 실행의 성공을 뜻하지 않습니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
