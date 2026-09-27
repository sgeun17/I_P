"""
pipeline.py : 업로드된 증적을 파싱 → 청킹 → 청크 DB 저장까지 이어줍니다. (팀장님 피드백 4번)

    python pipeline.py E0001        증적 하나만
    python pipeline.py --all        status 가 UPLOADED 인 것 전부
    python pipeline.py --all --retry 한 번 실패(FAILED)한 것까지 다시

흐름
    DB 에서 파일 경로 → parse_file()  → make_chunks() → validate_chunks() → save_chunks()
                         디스패처        청킹            규칙 검사          청크 DB

    status : UPLOADED → PREPROCESSING → PREPROCESSED   (잘 됨)
                                      → FAILED         (못 읽음. error_code 를 같이 남김)

왜 업로드(main.py) 안에서 바로 안 하나
    PNG·JPG 는 OCR 이 한 장에 10~30초라, 업로드 응답을 기다리는 브라우저가 먼저 끊깁니다.
    그래서 업로드는 저장까지만 하고, 파싱·청킹은 이 파일을 따로 돌립니다.

⚠ 마스킹을 하지 않습니다. chunk 표의 text 칸에 개인정보가 그대로 들어갑니다.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "database"))
sys.path.insert(0, str(HERE / "chunking"))

from chunk_format import validate_chunks          # noqa: E402
from chunk_store import save_chunks               # noqa: E402
from chunker import make_chunks                   # noqa: E402
from evidence_store import (get_evidence, get_file_path,  # noqa: E402
                            list_evidence, update_status)
from parser_dispatcher import parse_file          # noqa: E402

# 파서가 돌려주는 에러 코드 → 증적에 남길 에러 코드
ERROR_CODES = {
    "corrupted_file":  ("FILE_PARSE_FAILED", "파일을 읽지 못했습니다 (깨진 파일)."),
    "empty_document":  ("FILE_PARSE_FAILED", "파일에서 글자를 찾지 못했습니다."),
}


def process_one(evidence_id, verbose=True):
    """
    증적 하나를 파싱 → 청킹 → 청크 DB 저장까지 합니다.
    돌려주는 값: ("ok", 청크개수) 또는 ("failed", 이유)

    ★ 어떤 이유로 터져도 status 를 PREPROCESSING 에 남겨두지 않습니다.
      남겨두면 --all 이 그 증적을 영원히 건너뛰어서, 아무도 모르게 빠집니다.
    """
    def say(msg):
        if verbose:
            print(msg)

    info = get_evidence(evidence_id)
    file_type, version = info["file_type"], info["version"]
    say(f"[{evidence_id}] v{version} {file_type}  {info['file_name']}")

    update_status(evidence_id, "PREPROCESSING")
    try:
        path = get_file_path(evidence_id)
        parsed = parse_file(path, file_type)

        if parsed["errors"]:
            first = parsed["errors"][0]
            code, message = ERROR_CODES.get(first, ("FILE_PARSE_FAILED", f"파서 오류: {first}"))
            update_status(evidence_id, "FAILED", error_code=code, error_message=message)
            say(f"  ✗ FAILED  {first}")
            return "failed", first

        chunks = make_chunks(parsed, evidence_id, version)
        if not chunks:
            update_status(evidence_id, "FAILED", error_code="FILE_PARSE_FAILED",
                          error_message="글자는 읽었지만 만들 청크가 없습니다.")
            say("  ✗ FAILED  청크 0개")
            return "failed", "no_chunks"

        problems = validate_chunks(chunks)
        if problems:
            update_status(evidence_id, "FAILED", error_code="CHUNK_RULE_VIOLATION",
                          error_message=f"청크 규칙 위반 {len(problems)}건: {problems[0]}")
            say(f"  ✗ FAILED  청크 규칙 위반 {len(problems)}건")
            for p in problems[:3]:
                say(f"      {p}")
            return "failed", "chunk_rule"

        saved = save_chunks(chunks)                       # 옛 청크는 지우고 새로 넣습니다
        update_status(evidence_id, "PREPROCESSED")
        say(f"  ✓ PREPROCESSED  청크 {saved}개  (블록 {len(parsed['blocks'])}개)")
        return "ok", saved

    except Exception as e:                                # ★ 여기서 안 잡으면 상태가 멈춥니다
        update_status(evidence_id, "FAILED", error_code="FILE_PARSE_FAILED",
                      error_message=f"{type(e).__name__}: {e}")
        say(f"  ✗ FAILED  {type(e).__name__}: {e}")
        return "failed", type(e).__name__


def process_all(retry=False, verbose=True):
    """UPLOADED 인 증적을 전부 처리합니다. retry=True 면 FAILED 도 다시 시도합니다."""
    targets = []
    for status in (["UPLOADED", "FAILED"] if retry else ["UPLOADED"]):
        page = 1
        while True:
            got = list_evidence(status=status, page=page, size=100)
            targets += [i["evidence_id"] for i in got["items"]]
            if page * got["size"] >= got["total"]:
                break
            page += 1

    if not targets:
        print("처리할 증적이 없습니다. (status=UPLOADED)")
        return {"ok": 0, "failed": 0}

    print(f"대상 {len(targets)}개\n")
    ok = failed = 0
    for evidence_id in targets:
        result, _ = process_one(evidence_id, verbose)
        ok, failed = (ok + 1, failed) if result == "ok" else (ok, failed + 1)

    print(f"\n성공 {ok}개 / 실패 {failed}개")
    if failed:
        print("실패한 것은 화면 목록에서 FAILED 로 보이고, error_code 가 남아 있습니다.")
    return {"ok": ok, "failed": failed}


def main():
    args = [a for a in sys.argv[1:]]
    retry = "--retry" in args
    args = [a for a in args if a != "--retry"]

    if not args:
        print(__doc__.strip().split("\n\n")[1])           # 쓰는 법만 보여주기
        return
    if args[0] == "--all":
        process_all(retry=retry)
    else:
        for evidence_id in args:
            process_one(evidence_id)


if __name__ == "__main__":
    main()
