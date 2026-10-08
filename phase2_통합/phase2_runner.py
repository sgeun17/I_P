"""
phase2_runner.py : Phase 1 이 끝난 증적을 Phase 2 로 넘긴다

    from phase2_runner import run_evidence, run_pending

    run_evidence("E0001")     증적 하나를 Phase 2 까지 돌린다
    run_pending()             Phase 1 이 끝났고 아직 판정이 없는 증적을 모두 돌린다

하는 일
    ① Phase 1 결과와 청크를 읽는다
    ② Phase 2 입력으로 조립한다            (build_phase2_input.build)
    ③ 판정할 통제항목마다 Phase 2 를 돌린다 (validated_pipeline.run_control_judgment)
    ④ 결과를 DB 에 저장한다                 (phase2_result_store.save)
    ⑤ 못 돌린 항목은 사유와 함께 기록한다   (phase2_result_store.exclude)

evidence.status 는 ④ 안에서 다시 계산된다. 여기서 직접 건드리지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ── 저장소 안의 다른 폴더를 찾는다 ─────────────────────────────────────
_HERE = Path(__file__).resolve().parent


def _find_root(start: Path) -> Path:
    """
    phase1_* 과 phase2_* 폴더가 같이 있는 곳.

    glob 은 파일도 잡는다. phase2_통합 안에 phase1_mapping_store.py 가 있으면
    그 폴더가 뿌리로 잡혀서 아래 경로가 전부 어긋난다. 폴더만 센다.
    """
    def has(base, prefix):
        return any(p.is_dir() for p in base.glob(prefix))

    for base in [start, *start.parents]:
        if has(base, "phase1_*") and has(base, "phase2_*"):
            return base
    raise RuntimeError(f"저장소 뿌리를 찾지 못했습니다: {start}")


ROOT = _find_root(_HERE)
INTERFACE = ROOT / "phase2_인터페이스"
JUDGE = ROOT / "phase2_판단"
BASIS = ROOT / "phase2_기준"
SEARCH = ROOT / "phase1_검색"
INPUT_DB = ROOT / "phase1_입력" / "database"
RESULT_DIR = ROOT / "phase1_통합" / "results"

for _p in (_HERE, INTERFACE, JUDGE, JUDGE / "src", SEARCH, INPUT_DB):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── 기본값 ────────────────────────────────────────────────────────────
DEFAULT_MODEL = "qwen3:14b"
DEFAULT_CHECKLIST = BASIS / "full_checklist_draft.json"
DEFAULT_REASON_CODES = BASIS / "full_reason_codes_draft.json"
CONTROLS = SEARCH / "controls.json"

# Phase 1 이 여기서 멈춘 증적은 Phase 2 를 돌리지 않는다
PHASE1_STOP = {
    "FAILED": ("FAILED", "Phase 1 이 실패했다"),
    "NO_MATCH": ("SKIP_NO_MATCH", "Phase 1 이 관련 통제항목을 찾지 못했다"),
}

# Phase 2 입력 스키마가 받는 청크 칸. 입력팀 chunk 표는 13칸이고
# evidence_id·version·block_orders 는 스키마에 없다 (additionalProperties: false).
CHUNK_FIELDS = ("chunk_id", "chunk_index", "chunk_type", "text", "source",
                "file_type", "source_file", "page_start", "page_end", "heading")

# Phase 1 이 끝났으나 Phase 2 가 아직 안 돈 증적의 evidence.status.
# VALIDATING 이 정규 자리다. 판단팀 to_evidence_status() 가 그걸 안 써서
# COMPLETED·REVIEW_REQUIRED 로 들어오는 것까지 같이 본다.
# orchestrator 가 from_phase1(..., phase2_enabled=True) 로 바뀌면 뒤의 둘은 빼도 된다.
PENDING_STATUS = ("VALIDATING", "COMPLETED", "REVIEW_REQUIRED")

# Phase 1 사람 검토가 열린 증적(REVIEW_REQUIRED)까지 돌릴지.
# 기본은 돌린다 — 판정 결과에 phase1_review_required 가 붙고 승인 보류 사유
# PHASE1_REVIEW_OPEN 이 달려서 운영 승인으로 새지 않는다. 검토자가 Phase 1 과
# Phase 2 를 같이 보고 판단하는 게 낫다고 봤다.
# LLM 시간을 아끼려면 run_pending(status=("VALIDATING",)) 로 좁히면 된다.
PHASE1_ONLY_STATUS = ("VALIDATING",)

# 매핑을 DB 에 못 올리면 판정도 멈출지. 기본은 멈춘다 (fail-closed).
# 매핑이 안 올라간 채 판정만 쌓이면, 매핑이 바뀌어도 옛 판정을 무효화할
# 길이 없어서 틀린 결과가 현재 값으로 남는다.
# phase1_mapping.sql 을 아직 안 돌렸으면 False 로 두고 쓸 수 있다.
REQUIRE_MAPPING = True


class RunnerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_controls(path=CONTROLS) -> dict:
    """통제항목 이름 표 (control_id → control_name). controls.json 은 배열이다."""
    raw = _read(path)
    rows = raw if isinstance(raw, list) else raw["controls"]
    return {r["control_id"]: r["control_name"] for r in rows}


# ── 입력 모으기 ───────────────────────────────────────────────────────
def load_phase1_result(evidence_id: str, version: int | None = None):
    """Phase 1 결과 JSON. 통합팀 orchestrator 가 results/ 에 저장한 것을 읽는다."""
    if version is not None:
        p = RESULT_DIR / f"{evidence_id}_v{version}.json"
        if not p.is_file():
            raise RunnerError("PHASE1_RESULT_NOT_FOUND", f"Phase 1 결과가 없습니다: {p}")
        return _read(p)
    hits = sorted(RESULT_DIR.glob(f"{evidence_id}_v*.json"),
                  key=lambda f: int(f.stem.rsplit("_v", 1)[1]))
    if not hits:
        raise RunnerError("PHASE1_RESULT_NOT_FOUND",
                          f"Phase 1 결과가 없습니다: {evidence_id}")
    return _read(hits[-1])


def to_chunk(row: dict) -> dict:
    """입력팀 chunk 한 줄을 Phase 2 입력 스키마 모양으로 줄인다."""
    return {k: row[k] for k in CHUNK_FIELDS if k in row}


def load_chunks(evidence_id: str, version: int | None = None):
    """입력팀 chunk 표에서 그 증적의 청크를 읽는다."""
    from chunk_store import get_chunks
    rows = get_chunks(evidence_id, version)
    if not rows:
        raise RunnerError("NO_CHUNKS", f"청크가 없습니다: {evidence_id}")
    return [to_chunk(r) for r in rows]


# ── 한 증적 ───────────────────────────────────────────────────────────
def run_evidence(evidence_id: str, version: int | None = None, *,
                 phase1_result: dict | None = None,
                 chunks: list | None = None,
                 model: str = DEFAULT_MODEL,
                 checklist_path=DEFAULT_CHECKLIST,
                 reason_codes_path=DEFAULT_REASON_CODES,
                 allow_draft: bool = True,
                 as_of: str | None = None,
                 only_missing: bool = False,
                 progress=None) -> dict:
    """
    증적 하나를 Phase 2 까지 돌리고 결과를 DB 에 저장한다.

    phase1_result·chunks 를 넘기면 그걸 쓰고, 안 넘기면 파일·DB 에서 읽는다.

    only_missing 이면 **현재 판정이 없는 통제항목만** 돌린다. 매핑이 일부만
    바뀌었을 때 안 바뀐 통제항목의 판정(사람이 고쳐둔 값 포함)을 LLM 이
    덮어쓰지 않게 한다. run_pending 은 이걸 켜고 부른다.
    돌려주는 값
        {evidence_id, version, judged, excluded, failed, results[], skipped[]}
    """
    import phase2_result_store as store

    p1 = phase1_result or load_phase1_result(evidence_id, version)
    ver = p1["version"]
    review = p1.get("human_review") or {}
    row = {"phase1_review_required": bool(review.get("required")),
           "phase1_review_reasons": [
               (r.get("code") if isinstance(r, dict) else str(r))
               for r in (review.get("reasons") or [])]}

    # 매핑부터 DB 에 올린다. orchestrator 는 results/*.json 에만 쓰기 때문에,
    # 여기서 안 올리면 매핑만 파일에 남아 조회가 안 된다.
    # 판정보다 먼저 한다 — 뒤에 두면 체크리스트·LLM 쪽이 하나라도 깨졌을 때
    # 매핑까지 같이 안 올라간다. 표가 없으면 조용히 넘어간다.
    mapping = {"saved": False, "error": None}
    try:
        import phase1_mapping_store
        m = phase1_mapping_store.save(p1)
        mapping = {"saved": True, "error": None, "count": m["saved"],
                   "primary_changed": m["changed"],
                   "superseded": m["superseded"]}
        if m["superseded"] and progress:
            progress(0, 0, evidence_id,
                     f"매핑이 바뀌어 기존 판정 {len(m['superseded'])}건을 과거로 돌림")
    except Exception as exc:
        mapping = {"saved": False, "error": f"{type(exc).__name__}: {exc}"}
        if progress:
            progress(0, 0, evidence_id, f"매핑 저장 실패 — {mapping['error']}")
        if REQUIRE_MAPPING:
            # 매핑 없이 판정만 쌓으면 나중에 매핑이 바뀌어도 옛 판정을
            # 무효화할 수 없다. 여기서 멈추는 게 안전하다.
            raise RunnerError(
                "MAPPING_PERSIST_FAILED",
                f"Phase 1 매핑을 DB 에 올리지 못했습니다: {mapping['error']}\n"
                f"phase1_mapping.sql 을 돌렸는지 확인하세요. 매핑 없이 판정만 "
                f"진행하려면 phase2_runner.REQUIRE_MAPPING = False 로 두세요.")

    # 판정 쪽 모듈은 여기서 읽는다. 위의 매핑 저장이 이 import 에 안 걸리게 한다.
    import build_phase2_input as builder
    from kb_identity import kb_sha256
    from validated_pipeline import run_control_judgment, validate_input

    stop = (PHASE1_STOP.get(p1.get("processing_status"))
            or PHASE1_STOP.get(p1.get("match_status")))
    if stop:
        store.exclude(evidence_id, stop[0], stop[1])
        return {"evidence_id": evidence_id, "version": ver, "judged": 0,
                "excluded": 1, "failed": 0, "results": [],
                "skipped": [{"control_id": None, "reason_code": stop[0]}],
                "mapping": mapping}

    chunk_rows = ([to_chunk(c) for c in chunks] if chunks is not None
                  else load_chunks(evidence_id, ver))

    catalog = _read(checklist_path)
    reason_catalog = _read(reason_codes_path)
    scope, checklist_version = builder.load_checklist_scope(checklist_path)
    controls = load_controls()
    controls_sha256 = kb_sha256(Path(CONTROLS).read_bytes())

    payload = builder.build(p1, chunk_rows, scope, checklist_version)
    targets = builder.judge_targets(payload)

    # 질문지가 없어 못 도는 항목은 사유와 함께 남긴다
    skipped = []
    for t in payload["targets"]:
        if t["judge"] and not t["checklist_in_scope"]:
            store.exclude(evidence_id, "NO_CHECKLIST",
                          f"{t['control_id']} 질문지가 없습니다",
                          control_id=t["control_id"])
            skipped.append({"control_id": t["control_id"],
                            "reason_code": "NO_CHECKLIST"})

    # PRIMARY 매핑이 하나도 없으면 판정할 게 없다. 조회에서 사라지지 않게 적어둔다.
    if not targets and not skipped:
        store.exclude(evidence_id, "SKIP_NO_MATCH", "PRIMARY 매핑이 없습니다")
        return {"evidence_id": evidence_id, "version": ver, "judged": 0,
                "excluded": 1, "failed": 0, "results": [],
                "skipped": [{"control_id": None, "reason_code": "SKIP_NO_MATCH"}],
                "mapping": mapping}

    # 입력이 규격에 안 맞으면 판단팀 파이프라인은 통제항목마다 FAILED 를 돌려준다.
    # 101번 돌리고 전부 실패하기 전에 한 번만 보고 멈춘다.
    bad = [i for i in validate_input(payload, catalog, controls)
           if i.get("severity") != "warning"]
    if not bad and payload.get("source_versions", {}).get("kb_sha256") != controls_sha256:
        bad = [{"code": "P2E005", "message": "controls 파일 해시와 Phase 1 입력 해시 불일치"}]
    if bad:
        store.exclude(evidence_id, "HOLD_PHASE1_INVALID",
                      "; ".join(f"{i['code']} {i['message']}" for i in bad[:3]))
        return {"evidence_id": evidence_id, "version": ver, "judged": 0,
                "excluded": 1, "failed": 0, "results": [],
                "skipped": [{"control_id": None,
                             "reason_code": "HOLD_PHASE1_INVALID"}],
                "issues": bad, "mapping": mapping}

    if only_missing:
        # 버전을 못 박는다. 안 그러면 v1 결과를 보고 v2 를 이미 했다고 착각한다.
        done = {r["control_id"] for r in store.by_evidence(evidence_id, version=ver)}
        skipped_done = [t["control_id"] for t in targets
                        if t["control_id"] in done]
        targets = [t for t in targets if t["control_id"] not in done]
        if skipped_done and progress:
            progress(0, 0, evidence_id,
                     f"이미 판정된 {len(skipped_done)}건은 건너뜀")

    approved = (catalog.get("approved") is True
                and reason_catalog.get("approved") is True)
    results, failed = [], 0
    for i, t in enumerate(targets, 1):
        cid = t["control_id"]
        if progress:
            progress(i, len(targets), evidence_id, cid)
        out = run_control_judgment(
            payload, cid,
            catalog=catalog, reason_catalog=reason_catalog, controls=controls,
            model=model, allow_draft=allow_draft,
            critical_policy={"mode": "explicit"},
            as_of=as_of, controls_sha256=controls_sha256)
        if out["processing_status"] == "FAILED" or not out.get("output"):
            failed += 1
            # 실패도 DB 에 남긴다. 안 남기면 화면에서 그 통제항목이 그냥 사라진다.
            codes = [i.get("code") for i in
                     ((out.get("audit") or {}).get("issues") or [])
                     if i.get("severity") != "warning"]
            store.exclude(evidence_id, "FAILED",
                          f"{cid} 판정 실패" + (f" ({', '.join(codes[:3])})" if codes else ""),
                          control_id=cid)
            results.append({"control_id": cid, "processing_status": "FAILED",
                            "result_id": None, "error_codes": codes})
            continue
        # 전에 실패로 적어둔 게 있으면 지운다 (다시 돌려서 성공한 경우)
        store.unexclude(evidence_id, cid)
        result_id = store.save(out["output"], row=row, audit=out.get("audit"),
                               checklist_approved=approved)
        results.append({"control_id": cid,
                        "processing_status": out["processing_status"],
                        "result_id": result_id,
                        "overall_result": out["output"]["overall_result"]})

    judged = len(results) - failed
    # 대상이 전부 제외·실패해서 남은 판정이 하나도 없으면 증적 단위로도 적는다.
    # 안 적으면 통제항목 단위 기록만 남아 run_pending 이 매번 이 증적을 다시 집는다.
    if judged == 0:
        store.exclude(evidence_id, "ALL_TARGETS_UNRESOLVED",
                      f"판정 대상 {len(targets)}건이 전부 제외·실패했다", version=ver)
    else:
        store.unexclude(evidence_id, version=ver)

    return {"evidence_id": evidence_id, "version": ver,
            "judged": judged, "excluded": len(skipped),
            "failed": failed, "results": results, "skipped": skipped,
            "mapping": mapping}


# ── 아직 Phase 2 가 안 돈 증적 전부 ───────────────────────────────────
def pending_evidence_ids(status=PENDING_STATUS, *, retry_failed: bool = False):
    """
    Phase 1 은 끝났고 Phase 2 를 아직 안 돌린 증적 번호.

    빼는 것 — **지금 버전**의 판정이 이미 있거나, **지금 버전**에서 증적 전체가
    제외된 것(control_id=''). 버전을 같이 보므로 재업로드(v2)는 다시 집는다.

    retry_failed 면 FAILED·ALL_TARGETS_UNRESOLVED 로 제외된 것도 다시 집는다.
    고치고 나서 다시 돌릴 때 쓴다.
    """
    import phase2_result_store as store
    if isinstance(status, str):
        status = (status,)
    marks = ", ".join(["%s"] * len(status))
    args = list(status)

    # 기본은 증적 단위 제외가 있으면 안 집는다.
    # retry_failed 면 고치면 다시 될 수 있는 사유(FAILED 등)만 무시하고 다시 집는다.
    reason_cond = ""
    if retry_failed:
        reason_cond = ("AND x.reason_code NOT IN ("
                       + ", ".join(["%s"] * len(store.RETRYABLE_REASONS)) + ")")

    conn = store._connect()
    try:
        with conn.cursor() as cur:
            # 지금 PRIMARY 중 판정이 없는 게 하나라도 있으면 집는다.
            # 매핑을 모르면 예전 규칙 — 현재 판정이 아예 없을 때만 집는다.
            # 매핑 일부만 바뀐 경우(2.5.1 빠지고 2.5.2 생김)에 2.5.2 만 돌리려면
            # 이 조건이어야 한다. "판정이 하나라도 있으면 제외" 로는 못 집는다.
            missing = (
                "AND ( EXISTS (SELECT 1 FROM phase1_mapping m "
                "       WHERE m.evidence_id = e.evidence_id "
                "       AND m.evidence_version = e.version AND m.relation = 'PRIMARY' "
                "       AND NOT EXISTS (SELECT 1 FROM phase2_result r "
                "         WHERE r.evidence_id = e.evidence_id "
                "         AND r.evidence_version = e.version "
                "         AND r.control_id = m.control_id AND r.superseded_at IS NULL)) "
                "   OR (NOT EXISTS (SELECT 1 FROM phase1_mapping m2 "
                "         WHERE m2.evidence_id = e.evidence_id "
                "         AND m2.evidence_version = e.version) "
                "       AND NOT EXISTS (SELECT 1 FROM phase2_result r2 "
                "         WHERE r2.evidence_id = e.evidence_id "
                "         AND r2.evidence_version = e.version "
                "         AND r2.superseded_at IS NULL)) ) ")
            fallback = ("AND NOT EXISTS (SELECT 1 FROM phase2_result r "
                        "  WHERE r.evidence_id = e.evidence_id "
                        "  AND r.evidence_version = e.version "
                        "  AND r.superseded_at IS NULL) ")
            sql = (f"SELECT e.evidence_id FROM evidence e "
                   f"WHERE e.status IN ({marks}) "
                   f"{{missing}}"
                   # 지금 버전에서 증적 전체가 제외됐으면 안 집는다.
                   f"AND NOT EXISTS (SELECT 1 FROM phase2_excluded x "
                   f"  WHERE x.evidence_id = e.evidence_id "
                   f"  AND x.evidence_version = e.version AND x.control_id = '' "
                   f"  {reason_cond}) "
                   f"ORDER BY e.evidence_id")
            if retry_failed:
                args += list(store.RETRYABLE_REASONS)
            try:
                cur.execute(store._q(sql.replace("{missing}", missing)), tuple(args))
                rows = store._rows(cur)
            except Exception as exc:
                # phase1_mapping 표가 아직 없을 때만 옛 규칙으로 돌아간다.
                # 아무 오류나 삼키면 컬럼명 오타·스키마 불일치까지 숨는다.
                msg = str(exc).lower()
                if not any(k in msg for k in
                           ("no such table", "doesn't exist", "does not exist",
                            "1146", "undefinedtable")):
                    raise
                cur.execute(store._q(sql.replace("{missing}", fallback)), tuple(args))
                rows = store._rows(cur)
    finally:
        conn.close()
    return [r["evidence_id"] for r in rows]


def rejudge(evidence_id: str, version: int | None = None, **kwargs) -> dict:
    """
    같은 증적을 LLM 으로 다시 판정한다. 자동으로는 아무도 안 부른다.

    사람이 고친 값이 최종이라, 검토에서 REVALIDATING·REJECTED 가 된 판정을
    runner 가 알아서 다시 돌리지 않는다. 다시 돌리면 지금 줄이 superseded 되고
    사람이 고친 값은 그 줄에 남아 과거가 된다 — 화면에 보이는 값이 LLM 원본으로
    되돌아간다. 그래도 다시 돌려야 할 때만 사람이 직접 부른다.

    반려(REJECTED)는 보통 증적을 다시 올리라는 뜻이다. 다시 올리면 version 이
    올라가고 Phase 1 부터 새로 도므로 이 함수가 아니라 run_evidence 가 맡는다.
    """
    return run_evidence(evidence_id, version, **kwargs)


def run_pending(*, model: str = DEFAULT_MODEL, limit: int | None = None,
                status=PENDING_STATUS, retry_failed: bool = False,
                evidence_ids=None, progress=None, **kwargs) -> dict:
    """
    Phase 2 를 기다리는 증적을 모두 돌린다.
    하나가 실패해도 멈추지 않는다. 실패한 증적은 errors 에 남고 DB 에도 적힌다.

    retry_failed 면 지난번에 실패·전부제외로 끝난 증적도 다시 집는다.
    evidence_ids 를 주면 **대기 목록에서 그것만 골라** 돌린다. 찾는 건 똑같이
    runner 가 하고 실행 대상만 좁히는 것이라, 시연에서 다른 증적까지 같이
    돌아버리는 걸 막을 때 쓴다.
    """
    import phase2_result_store as store
    ids = pending_evidence_ids(status, retry_failed=retry_failed)
    if evidence_ids is not None:
        want = set(evidence_ids)
        ids = [e for e in ids if e in want]
    if limit:
        ids = ids[:limit]
    done, errors = [], []
    for eid in ids:
        try:
            done.append(run_evidence(eid, model=model, only_missing=True,
                                     progress=progress, **kwargs))
        except Exception as exc:
            errors.append({"evidence_id": eid, "error": type(exc).__name__,
                           "message": str(exc)})
            # 터진 것도 DB 에 남긴다. 안 남기면 다음에 또 집어서 또 터진다.
            try:
                store.exclude(eid, "FAILED",
                              f"{type(exc).__name__}: {str(exc)[:200]}")
            except Exception:
                pass          # DB 가 죽어서 터진 경우엔 적을 수도 없다
    return {"evidence_total": len(ids), "done": len(done), "error": len(errors),
            "results": done, "errors": errors}


# ── 터미널에서 ────────────────────────────────────────────────────────
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 1 이 끝난 증적을 Phase 2 로 넘긴다")
    ap.add_argument("evidence_id", nargs="?", help="안 주면 대기 중인 증적 전부")
    ap.add_argument("--version", type=int)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--checklist", default=str(DEFAULT_CHECKLIST))
    ap.add_argument("--reason-codes", default=str(DEFAULT_REASON_CODES))
    ap.add_argument("--as-of")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)

    def show(n, total, eid, cid):
        print(f"  [{n}/{total}] {eid} {cid}", flush=True)

    kw = dict(model=a.model, checklist_path=a.checklist,
              reason_codes_path=a.reason_codes, as_of=a.as_of, progress=show)
    if a.evidence_id:
        r = run_evidence(a.evidence_id, a.version, **kw)
        print(f"{r['evidence_id']}  판정 {r['judged']} / 제외 {r['excluded']} "
              f"/ 실패 {r['failed']}")
        if not r["mapping"]["saved"]:
            print(f"  매핑 DB 저장 실패 — {r['mapping']['error']}")
        return 1 if r["failed"] else 0
    r = run_pending(limit=a.limit, **kw)
    print(f"증적 {r['evidence_total']}건  성공 {r['done']} / 오류 {r['error']}")
    for e in r["errors"]:
        print(f"  X {e['evidence_id']}: {e['error']} {e['message']}")
    return 1 if r["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
