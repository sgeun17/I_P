"""Phase 1 최종 결과 → Phase 2 입력 조립 (judge_policy=primary_only 임시 규칙).

Phase 1 이 내보내는 값만 쓴다. 새로 만드는 값은 judge·checklist_in_scope 두 개뿐이고,
둘 다 다른 값에서 기계적으로 계산한다.
"""
import json

SCHEMA_VERSION = "phase2-input-0.2"


def load_checklist_scope(path):
    """체크리스트에 질문지가 있는 통제항목 ID 집합."""
    d = json.load(open(path, encoding="utf-8"))
    return {c["control_id"] for c in d.get("controls", [])}, d.get("draft_version")


def build(phase1_result, chunks, checklist_scope, checklist_version=None,
          judge_policy="primary_only"):
    """
    phase1_result : Phase1MappingResult (dict)
    chunks        : 그 증적의 청크 목록 (입력팀 chunk 표 모양)
    """
    targets = []
    for m in phase1_result.get("mapped_controls", []):
        relation = m.get("relation", "RELATED")
        judge = (relation == "PRIMARY") if judge_policy == "primary_only" else True
        t = {
            "control_id": m["control_id"],
            "control_name": m.get("control_name") or m["control_id"],
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
        "chunks": chunks,
        "source_versions": phase1_result.get("versions") or {"kb_sha256": "0" * 64},
    }
    if checklist_version:
        out["checklist_version"] = checklist_version
    if phase1_result.get("trace_id"):
        out["trace_id"] = phase1_result["trace_id"]
    return out


def judge_targets(phase2_input):
    """실제로 질문지를 돌릴 대상. judge 이면서 질문지가 있는 것만."""
    return [t for t in phase2_input["targets"]
            if t["judge"] and t.get("checklist_in_scope")]
