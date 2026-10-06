"""Phase 2 기준팀 카탈로그를 판단 하네스가 읽기 위한 얇은 adapter.

기본값은 현재 승인된 1·2·3장 전체 카탈로그를 사용한다.
미승인 과거 draft를 직접 지정해 개발 검수할 때만 allow_draft=True를 사용한다.
운영 저장소/API를 대신하지 않는다.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
DEFAULT_CHECKLIST = PROJECT_ROOT / "phase2_기준" / "full_checklist_draft.json"
DEFAULT_REASON_CODES = PROJECT_ROOT / "phase2_기준" / "full_reason_codes_draft.json"


class DraftDataError(ValueError):
    pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DraftDataError(f"draft JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(data, dict):
        raise DraftDataError(f"draft JSON 최상위는 object여야 합니다: {path}")
    return data


def load_checklist_catalog(
    path: Path = DEFAULT_CHECKLIST,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("approved") is not True and not allow_draft:
        raise DraftDataError("미승인 체크리스트입니다. 개발 검수에는 allow_draft=True가 필요합니다.")
    return data


def get_checklist_item(
    item_id: str,
    path: Path = DEFAULT_CHECKLIST,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    catalog = load_checklist_catalog(path, allow_draft=allow_draft)
    for control in catalog.get("controls", []):
        for item in control.get("items", []):
            if item.get("item_id") == item_id:
                return deepcopy(item)
    raise DraftDataError(f"체크리스트 item_id를 찾을 수 없습니다: {item_id}")


def load_reason_code_catalog(
    path: Path = DEFAULT_REASON_CODES,
    *,
    allow_draft: bool = False,
) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("approved") is not True and not allow_draft:
        raise DraftDataError("미승인 사유 코드입니다. 개발 검수에는 allow_draft=True가 필요합니다.")
    return data


def compact_reason_codes(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "code": row.get("code"),
            "result": row.get("result"),
            "label": row.get("label"),
            "use_when": row.get("use_when"),
            "do_not_use_when": row.get("do_not_use_when"),
        }
        for row in catalog.get("codes", [])
    ]
