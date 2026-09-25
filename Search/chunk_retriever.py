"""전처리 청크 → 로컬 BGE-M3 → ChromaDB Top-K → 판단팀 전달 JSON.

공개 Python 함수 retrieve()는 별도 프로세스를 사용하여 호출자의 작업 폴더를 보존합니다.
입력팀의 소스는 import하거나 수정하지 않습니다.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile

from retriever import (ROOT, MODEL_DIR, load_controls, control_text, load_model,
                       embedding_cache_key, encode_texts)

SCHEMA_VERSION = "retriever-0.2"
COLLECTION = "isms_p_controls"
FILE_TYPES = {"pdf", "docx", "xlsx", "pptx", "txt", "csv"}
CHUNK_KEYS = {"chunk_id", "evidence_id", "version", "chunk_index", "file_type", "source_file",
              "chunk_type", "page_start", "page_end", "heading", "text", "block_orders", "source"}


class RetrievalError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check(condition, message, code="INVALID_INPUT"):
    if not condition:
        raise RetrievalError(code, message)


def positive_int(value):
    return type(value) is int and value > 0


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def prepare_input(payload, compatibility=False):
    """원문을 보존하고, 임시 호환 변환을 warnings에 기록합니다."""
    check(isinstance(payload, dict), "입력은 증적 정보와 chunks를 가진 객체여야 합니다.")
    check(not payload.get("errors"), "전처리 오류가 있는 문서는 검색할 수 없습니다.", "PREPROCESSING_FAILED")
    document = deepcopy(payload)
    warnings = []

    def warn(code, field, message):
        warnings.append({"code": code, "field": field, "message": message})

    def evidence_id(value, field):
        if positive_int(value) and compatibility:
            warn("EVIDENCE_ID_CONVERTED", field, "입력팀 정수 ID를 저장 모듈의 6자리 문자열로 변환했습니다.")
            return str(value).zfill(6)
        check(nonempty(value) and value == value.strip(), f"{field}: 공백 없는 증적 ID 문자열이 필요합니다.")
        return value

    document["evidence_id"] = evidence_id(document.get("evidence_id"), "evidence_id")
    check(positive_int(document.get("version")), "version은 1 이상의 정수여야 합니다.")
    check(nonempty(document.get("source_file")), "source_file에 원래 파일명을 명시해 주세요.")
    check(isinstance(document.get("file_type"), str) and document["file_type"] in FILE_TYPES, "지원하지 않는 file_type입니다.")
    chunks = document.get("chunks")
    check(isinstance(chunks, list) and 1 <= len(chunks) <= 5000, "chunks는 1~5000개 청크 목록이어야 합니다.")
    seen = set()
    for index, chunk in enumerate(chunks):
        field = f"chunks[{index}]"
        check(isinstance(chunk, dict), f"{field}: 객체가 아닙니다.")
        chunk["evidence_id"] = evidence_id(chunk.get("evidence_id"), f"{field}.evidence_id")
        check(chunk["evidence_id"] == document["evidence_id"], f"{field}: 다른 증적 ID가 섞였습니다.")
        check(positive_int(chunk.get("version")) and chunk["version"] == document["version"], f"{field}: 버전이 다릅니다.")
        check(type(chunk.get("chunk_index")) is int and chunk["chunk_index"] == index, f"{field}: chunk_index는 0부터 연속이어야 합니다.")
        cid = chunk.get("chunk_id")
        check(nonempty(cid) and cid == cid.strip() and cid not in seen, f"{field}: 청크 ID가 비었거나 중복됐습니다.")
        seen.add(cid)
        check(chunk.get("file_type") == document["file_type"], f"{field}: file_type이 다릅니다.")
        if compatibility and "source_file" not in chunk:
            chunk["source_file"] = document["source_file"]
            warn("SOURCE_FILE_FROM_DOCUMENT", field, "누락된 파일명을 명시된 문서 정보에서 보충했습니다.")
        check(chunk.get("source_file") == document["source_file"], f"{field}: 원본 파일명이 다르거나 누락됐습니다.")
        source = chunk.get("source")
        if compatibility and nonempty(source) and source not in {"parser", "ocr"}:
            check(PureWindowsPath(source).name == PureWindowsPath(document["source_file"]).name,
                  f"{field}: source의 파일명이 문서와 다릅니다.")
            chunk["source"] = "parser"
            warn("LEGACY_SOURCE_CONVERTED", field, "기존 source의 파일 경로를 parser로 변환했습니다. 입력팀 수정 전 호환 처리입니다.")
        check(CHUNK_KEYS <= set(chunk), f"{field}: 필수 필드 누락 {sorted(CHUNK_KEYS - set(chunk))}")
        check(chunk["source"] in ("parser", "ocr"), f"{field}: source는 parser 또는 ocr여야 합니다.")
        check(chunk["chunk_type"] in ("text", "table"), f"{field}: 잘못된 chunk_type입니다.")
        check(nonempty(chunk["text"]), f"{field}: text가 비었습니다.", "EMPTY_TEXT")
        # 800글자는 전처리의 현재 설정이지 모델의 토큰 한도가 아닙니다.
        check(len(chunk["text"]) <= 100000, f"{field}: 과도하게 긴 입력입니다.", "INPUT_TOO_LONG")
        check(chunk["heading"] is None or nonempty(chunk["heading"]), f"{field}: heading이 잘못됐습니다.")
        start, end = chunk["page_start"], chunk["page_end"]
        if chunk["file_type"] in {"pdf", "pptx", "xlsx"}:
            check(positive_int(start) and positive_int(end) and start <= end, f"{field}: 페이지 범위를 확인해 주세요.")
        else:
            check(start is None and end is None, f"{field}: 이 파일 유형의 페이지는 null이어야 합니다.")
        orders = chunk["block_orders"]
        check(isinstance(orders, list) and orders and all(positive_int(o) for o in orders), f"{field}: block_orders를 확인해 주세요.")
        check(orders == sorted(set(orders)), f"{field}: block_orders가 중복되거나 정렬되지 않았습니다.")
        check(chunk["chunk_type"] != "table" or len(orders) == 1, f"{field}: 표 청크는 블록 하나에 연결돼야 합니다.")
    return document, warnings


def validate_index(collection, controls, kb_sha256, cache_key):
    check(collection.count() == 101, "전체 101개 KB 색인이 필요합니다. 04_index_chroma.cmd를 실행하세요.", "INDEX_MISMATCH")
    check(collection.configuration["hnsw"]["space"] == "cosine", "DB가 cosine 거리 설정이 아닙니다.", "INDEX_MISMATCH")
    stored = collection.get(include=["documents", "metadatas"])
    lookup = {cid: i for i, cid in enumerate(stored["ids"])}
    check(set(lookup) == {c["control_id"] for c in controls}, "DB와 현재 KB의 ID가 다릅니다.", "INDEX_MISMATCH")
    for control in controls:
        i = lookup[control["control_id"]]
        metadata = stored["metadatas"][i] or {}
        check(metadata.get("kb_sha256") == kb_sha256 and metadata.get("embedding_cache_key") == cache_key
              and metadata.get("control_id") == control["control_id"]
              and metadata.get("control_name") == control["control_name"]
              and stored["documents"][i] == control_text(control),
              "KB 또는 모델이 색인 당시와 다릅니다. 04_index_chroma.cmd로 재색인하세요.", "INDEX_MISMATCH")


def assemble_result(document, warnings, controls, db_results, top_k, kb_sha256, cache_key):
    lookup = {c["control_id"]: c for c in controls}
    check(type(top_k) is int and 1 <= top_k <= len(controls), "잘못된 top_k입니다.")
    check(len(db_results["ids"]) == len(db_results["distances"]) == len(document["chunks"]),
          "청크와 DB 결과 개수가 다릅니다.", "INVALID_DB_RESULT")
    groups, aggregated = [], {}
    for index, chunk in enumerate(document["chunks"]):
        ids, distances = db_results["ids"][index], db_results["distances"][index]
        # 101개를 모두 받아 집계: 청크별로 먼저 자르면 동점/연결 정보를 잃을 수 있습니다.
        check(len(ids) == len(distances) == len(lookup) and set(ids) == set(lookup),
              "전체 KB 검색 결과의 누락 또는 중복 ID를 확인해 주세요.", "INVALID_DB_RESULT")
        for cid, distance in zip(ids, distances):
            check(isinstance(distance, (int, float)) and math.isfinite(distance)
                  and -1e-5 <= distance <= 2.00001, "DB 검색 점수 또는 ID가 잘못됐습니다.", "INVALID_DB_RESULT")
        pairs = sorted(zip(ids, distances), key=lambda p: (p[1], p[0]))
        candidates = []
        for rank, (cid, distance) in enumerate(pairs, 1):
            score = max(-1.0, min(1.0, 1.0 - distance))
            if cid not in aggregated:
                aggregated[cid] = {"score": score, "best_chunk_id": chunk["chunk_id"], "matched_chunk_ids": []}
            elif score > aggregated[cid]["score"]:
                aggregated[cid].update(score=score, best_chunk_id=chunk["chunk_id"])
            if rank <= top_k:
                aggregated[cid]["matched_chunk_ids"].append(chunk["chunk_id"])
                candidates.append({"rank": rank, "control_id": cid, "control_name": lookup[cid]["control_name"],
                                   "similarity_score": round(score, 6)})
        groups.append({"chunk_id": chunk["chunk_id"], "candidates": candidates})
    selected = sorted(aggregated, key=lambda cid: (-aggregated[cid]["score"], cid))[:top_k]
    document_candidates, candidate_controls = [], []
    for rank, cid in enumerate(selected, 1):
        info = aggregated[cid]
        document_candidates.append({"rank": rank, "control_id": cid, "control_name": lookup[cid]["control_name"],
                                    "similarity_score": round(info["score"], 6),
                                    "best_chunk_id": info["best_chunk_id"],
                                    "matched_chunk_ids": info["matched_chunk_ids"]})
        candidate_controls.append({"control_id": cid, "control_name": lookup[cid]["control_name"],
                                   "requirement": lookup[cid]["requirement"],
                                   "matched_chunk_ids": info["matched_chunk_ids"]})
    return {"schema_version": SCHEMA_VERSION, "success": True,
            "evidence_id": document["evidence_id"], "version": document["version"],
            "source_file": document["source_file"], "file_type": document["file_type"],
            "evidence_chunks": document["chunks"],
            "retrieval": {"unit": "document", "top_k": top_k, "score_type": "cosine_similarity",
                          "aggregation": "max_chunk_similarity", "query_text_field": "text",
                          "candidates": document_candidates, "chunk_results": groups},
            "candidate_controls": candidate_controls, "warnings": warnings,
            "index": {"collection": COLLECTION, "kb_sha256": kb_sha256, "embedding_cache_key": cache_key},
            "generated_at": datetime.now().astimezone().isoformat()}


class LocalDocumentRetriever:
    """검색 전용 프로세스에서 모델을 재사용합니다. 실행 기준 폴더는 Search입니다."""
    def __init__(self):
        check(Path.cwd().resolve() == ROOT.resolve(), "검색 작업 폴더 설정이 잘못됐습니다.", "ENVIRONMENT_ERROR")
        check((ROOT / "data/chroma_kb/chroma.sqlite3").is_file(), "101개 KB DB가 없습니다. 04_index_chroma.cmd를 실행하세요.", "INDEX_MISSING")
        import chromadb
        from chromadb.config import Settings
        self.controls = load_controls(ROOT / "controls.json")
        self.kb_sha = hashlib.sha256((ROOT / "controls.json").read_bytes()).hexdigest()
        self.model = load_model(MODEL_DIR, "cpu")
        self.cache_key = embedding_cache_key([control_text(c) for c in self.controls], MODEL_DIR, self.model)
        self.db = chromadb.PersistentClient(path="data/chroma_kb", settings=Settings(anonymized_telemetry=False))
        self.collection = self.db.get_collection(COLLECTION, embedding_function=None)
        validate_index(self.collection, self.controls, self.kb_sha, self.cache_key)

    def search(self, payload, top_k=5, compatibility=False):
        check(type(top_k) is int and 1 <= top_k <= 101, "top_k는 1~101의 정수여야 합니다.")
        document, warnings = prepare_input(payload, compatibility)
        check(hashlib.sha256((ROOT / "controls.json").read_bytes()).hexdigest() == self.kb_sha,
              "실행 중 KB가 변경됐습니다. 재색인 후 다시 실행하세요.", "INDEX_MISMATCH")
        try:
            vectors = encode_texts(self.model, [c["text"] for c in document["chunks"]])
        except ValueError as error:
            raise RetrievalError("INPUT_TOO_LONG", str(error)) from error
        results = self.collection.query(query_embeddings=vectors.tolist(), n_results=len(self.controls), include=["distances"])
        return assemble_result(document, warnings, self.controls, results, top_k, self.kb_sha, self.cache_key)


def run_local(payload, top_k, compatibility):
    check(type(top_k) is int and 1 <= top_k <= 101, "top_k는 1~101의 정수여야 합니다.")
    prepare_input(payload, compatibility)  # 잘못된 입력은 모델을 로드하기 전에 거부합니다.
    return LocalDocumentRetriever().search(payload, top_k, compatibility)


def error_result(code, message):
    return {"schema_version": SCHEMA_VERSION, "success": False, "error": {"code": code, "message": message}}


def retrieve(payload, top_k=5, compatibility=False, timeout=600):
    """어느 작업 폴더에서도 호출 가능. 성공/실패 JSON 객체 반환. 호출마다 모델을 로드합니다."""
    python = ROOT / ".venv/Scripts/python.exe"
    if not python.is_file():
        return error_result("ENVIRONMENT_ERROR", "Search의 Python 환경이 없습니다. 01_setup.cmd를 실행하세요.")
    try:
        result = subprocess.run([str(python), "-X", "utf8", str(Path(__file__).resolve()), "--worker"],
            input=json.dumps({"payload": payload, "top_k": top_k, "compatibility": compatibility}, ensure_ascii=False, allow_nan=False),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=ROOT, timeout=timeout)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        answer = json.loads(result.stdout)
        check(isinstance(answer, dict) and isinstance(answer.get("success"), bool), "검색 프로세스의 응답이 잘못됐습니다.", "WORKER_FAILED")
        return answer
    except subprocess.TimeoutExpired:
        return error_result("TIMEOUT", "검색 시간이 제한을 초과했습니다.")
    except (OSError, ValueError, TypeError) as error:
        return error_result("WORKER_FAILED", str(error))


def write_result(path, result):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="증적 정보와 chunks가 있는 입력 JSON")
    parser.add_argument("--output", type=Path, help="결과 JSON 경로. 생략하면 표준 출력")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--compat-input", action="store_true", help="입력팀 현재 출력의 알려진 필드 차이를 경고와 함께 보정")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            request = json.load(sys.stdin)
            os.chdir(ROOT)
            with redirect_stdout(sys.stderr):
                result = run_local(request["payload"], request["top_k"], request["compatibility"])
        except RetrievalError as error:
            result = error_result(error.code, str(error))
        except Exception as error:
            result = error_result("SEARCH_FAILED", str(error))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    else:
        if args.input is None:
            parser.error("--input을 지정해 주세요.")
        if args.output and args.input.resolve() == args.output.resolve():
            parser.error("입력 파일과 출력 파일을 같은 경로로 지정할 수 없습니다.")
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
            result = retrieve(payload, args.top_k, args.compat_input)
        except (OSError, ValueError) as error:
            result = error_result("INVALID_INPUT_FILE", str(error))
        if args.output:
            write_result(args.output, result)
            print(f"{'PASS' if result['success'] else 'FAIL'}: {args.output.resolve()}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
