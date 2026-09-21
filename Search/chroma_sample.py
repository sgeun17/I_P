"""9/21 WBS: 실제 BGE-M3 + Chroma 저장/검색/프로세스 재시작 확인."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from retriever import ROOT, MODEL_DIR, MODEL_ID, MODEL_REVISION, load_controls, control_text, load_model, normalized_vectors

DB_DIR = ROOT / "data" / "chroma_sample"
MANIFEST = ROOT / "data" / "chroma_sample_manifest.json"
COLLECTION = "isms_p_sample_10"
SAMPLE_IDS = ["2.5.1", "2.5.2", "2.5.3", "2.5.4", "2.5.5", "2.5.6",
              "2.6.1", "2.6.2", "2.6.6", "2.9.3"]
QUERY = "퇴사자의 사용자 계정을 삭제하고 승인자와 처리 일자를 계정 관리대장에 기록했다."


def require(condition, message):
    if not condition:
        raise ValueError(message)


def client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(
        path=str(DB_DIR), settings=Settings(anonymized_telemetry=False))


def query_rows(collection, vector):
    result = collection.query(query_embeddings=[vector], n_results=5,
                              include=["metadatas", "distances"])
    return [
        {"rank": index + 1, "control_id": cid,
         "control_name": metadata["control_name"],
         "distance": float(distance),
         "similarity_score": max(-1.0, min(1.0, 1.0 - float(distance)))}
        for index, (cid, metadata, distance) in enumerate(zip(
            result["ids"][0], result["metadatas"][0], result["distances"][0]))
    ]


def build():
    controls = {c["control_id"]: c for c in load_controls(ROOT / "controls.json")}
    sample = [controls[cid] for cid in SAMPLE_IDS]
    texts = [control_text(c) for c in sample]
    model = load_model(MODEL_DIR, "cpu")
    inputs = texts + [QUERY]
    tokens = model.tokenizer(inputs, truncation=False, padding=False)["input_ids"]
    require(all(len(t) <= model.max_seq_length for t in tokens), "입력이 모델 한도를 초과합니다.")
    print("[1/4] 샘플 KB 10개와 검색 문장을 BGE-M3로 변환합니다.", flush=True)
    vectors = normalized_vectors(model.encode(inputs, batch_size=4,
        normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False))
    kb_vectors, query_vector = vectors[:-1], vectors[-1].tolist()
    kb_hash = hashlib.sha256((ROOT / "controls.json").read_bytes()).hexdigest()
    metadatas = [{"control_id": c["control_id"], "control_name": c["control_name"],
                  "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                  "kb_sha256": kb_hash} for c in sample]
    db = client()
    collection = db.get_or_create_collection(name=COLLECTION, embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}})
    require(collection.configuration["hnsw"]["space"] == "cosine", "DB 거리 설정이 cosine이 아닙니다.")
    existing = set(collection.get()["ids"])
    require(existing <= set(SAMPLE_IDS), "샘플 DB에 다른 ID가 있습니다. DB 내용을 확인해 주세요.")
    print("[2/4] ChromaDB에 벡터·원문·ID·명칭을 저장합니다.", flush=True)
    collection.upsert(ids=SAMPLE_IDS, embeddings=kb_vectors.tolist(),
                      documents=texts, metadatas=metadatas)
    require(collection.count() == 10, "저장 개수가 10개가 아닙니다.")
    before = query_rows(collection, query_vector)
    manifest = {"ids": SAMPLE_IDS, "documents": texts, "metadatas": metadatas,
                "vectors": kb_vectors.tolist(), "query": QUERY, "query_vector": query_vector,
                "before_restart": before, "kb_sha256": kb_hash}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("샘플 저장 및 첫 검색 성공. 저장 프로세스를 종료합니다.", flush=True)


def verify():
    import numpy as np
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    db = client()
    collection = db.get_collection(COLLECTION, embedding_function=None)
    require(collection.configuration["hnsw"]["space"] == "cosine", "재시작 후 DB 거리 설정이 다릅니다.")
    print("[3/4] 새 프로세스에서 DB를 열고 저장 내용을 대조합니다.", flush=True)
    stored = collection.get(include=["documents", "metadatas", "embeddings"])
    require(collection.count() == 10 and set(stored["ids"]) == set(manifest["ids"]),
            "재시작 후 저장 개수 또는 ID가 다릅니다.")
    positions = {cid: i for i, cid in enumerate(stored["ids"])}
    for i, cid in enumerate(manifest["ids"]):
        j = positions[cid]
        require(stored["documents"][j] == manifest["documents"][i], f"{cid}: 원문 불일치")
        require(stored["metadatas"][j] == manifest["metadatas"][i], f"{cid}: 메타데이터 불일치")
        require(np.allclose(stored["embeddings"][j], manifest["vectors"][i], atol=1e-6),
                f"{cid}: 벡터 불일치")
    after = query_rows(collection, manifest["query_vector"])
    require(len(after) == 5, "검색 결과가 5개가 아닙니다.")
    require([r["control_id"] for r in after] ==
            [r["control_id"] for r in manifest["before_restart"]], "재시작 후 검색 순위가 다릅니다.")
    require(np.allclose([r["distance"] for r in after],
                        [r["distance"] for r in manifest["before_restart"]], atol=1e-6),
            "재시작 후 점수가 다릅니다.")
    kb = normalized_vectors(manifest["vectors"])
    q = normalized_vectors([manifest["query_vector"]])[0]
    scores = np.clip(kb @ q, -1, 1)
    expected = [manifest["ids"][i] for i in np.argsort(-scores, kind="stable")[:5]]
    require([r["control_id"] for r in after] == expected, "직접 코사인 계산과 DB 순위가 다릅니다.")
    for row in after:
        i = manifest["ids"].index(row["control_id"])
        require(abs(row["similarity_score"] - float(scores[i])) < 1e-5, "코사인 점수 불일치")
    print("[4/4] 재검색 및 직접 코사인 계산 대조 성공.", flush=True)
    report = {
        "status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
        "chromadb_version": version("chromadb"), "python_version": sys.version.split()[0],
        "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
        "kb_sha256": manifest["kb_sha256"], "db_path": str(DB_DIR),
        "collection": COLLECTION, "count": collection.count(), "dimension": int(kb.shape[1]),
        "sample_ids": manifest["ids"], "query": manifest["query"], "results": after,
        "checks": {"stored_count_and_ids": True, "documents_and_metadata": True,
                   "vectors_after_process_restart": True, "same_search_after_restart": True,
                   "matches_direct_cosine": True},
        "scope": "샘플 10개 로컬 저장·검색·재시작 검증. 전체 101개 검색 품질 평가는 아님.",
    }
    report_dir = ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "chroma_sample_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# ChromaDB 샘플 구동 검증 결과", "", f"- 결과: **PASS** ({report['checked_at']})",
             f"- ChromaDB: {report['chromadb_version']} / Python: {report['python_version']}",
             f"- 임베딩: {MODEL_ID}, {kb.shape[1]}차원 / CPU 로컬 실행",
             f"- 모델 revision: `{MODEL_REVISION}`", f"- KB SHA-256: `{manifest['kb_sha256']}`",
             f"- DB: `{DB_DIR}`", f"- 컬렉션: `{COLLECTION}` / 저장 항목: 10개",
             "- ID·원문·메타데이터·벡터 저장 및 새 프로세스에서 재조회: 통과",
             "- 재시작 전후 Top-5 순위·점수 일치: 통과",
             "- 직접 코사인 유사도 계산과 DB 검색 결과 대조: 통과", "",
             f"검색 문장: {manifest['query']}", "",
             "| 순위 | ID | 명칭 | 거리 | 유사도 |", "|---|---|---|---|---|"]
    lines += [f"| {r['rank']} | {r['control_id']} | {r['control_name']} | {r['distance']:.6f} | {r['similarity_score']:.6f} |" for r in after]
    lines += ["", "거리는 작을수록 유사하며, 이 DB는 cosine 거리로 설정했습니다. 유사도 = 1 - 거리입니다.",
              "", "범위: 9/21 WBS의 로컬 임베딩·Vector DB 구동 확인. 별도 서버 없이 실행하는 내장형 로컬 DB입니다.",
              "샘플 10개 중 검색한 결과이며, 전체 101개 색인·기존 Retriever 전환·정답/무관 증적 품질 평가는 후속 작업입니다."]
    (report_dir / "chroma_sample_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for row in after:
        print(f"{row['rank']}. {row['control_id']} {row['control_name']} | 유사도 {row['similarity_score']:.6f}")
    print(f"\nPASS: 샘플 DB 구동 확인 완료\n결과: {report_dir / 'chroma_sample_result.md'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["all", "build", "verify"], default="all")
    args = parser.parse_args()
    try:
        if args.stage == "all":
            # 저장 프로세스의 완전 종료 후 별도의 프로세스에서 DB를 다시 엽니다.
            for stage in ("build", "verify"):
                subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                                "--stage", stage], check=True)
        elif args.stage == "build":
            build()
        else:
            verify()
        return 0
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        print("이전 보고서가 있더라도 이번 실행의 성공 결과가 아닙니다.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
