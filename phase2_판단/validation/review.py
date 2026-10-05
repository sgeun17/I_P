"""Official review codes plus auditable local signals not yet in the shared enum."""
from copy import deepcopy
import re
from .contracts import interface_module

REVIEW_PROFILE = "phase2_review_v0.1-provisional"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def injection_suspected(context):
    pattern = re.compile(r"이전\s*지시.{0,20}무시|(?:MET|NOT_MET|UNKNOWN).{0,15}(?:출력|판정)하|ignore.{0,30}instructions", re.I)
    return any(pattern.search(c["text"]) for c in context["chunks"])


def decide_review(items, context, issues=(), *, signals=(), injection=False):
    # Keep the model-generated wire item untouched. Enrich a copy solely for OCR review routing.
    sources = {c["chunk_id"]: c.get("source") for c in context["chunks"]}
    enriched = deepcopy(items)
    for item in enriched:
        for citation in item.get("citations", []):
            citation["source"] = sources.get(citation["chunk_id"])
    codes = list(dict.fromkeys(i["code"] for i in issues))
    result = interface_module("errors").decide_review(
        enriched, codes, injection_suspected=injection, review_signals=signals
    )
    result["error_codes"] = codes
    return result
