"""Read the checked-in interface contract; never fetch schemas over the network."""
from copy import deepcopy
from functools import lru_cache
import importlib.util
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
INTERFACE = ROOT / "phase2_인터페이스"


@lru_cache(maxsize=None)
def schema(kind):
    if kind not in {"input", "output", "item"}:
        raise ValueError("schema kind must be input, output or item")
    path = INTERFACE / f"phase2_{'output' if kind == 'item' else kind}.schema.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if kind == "item":
        data = {"$schema": data["$schema"], "$defs": data["$defs"], **data["$defs"]["ItemResult"]}
    Draft202012Validator.check_schema(data)
    return data


def item_schema():
    return deepcopy(schema("item"))


def validate_schema(payload, kind):
    issues = []
    def finite_numbers(value, path=""):
        if isinstance(value, float) and not math.isfinite(value):
            issues.append({"code": "P2E005" if kind == "input" else "E202",
                           "message": "JSON은 NaN/Infinity를 허용하지 않는다", "path": path, "severity": "error"})
        elif isinstance(value, dict):
            for key, child in value.items():
                finite_numbers(child, path + "/" + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                finite_numbers(child, path + "/" + str(index))
    finite_numbers(payload)
    validator = Draft202012Validator(schema(kind), format_checker=FormatChecker())
    for error in sorted(validator.iter_errors(payload), key=lambda e: str(list(e.path))):
        code = "P2E005" if kind == "input" else {
            "required": "E203", "enum": "E204", "const": "E204",
        }.get(error.validator, "E202")
        issues.append({"code": code, "message": error.message,
                       "path": "/" + "/".join(map(str, error.path)), "severity": "error"})
    return issues


@lru_cache(maxsize=None)
def interface_module(name):
    paths = {"errors": INTERFACE / "phase2_errors.py",
             "overall": INTERFACE / "test" / "overall_result.py",
             "kb_identity": ROOT / "phase1_검색" / "kb_identity.py"}
    path = paths[name]
    spec = importlib.util.spec_from_file_location(f"phase2_contract_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(code, message, **details):
    return {"code": code, "message": message, "severity": "error", **details}
