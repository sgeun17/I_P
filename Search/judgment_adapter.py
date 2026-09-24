"""Retriever 0.2 출력 → 판단팀 MappingInput 변환. 판단·LLM 호출은 수행하지 않습니다."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from chunk_retriever import ROOT, prepare_input, RetrievalError, write_result

JUDGMENT = ROOT.parent / "판단"


class MappingAdapterError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MappingAdapterError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def to_mapping_input(search_result, judgment_dir=JUDGMENT):
    """성공 검색 결과만 변환. 본문·ID·순위·점수 보존, 불일치는 수정하지 않고 거부."""
    judgment_dir = Path(judgment_dir)
    require(isinstance(search_result, dict) and search_result.get("success") is True,
            "검색 실패 결과는 판단 입력으로 보낼 수 없습니다.")
    errors = list(Draft202012Validator(read(ROOT / "schemas/chunk_output.schema.json"),
                                      format_checker=FormatChecker()).iter_errors(search_result))
    require(not errors, f"검색 출력 규격 불일치: {errors[0].message if errors else ''}")
    result = deepcopy(search_result)
    envelope = {k: result[k] for k in ("evidence_id", "version", "source_file", "file_type")}
    envelope["chunks"] = result["evidence_chunks"]
    try:
        prepare_input(envelope)
    except RetrievalError as e:
        raise MappingAdapterError(str(e)) from e
    for c in envelope["chunks"]:
        expected = f"{envelope['evidence_id']}_v{envelope['version']}_c{c['chunk_index']:04d}"
        require(c["chunk_id"] == expected, f"판단팀 청크 ID 규칙 불일치: {c['chunk_id']} (기대 {expected})")
    kb_raw = (ROOT / "controls.json").read_bytes()
    kb_sha = hashlib.sha256(kb_raw).hexdigest()
    index = read(judgment_dir / "data/control_index.json")
    controls = {c["control_id"]: c for c in json.loads(kb_raw)}
    require(result["index"]["kb_sha256"] == kb_sha == index["kb_sha256"], "검색 결과·현재 KB·판단팀 KB 해시가 다릅니다.")
    require(index["count"] == len(controls) == len(index["controls"]) == 101
            and index["controls"] == {cid: c["control_name"] for cid, c in controls.items()}, "판단팀 KB의 ID·명칭 목록이 다릅니다.")
    candidates = result["retrieval"]["candidates"]
    ids = [c["control_id"] for c in candidates]
    require(len(ids) == len(set(ids)) == result["retrieval"]["top_k"], "후보 개수·중복·top_k 불일치")
    require([c["rank"] for c in candidates] == list(range(1, len(ids)+1)), "후보 순위가 연속적이지 않습니다.")
    scores = [c["similarity_score"] for c in candidates]
    require(scores == sorted(scores, reverse=True), "후보 유사도 순서가 잘못됐습니다.")
    require([c["control_id"] for c in result["candidate_controls"]] == ids, "후보와 요구사항 ID·순서 불일치")
    chunks = {c["chunk_id"] for c in envelope["chunks"]}
    mapped_candidates = []
    for candidate, detail in zip(candidates, result["candidate_controls"]):
        cid = candidate["control_id"]
        require(cid in controls, f"KB에 없는 후보: {cid}")
        require(candidate["control_name"] == detail["control_name"] == controls[cid]["control_name"]
                and detail["requirement"] == controls[cid]["requirement"], f"KB 명칭·요구사항 불일치: {cid}")
        require(candidate["matched_chunk_ids"] == detail["matched_chunk_ids"]
                and set(candidate["matched_chunk_ids"]) <= chunks
                and candidate["best_chunk_id"] in candidate["matched_chunk_ids"], f"후보의 원문 청크 연결 불일치: {cid}")
        mapped_candidates.append({k: candidate[k] for k in ("rank", "control_id", "control_name", "similarity_score")}
                                 | {"requirement": detail["requirement"], "source_chunk_ids": candidate["matched_chunk_ids"]})
    payload = {"evidence_id": result["evidence_id"], "version": result["version"],
               "chunks": result["evidence_chunks"], "candidate_controls": mapped_candidates,
               "top_k": result["retrieval"]["top_k"], "kb_sha256": kb_sha}
    # 원본 검색 출력에는 revision이 없으므로 현재 설정으로 과거 실행 정보를 추정하지 않습니다.
    # 해당 필드는 판단팀에서 선택 사항이며, 실제 검색 시 revision이 기록되면 전달할 수 있습니다.
    errors = list(Draft202012Validator(read(judgment_dir / "schemas/phase1_mapping_input.schema.json")).iter_errors(payload))
    require(not errors, f"판단팀 입력 규격 불일치: {errors[0].message if errors else ''}")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("원본 검색 출력과 판단 입력 저장 경로는 달라야 합니다.")
    try:
        payload = to_mapping_input(read(args.input))
    except (MappingAdapterError, OSError, ValueError) as e:
        write_result(args.output, {"success": False, "error": {"code": "MAPPING_INPUT_REJECTED", "message": str(e)}})
        print(f"FAIL: {e}")
        return 1
    write_result(args.output, payload)
    print(f"PASS: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
