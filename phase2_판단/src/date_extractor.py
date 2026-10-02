"""Conservative date candidates, with source offsets; no filename/mtime inference."""
from dataclasses import asdict, dataclass
from datetime import date
from calendar import monthrange
import re

DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*(?:[-./]\s*(?P<month>\d{1,2})\s*[-./]\s*(?P<day>\d{1,2})"
    r"|년\s*(?P<km>\d{1,2})\s*월\s*(?P<kd>\d{1,2})\s*일)(?!\d)"
)


@dataclass(frozen=True)
class DateCandidate:
    chunk_id: str
    raw: str
    value: str | None
    start: int
    end: int
    source: str
    label: str


def extract_dates(chunks):
    """Accept full dates only. Invalid calendar dates remain visible with value=None."""
    found = []
    for chunk in chunks:
        text = chunk.get("text", "")
        for match in DATE_PATTERN.finditer(text):
            try:
                parsed = date(int(match["year"]), int(match["month"] or match["km"]),
                              int(match["day"] or match["kd"])).isoformat()
            except ValueError:
                parsed = None
            prefix = text[max(0, match.start() - 20):match.start()]
            labels = [(prefix.rfind(term), label) for term, label in (
                ("작성", "document_date"), ("수행", "performed_date"),
                ("시행", "effective_date"), ("기한", "deadline"), ("만료", "expiry"))]
            position, label = max(labels)
            found.append(asdict(DateCandidate(chunk["chunk_id"], match[0], parsed,
                            match.start(), match.end(), chunk.get("source", "parser"),
                            label if position >= 0 else "unclassified")))
    return found


def check_freshness(candidates, *, as_of, max_age_months=None, date_label=None):
    """Inclusive calendar-month cutoff. Returns a signal, never automatic NOT_MET."""
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    if max_age_months is None:
        return {"status": "POLICY_MISSING", "cutoff": None, "as_of": as_of.isoformat()}
    if type(max_age_months) is not int or max_age_months < 0:
        raise ValueError("max_age_months must be a nonnegative integer")
    if not date_label:
        return {"status": "DATE_ROLE_UNSPECIFIED", "as_of": as_of.isoformat()}
    if date_label not in {"document_date", "performed_date", "effective_date", "deadline", "expiry"}:
        raise ValueError("unknown date_label")
    selected = [c for c in candidates if c["label"] == date_label]
    values = {c["value"] for c in selected if c["value"]}
    if not selected:
        status = "DATE_MISSING"
    elif any(c["value"] is None for c in selected):
        status = "INVALID_DATE"
    elif len(values) != 1:
        status = "AMBIGUOUS_DATE"
    elif any(c["source"] == "ocr" for c in selected):
        status = "OCR_DATE_REVIEW"
    else:
        status = None
    month_index = as_of.year * 12 + as_of.month - 1 - max_age_months
    year, month = divmod(month_index, 12)
    if year < 1:
        raise ValueError("max_age_months exceeds supported calendar range")
    cutoff = date(year, month + 1, min(as_of.day, monthrange(year, month + 1)[1]))
    value = next(iter(values)) if len(values) == 1 else None
    if status is None:
        parsed = date.fromisoformat(value)
        status = "FUTURE_DATE" if parsed > as_of else "FRESH" if parsed >= cutoff else "STALE"
    return {"status": status, "date": value, "date_label": date_label,
            "as_of": as_of.isoformat(), "cutoff": cutoff.isoformat(), "max_age_months": max_age_months}


def check_evidence_format(chunks, allowed_types=None):
    """Check trusted parser metadata, not a title-based semantic evidence category."""
    types = sorted({c.get("file_type") for c in chunks if c.get("file_type")})
    if not chunks or any(not c.get("file_type") for c in chunks):
        status = "FORMAT_UNKNOWN"
    elif allowed_types is None:
        status = "POLICY_MISSING"
    elif set(types) <= set(allowed_types):
        status = "ALLOWED"
    else:
        status = "FORMAT_MISMATCH"
    return {"status": status, "file_types": types, "allowed_types": allowed_types}
