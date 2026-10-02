import pytest
from date_extractor import extract_dates, check_freshness, check_evidence_format


def chunks(text, **kw):
    return [{"chunk_id": "E0001_v1_c0000", "text": text, "source": "parser", "file_type": "pdf", **kw}]


@pytest.mark.parametrize("raw", ["2026-10-02", "2026.10.02", "2026/10/2", "2026년 10월 2일"])
def test_date_forms_and_offsets(raw):
    text = "작성일: " + raw + " 기록"
    candidate = extract_dates(chunks(text))[0]
    assert candidate["value"] == "2026-10-02"
    assert text[candidate["start"]:candidate["end"]] == raw
    assert candidate["label"] == "document_date"


@pytest.mark.parametrize("raw,expected", [("2024-02-29", "2024-02-29"), ("2026-02-29", None), ("2026-13-01", None)])
def test_calendar_dates(raw, expected):
    assert extract_dates(chunks(raw))[0]["value"] == expected


def test_does_not_invent_dates_from_filename_partial_year_or_ocr_digit():
    assert extract_dates(chunks("2026년 상반기, 26-10-02, 2026년 10월, 2O26-10-02", source_file="2026-10-02.pdf")) == []


@pytest.mark.parametrize("text,status", [
    ("작성일 2026-04-02", "FRESH"), ("작성일 2026-04-01", "STALE"),
    ("작성일 2026-10-02", "FRESH"), ("작성일 2026-10-03", "FUTURE_DATE"),
    ("날짜 없음", "DATE_MISSING"), ("작성일 2026-02-30", "INVALID_DATE"),
    ("작성일 2026-01-01 / 작성일 2026-09-01", "AMBIGUOUS_DATE"),
])
def test_freshness_boundaries(text, status):
    result = check_freshness(extract_dates(chunks(text)), as_of="2026-10-02", max_age_months=6, date_label="document_date")
    assert result["status"] == status


def test_leap_month_cutoff_clamps_day():
    result = check_freshness(extract_dates(chunks("수행일 2024-02-29")), as_of="2024-03-31", max_age_months=1, date_label="performed_date")
    assert result["cutoff"] == "2024-02-29" and result["status"] == "FRESH"


def test_date_role_not_latest_wins():
    found = extract_dates(chunks("작성일 2026-09-01; 시행일 2025-01-01"))
    assert check_freshness(found, as_of="2026-10-02", max_age_months=6, date_label="effective_date")["status"] == "STALE"


def test_ocr_dates_always_need_review():
    found = extract_dates(chunks("작성일 2026-09-01", source="ocr"))
    assert check_freshness(found, as_of="2026-10-02", max_age_months=6, date_label="document_date")["status"] == "OCR_DATE_REVIEW"


def test_no_policy_no_assumed_age():
    assert check_freshness([], as_of="2026-10-02")["status"] == "POLICY_MISSING"
    assert check_freshness([], as_of="2026-10-02", max_age_months=6)["status"] == "DATE_ROLE_UNSPECIFIED"


@pytest.mark.parametrize("months", [-1, True, 1.5])
def test_bad_month_policy(months):
    with pytest.raises(ValueError):
        check_freshness([], as_of="2026-10-02", max_age_months=months)


def test_format_uses_parser_metadata():
    assert check_evidence_format(chunks("규정", source_file="fake.xlsx"), ["pdf"])["status"] == "ALLOWED"
    assert check_evidence_format(chunks("규정"), ["xlsx"])["status"] == "FORMAT_MISMATCH"
    assert check_evidence_format(chunks("규정", file_type=None), ["pdf"])["status"] == "FORMAT_UNKNOWN"
    assert check_evidence_format(chunks("규정"))["status"] == "POLICY_MISSING"
