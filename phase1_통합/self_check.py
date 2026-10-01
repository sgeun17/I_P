from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def check() -> dict:
    checks = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    required = {
        "input_main": ROOT / "phase1_입력" / "main.py",
        "input_pipeline": ROOT / "phase1_입력" / "pipeline.py",
        "search_retriever": ROOT / "phase1_검색" / "chunk_retriever.py",
        "search_judgment_pipeline": ROOT / "phase1_검색" / "judgment_pipeline.py",
        "judgment_service": ROOT / "phase1_판단" / "src" / "service.py",
        "judgment_schema": ROOT / "phase1_판단" / "schemas" / "phase1_mapping_result.schema.json",
    }
    for name, path in required.items():
        add(name, path.is_file(), str(path))

    model_dir = ROOT / "phase1_검색" / "models" / "bge-m3"
    add("bge_m3_model", model_dir.is_dir(), str(model_dir))

    chroma_dir = ROOT / "phase1_검색" / "data" / "chroma"
    add("chroma_index", chroma_dir.exists(), str(chroma_dir))

    model = (os.getenv("PHASE1_JUDGMENT_MODEL") or "").strip()
    add("judgment_model_env", bool(model), model or "PHASE1_JUDGMENT_MODEL 미설정")

    db_env = ROOT / "phase1_입력" / "database" / ".env"
    add("input_db_env", db_env.is_file(), str(db_env))

    return {
        "ready": all(x["ok"] for x in checks),
        "checks": checks,
        "note": "파일/설정 존재 여부 점검이며 DB·BGE-M3·LLM 실제 연결 성공까지 보장하지는 않습니다.",
    }


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
