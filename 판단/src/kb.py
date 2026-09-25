"""통제항목 ID 목록.

검색팀 `controls.json`에서 뽑은 ID → 명칭 표다.
Validator가 "KB에 존재하는 ID인가", "ID와 이름이 맞는가"를 볼 때만 쓴다.

KB가 바뀌면 `data/control_index.json`을 다시 만들고 `kb_sha256`을 갱신한다.
판단 결과에 이 해시를 기록하므로, 나중에 어느 KB로 판단했는지 추적할 수 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

INDEX_PATH = Path(__file__).parent.parent / "data" / "control_index.json"


class ControlIndex:
    def __init__(self, path: Path = INDEX_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.kb_sha256: str = data["kb_sha256"]
        self.controls: dict[str, str] = data["controls"]
        if len(self.controls) != data["count"]:
            raise ValueError("control_index.json의 count와 실제 개수가 다르다")

    def __len__(self) -> int:
        return len(self.controls)

    def exists(self, control_id: str) -> bool:
        """KB에 있는 ID인가. 정규화하지 않고 정확히 비교한다."""
        return control_id in self.controls

    def name_of(self, control_id: str) -> str | None:
        return self.controls.get(control_id)

    def name_matches(self, control_id: str, control_name: str) -> bool:
        """ID와 명칭이 KB와 일치하는가. 앞뒤 공백만 무시한다."""
        expected = self.controls.get(control_id)
        if expected is None:
            return False
        return expected.strip() == (control_name or "").strip()


DEFAULT_INDEX = ControlIndex()
