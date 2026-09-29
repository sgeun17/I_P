"""Review-only reason code lookup and validation; never judges evidence.

The catalog and examples are unapproved proposals. Explicit ``allow_draft``
is required. Phase1, team API contracts and operational judgments are untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "reason_codes_draft.json"
DEFAULT_EXAMPLES = HERE / "reason_code_examples.json"
RESULTS = {"MET", "NOT_MET", "UNKNOWN"}
SOURCE_NAMES = {"checklist_draft.json", "review_examples.json"}


class ReasonCodeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _require(passed, code, message):
    if not passed:
        raise ReasonCodeError(code, message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _object(value, code="INVALID_INPUT"):
    _require(isinstance(value, dict), code, "JSON 객체가 필요합니다.")
    return value


def _array(value, code="INVALID_INPUT"):
    _require(isinstance(value, list), code, "JSON 배열이 필요합니다.")
    return value


def _read(path):
    try:
        content = Path(path).read_bytes()
    except (OSError, TypeError, ValueError) as exc:
        raise ReasonCodeError("SOURCE_UNAVAILABLE", f"파일을 읽을 수 없습니다: {path}") from exc
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ReasonCodeError("INVALID_JSON", f"유효한 UTF-8 JSON이 아닙니다: {path}") from exc
    return _object(value, "INVALID_JSON"), hashlib.sha256(content).hexdigest()


class ReasonCatalog:
    def __init__(self, catalog_path=None):
        self.path = Path(catalog_path) if catalog_path is not None else DEFAULT_CATALOG
        self._data, self.sha256 = _read(self.path)
        self._validate_catalog()
        self._sources = {}
        self._source_hashes = {}
        for source in self._data["source_files"]:
            path = self.path.parent / source["path"]
            value, actual_hash = _read(path)
            _require(actual_hash == source["sha256"], "SOURCE_CHANGED", f"원본 해시가 다릅니다: {source['path']}")
            self._sources[source["path"]] = value
            self._source_hashes[source["path"]] = actual_hash
        checklist = self._sources["checklist_draft.json"]
        _require(checklist.get("draft_version") == self._data["checklist_version"], "SOURCE_CHANGED", "체크리스트 버전이 다릅니다.")
        _require(checklist.get("approved") is False and checklist.get("status") == "DRAFT_FOR_TEAM_REVIEW", "INVALID_SOURCE", "체크리스트는 미승인 검수용 초안이어야 합니다.")

    def _validate_catalog(self):
        data = self._data
        _require(_text(data.get("catalog_version")) and _text(data.get("checklist_version")), "INVALID_CATALOG", "카탈로그·체크리스트 버전이 필요합니다.")
        _require(data.get("approved") is False and data.get("status") == "DRAFT_FOR_TEAM_REVIEW", "INVALID_CATALOG", "이 모듈은 팀 미승인 검수용 초안만 처리합니다.")
        metadata = _object(data.get("metadata"), "INVALID_CATALOG")
        _require(metadata.get("review_only") is True and metadata.get("human_approved") is False and metadata.get("llm_executed") is False and metadata.get("actual_evidence_used") is False, "INVALID_CATALOG", "검수용·미승인·미실행 상태를 유지해야 합니다.")
        values = _array(data.get("result_values"), "INVALID_CATALOG")
        _require(len(values) == 3 and all(isinstance(x, str) for x in values) and set(values) == RESULTS, "INVALID_CATALOG", "제안 결과 값은 MET·NOT_MET·UNKNOWN입니다.")
        rules = _object(data.get("assignment_rules"), "INVALID_CATALOG")
        _require(rules.get("met_reason_codes") == [] and rules.get("not_met_requires_direct_confirmation") is True and rules.get("evidence_missing_is_not_failure") is True and rules.get("multiple_codes_allowed") is True and rules.get("code_order_is_priority") is False and rules.get("classification_automated") is False, "INVALID_CATALOG", "근거 구분·검수용 사유 연결 규칙이 올바르지 않습니다.")
        sources = _array(data.get("source_files"), "INVALID_CATALOG")
        _require(len(sources) == len(SOURCE_NAMES), "INVALID_CATALOG", "체크리스트와 원래 합성 사례의 출처가 필요합니다.")
        for source in sources:
            source = _object(source, "INVALID_CATALOG")
            _require(_text(source.get("path")) and source["path"] in SOURCE_NAMES and isinstance(source.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), "INVALID_CATALOG", "출처 파일명 또는 SHA-256이 유효하지 않습니다.")
        _require({x["path"] for x in sources} == SOURCE_NAMES, "INVALID_CATALOG", "출처가 중복되거나 누락되었습니다.")
        decisions = _array(data.get("pending_decisions"), "INVALID_CATALOG")
        self._decisions = {}
        for decision in decisions:
            decision = _object(decision, "INVALID_CATALOG")
            key = decision.get("decision_id")
            _require(_text(key) and _text(decision.get("question")) and key not in self._decisions, "INVALID_CATALOG", "협의 ID·질문은 비어 있거나 중복될 수 없습니다.")
            self._decisions[key] = decision
        codes = _array(data.get("codes"), "INVALID_CATALOG")
        _require(bool(codes), "INVALID_CATALOG", "사유 코드가 필요합니다.")
        self._codes = {}
        for entry in codes:
            entry = _object(entry, "INVALID_CATALOG")
            code, result = entry.get("code"), entry.get("result")
            _require(_text(code) and isinstance(result, str) and result in {"NOT_MET", "UNKNOWN"}, "INVALID_CATALOG", "사유 코드·결과가 유효하지 않습니다.")
            prefix = "P2_NM_" if result == "NOT_MET" else "P2_U_"
            _require(re.fullmatch(prefix + r"[A-Z_]+", code) and code not in self._codes, "INVALID_CATALOG", "사유 코드가 중복되거나 형식이 잘못되었습니다.")
            _require(all(_text(entry.get(key)) for key in ("label", "use_when", "do_not_use_when")), "INVALID_CATALOG", "사유의 이름·사용 조건·오용 방지 조건이 필요합니다.")
            context = _array(entry.get("required_context"), "INVALID_CATALOG")
            _require(context and all(_text(x) for x in context), "INVALID_CATALOG", "필요한 컨텍스트를 명시해야 합니다.")
            pending = entry.get("pending_decision_id")
            _require(pending is None or (isinstance(pending, str) and pending in self._decisions), "INVALID_CATALOG", "사유 코드의 협의 ID가 유효하지 않습니다.")
            self._codes[code] = entry

    def _allow(self, allow_draft):
        _require(allow_draft is True, "DRAFT_NOT_APPROVED", "팀 미승인 초안입니다. 검수 목적으로 allow_draft=True를 명시하세요.")

    def _check_sources_unchanged(self):
        _, current_catalog_hash = _read(self.path)
        _require(current_catalog_hash == self.sha256, "SOURCE_CHANGED", "검토 중 사유 코드 카탈로그가 변경되었습니다.")
        for name, expected in self._source_hashes.items():
            _, actual = _read(self.path.parent / name)
            _require(actual == expected, "SOURCE_CHANGED", f"검토 중 원본이 변경되었습니다: {name}")

    def list_codes(self, result=None, *, allow_draft=False):
        self._allow(allow_draft)
        _require(result is None or (isinstance(result, str) and result in RESULTS), "INVALID_RESULT", "유효한 결과를 지정하세요.")
        self._check_sources_unchanged()
        return deepcopy({
            "catalog_version": self._data["catalog_version"],
            "status": self._data["status"], "approved": False,
            "codes": [x for x in self._data["codes"] if result is None or x["result"] == result],
            "pending_decisions": self._data["pending_decisions"],
        })

    def validate_assignment(self, result, reason_codes, *, allow_draft=False):
        self._allow(allow_draft)
        self._check_sources_unchanged()
        return self._assignment(result, reason_codes)

    def _assignment(self, result, reason_codes):
        _require(isinstance(result, str) and result in RESULTS, "INVALID_RESULT", "판정 값은 MET·NOT_MET·UNKNOWN이어야 합니다.")
        _require(isinstance(reason_codes, list) and all(_text(x) for x in reason_codes), "INVALID_ASSIGNMENT", "사유 코드는 문자열 배열이어야 합니다.")
        _require(len(reason_codes) == len(set(reason_codes)), "INVALID_ASSIGNMENT", "사유 코드는 중복될 수 없습니다.")
        if result == "MET":
            _require(not reason_codes, "INVALID_ASSIGNMENT", "MET에는 미충족·UNKNOWN 사유 코드를 부여하지 않습니다.")
        else:
            _require(reason_codes, "INVALID_ASSIGNMENT", "NOT_MET·UNKNOWN에는 사유 코드가 필요합니다.")
        for code in reason_codes:
            _require(code in self._codes, "UNKNOWN_REASON_CODE", f"등록되지 않은 사유 코드입니다: {code}")
            _require(self._codes[code]["result"] == result, "RESULT_REASON_MISMATCH", f"결과와 사유 종류가 다릅니다: {code}")
        return {"catalog_version": self._data["catalog_version"], "result": result,
                "reason_codes_draft": list(reason_codes), "approved": False}

    def _validate_case(self, row, base, active):
        row = _object(row, "INVALID_EXAMPLE")
        _require(_text(row.get("item_id")) and row["item_id"] in active, "INVALID_EXAMPLE", "사례가 활성 문항을 참조하지 않습니다.")
        for key in ("case_id", "evidence_excerpt", "rationale"):
            _require(_text(row.get(key)), "INVALID_EXAMPLE", f"사례의 {key}가 필요합니다.")
        _require(row.get("item_id") == base.get("item_id") and row.get("expected_result_draft") == base.get("expected_result_draft"), "EXAMPLE_MISMATCH", "원래 사례의 문항·기대 판정과 다릅니다.")
        evidence = base.get("evidence_text")
        _require(_text(evidence) and row["evidence_excerpt"] in evidence, "EXAMPLE_MISMATCH", "발췌는 원래 사례 근거의 실제 일부여야 합니다.")
        self._assignment(row.get("expected_result_draft"), row.get("reason_codes_draft"))
        pending = row.get("pending_decision_ids")
        _require(isinstance(pending, list) and all(_text(x) for x in pending) and len(pending) == len(set(pending)), "INVALID_PENDING_DECISIONS", "협의 ID는 중복 없는 문자열 배열이어야 합니다.")
        required = {self._codes[code]["pending_decision_id"] for code in row["reason_codes_draft"]} - {None}
        _require(set(pending) == required, "INVALID_PENDING_DECISIONS", "사유에 연결된 협의 ID가 누락되거나 다른 협의 ID가 섞였습니다.")

    def validate_examples(self, examples_path=None, *, allow_draft=False):
        self._allow(allow_draft)
        path = Path(examples_path) if examples_path is not None else self.path.parent / DEFAULT_EXAMPLES.name
        mapping, mapping_hash = _read(path)
        _require(mapping.get("approved") is False and mapping.get("status") == "DRAFT_FOR_TEAM_REVIEW" and _text(mapping.get("mapping_version")), "INVALID_EXAMPLE_SET", "사유 연결은 버전이 있는 팀 미승인 검수용 초안이어야 합니다.")
        _require(mapping.get("catalog_version") == self._data["catalog_version"], "SOURCE_CHANGED", "사유 연결의 카탈로그 버전이 다릅니다.")
        metadata = _object(mapping.get("metadata"), "INVALID_EXAMPLE_SET")
        _require(metadata.get("synthetic") is True and metadata.get("human_approved") is False and metadata.get("llm_executed") is False and metadata.get("actual_evidence_used") is False, "INVALID_EXAMPLE_SET", "합성·미승인·미실행 상태를 유지해야 합니다.")
        _require(mapping.get("source_files") == self._data["source_files"], "SOURCE_CHANGED", "원래 체크리스트·합성 사례의 파일 해시 연결이 다릅니다.")
        checklist = self._sources["checklist_draft.json"]
        controls = _array(checklist.get("controls"), "INVALID_SOURCE")
        items = []
        for control in controls:
            items.extend(_array(_object(control, "INVALID_SOURCE").get("items"), "INVALID_SOURCE"))
        _require(all(isinstance(x, dict) and _text(x.get("item_id")) for x in items), "INVALID_SOURCE", "원본 문항 ID가 잘못되었습니다.")
        active = {x["item_id"] for x in items}
        _require(len(active) == len(items), "INVALID_SOURCE", "원본 활성 문항 ID가 중복됩니다.")
        originals = _array(self._sources["review_examples.json"].get("cases"), "INVALID_SOURCE")
        _require(all(isinstance(x, dict) and _text(x.get("case_id")) for x in originals), "INVALID_SOURCE", "원래 사례 ID가 잘못되었습니다.")
        bases = {x["case_id"]: x for x in originals}
        _require(len(bases) == len(originals), "INVALID_SOURCE", "원래 사례 ID가 중복됩니다.")
        rows = _array(mapping.get("cases"), "INVALID_EXAMPLE_SET")
        _require(all(isinstance(x, dict) and _text(x.get("case_id")) for x in rows), "INVALID_EXAMPLE_SET", "사유 연결 사례 ID가 잘못되었습니다.")
        _require(len(rows) == len(bases) and {x["case_id"] for x in rows} == set(bases), "INVALID_EXAMPLE_SET", "원래 사례를 누락·중복·대체할 수 없습니다.")
        for row in rows:
            self._validate_case(row, bases[row["case_id"]], active)
        expected_pairs = {(item, result) for item in active for result in RESULTS}
        _require({(x["item_id"], x["expected_result_draft"]) for x in rows} == expected_pairs, "INVALID_EXAMPLE_SET", "활성 문항별 세 결과 사례가 모두 필요합니다.")
        boundaries = _array(mapping.get("boundary_cases"), "INVALID_EXAMPLE_SET")
        ids = set(bases)
        for row in boundaries:
            row = _object(row, "INVALID_EXAMPLE")
            _require(_text(row.get("case_id")) and row["case_id"].startswith("BC-") and row["case_id"] not in ids and _text(row.get("assumed_scope")) and _text(row.get("evidence_text")), "INVALID_EXAMPLE", "추가 경계 사례 ID·가정·근거가 없거나 중복됩니다.")
            ids.add(row["case_id"])
            self._validate_case(row, row, active)
        usage = Counter(code for row in rows + boundaries for code in row["reason_codes_draft"])
        _require(set(usage) == set(self._codes), "INVALID_EXAMPLE_SET", "모든 제안 사유 코드에는 검토 사례가 필요합니다.")
        self._check_sources_unchanged()
        _, final_mapping_hash = _read(path)
        _require(final_mapping_hash == mapping_hash, "SOURCE_CHANGED", "검토 중 사유 연결 사례가 변경되었습니다.")
        return {
            "status": "PASS", "checked_at": datetime.now().astimezone().isoformat(),
            "catalog_version": self._data["catalog_version"], "catalog_sha256": self.sha256,
            "mapping_version": mapping["mapping_version"], "mapping_sha256": mapping_hash,
            "source_files": deepcopy(self._data["source_files"]), "source_files_verified": True,
            "reason_code_count": len(self._codes), "mapped_case_count": len(rows),
            "boundary_case_count": len(boundaries), "active_item_count": len(active),
            "counts_by_result": dict(sorted(Counter(x["expected_result_draft"] for x in rows).items())),
            "counts_by_code": dict(sorted(usage.items())),
            "pending_decision_ids_in_examples": sorted({x for row in rows + boundaries for x in row["pending_decision_ids"]}),
            "approved": False, "human_approved": False, "llm_executed": False, "actual_evidence_used": False,
            "limits": ["코드 종류·참조·발췌·라벨 보존 등의 구조 검증. 사유 선택의 의미나 실제 증적 판정 정확도를 자동 검증하지 않음.", "합성 예시의 기대값이며 사람 검수·팀 승인 전. 운영 API·종합 판정 규칙을 확정하지 않음."],
        }


def main():
    parser = argparse.ArgumentParser(description="팀 미승인 사유 코드의 검수용 조회·사례 검증")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--result", choices=sorted(RESULTS))
    list_parser.add_argument("--allow-draft", action="store_true")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--examples", type=Path)
    validate_parser.add_argument("--allow-draft", action="store_true")
    validate_parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        catalog = ReasonCatalog(args.catalog)
        if args.command == "list":
            result = catalog.list_codes(args.result, allow_draft=args.allow_draft)
        else:
            result = catalog.validate_examples(args.examples, allow_draft=args.allow_draft)
            if args.report is not None:
                protected = {catalog.path.resolve(), (args.examples or catalog.path.parent / DEFAULT_EXAMPLES.name).resolve()}
                protected.update((catalog.path.parent / name).resolve() for name in SOURCE_NAMES)
                _require(args.report.resolve() not in protected, "INVALID_REPORT_PATH", "검증 보고서로 원본 JSON을 덮어쓸 수 없습니다.")
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReasonCodeError as exc:
        print(json.dumps({"status": "ERROR", "code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
