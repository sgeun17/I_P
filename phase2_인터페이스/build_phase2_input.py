"""Phase 1 최종 결과 → Phase 2 입력 조립 (judge_policy = primary_only 확정).

Phase 1 이 내보내는 값만 쓴다. 새로 만드는 값은 judge·checklist_in_scope 두 개뿐이고,
둘 다 다른 값에서 기계적으로 계산한다.
"""
import json

SCHEMA_VERSION = "phase2-input-0.2"


def load_checklist_scope(path):
    """체크리스트에 질문지가 있는 통제항목 ID 집합."""
    d = json.load(open(path, encoding="utf-8"))
    return {c["control_id"] for c in d.get("controls", [])}, d.get("draft_version")


def load_control_names(path):
    """
    체크리스트의 통제항목 이름 표 (control_id → control_name).

    Phase 1 의 MappedControl 은 control_name 을 들고 있으므로 보통 필요 없다.
    실행 기록(runs/*.jsonl)처럼 LLM 원본 응답만 있는 자료로 시험할 때 쓴다.
    """
    d = json.load(open(path, encoding="utf-8"))
    return {c["control_id"]: c["control_name"] for c in d.get("controls", [])
            if c.get("control_name")}


def sort_chunks(chunks):
    """
    chunk_index 오름차순. 입력 스키마가 chunk_index 를 required 로 두므로
    빠진 청크는 입력이 잘못된 것이다 — KeyError 로 드러낸다.
    """
    return sorted(chunks, key=lambda c: c["chunk_index"])


def build(phase1_result, chunks, checklist_scope, checklist_version=None,
          judge_policy="primary_only"):
    """
    phase1_result : Phase1MappingResult (dict)
    chunks        : 그 증적의 청크 목록 (입력팀 chunk 표 모양)
    """
    # ★ 정책을 먼저 검사한다. 오타가 들어오면 조용히 전부 judge=true 가 돼서
    #   RELATED 까지 다 판정해 버린다 (시간 1.5배 + 검토 폭증). 모르는 값은 막는다.
    if judge_policy != "primary_only":
        raise ValueError(
            f"judge_policy 는 primary_only 로 확정됐다 (받은 값: {judge_policy!r}). "
            "넓히려면 ISSUES #2 를 먼저 다시 열어야 한다.")

    targets = []
    for m in phase1_result.get("mapped_controls", []):
        # ★ 없는 값을 메우지 않는다. 인터페이스가 상위 단계 오류를 보정하면
        #   Phase 1 의 버그가 조용히 '정상 입력'으로 바뀌어 내려간다.
        #   relation 이 빠진 걸 RELATED 로 메우면 판정 대상이 사라지고,
        #   control_name 을 control_id 로 메우면 화면에 '2.5.1' 이 이름으로 뜬다.
        relation = m["relation"]
        judge = (relation == "PRIMARY")
        t = {
            "control_id": m["control_id"],
            "control_name": m["control_name"],
            "relation": relation,
            "judge": judge,
            "checklist_in_scope": m["control_id"] in checklist_scope,
        }
        if m.get("llm_confidence") is not None:
            t["llm_confidence"] = m["llm_confidence"]
        if m.get("similarity_score") is not None:
            t["similarity_score"] = m["similarity_score"]
        if m.get("citations"):
            t["mapping_citations"] = [
                {"chunk_id": c["chunk_id"], "page": c.get("page"), "quote": c["quote"]}
                for c in m["citations"]]
        targets.append(t)

    out = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": phase1_result["evidence_id"],
        "version": phase1_result["version"],
        "judge_policy": judge_policy,
        "targets": targets,
        # ★ chunk_index 오름차순으로 정렬해서 넣는다.
        #   phase2_판단 context_builder.normalize_chunks() 가 enumerate(chunks) 로
        #   배열 순서를 그대로 문서 순서로 쓴다. DB·파일에서 읽은 순서를 그대로 넘기면
        #   앞뒤 문맥 청크가 엉뚱하게 붙는다.
        "chunks": sort_chunks(chunks),
        # ★ 가짜 해시를 만들지 않는다. "0"*64 을 넣으면 '값이 없음' 과
        #   '실제 해시가 0...0' 을 구분할 수 없게 되고, 재현성 추적이 무의미해진다.
        "source_versions": phase1_result["versions"],
    }
    if checklist_version:
        out["checklist_version"] = checklist_version
    if phase1_result.get("trace_id"):
        out["trace_id"] = phase1_result["trace_id"]
    return out


def judge_targets(phase2_input):
    """
    실제로 질문지를 돌릴 대상. judge 이면서 질문지가 있는 것만.

    checklist_in_scope 는 입력 스키마 required 다. .get() 으로 받으면
    키가 빠진 대상이 조용히 '질문지 없음' 으로 걸러져 판정에서 사라진다.
    빠졌으면 입력이 잘못된 것이므로 KeyError 로 드러낸다.
    """
    return [t for t in phase2_input["targets"]
            if t["judge"] and t["checklist_in_scope"]]
