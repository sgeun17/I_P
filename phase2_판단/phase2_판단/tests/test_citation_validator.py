from validation.citations import validate_citations


def context(*chunks):
    return {"chunks": list(chunks)}


def chunk(
    chunk_id="EV1_v1_c0000",
    *,
    text="승인된 담당자가 점검을 완료했다.",
    page_start=2,
    page_end=2,
    role="evidence",
):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "page_start": page_start,
        "page_end": page_end,
        "role": role,
    }


def output(result="MET", *, citations=None):
    return {
        "result": result,
        "citations": citations if citations is not None else [],
    }


def citation(chunk_id="EV1_v1_c0000", *, page=2, quote="담당자가 점검을 완료했다."):
    return {"chunk_id": chunk_id, "page": page, "quote": quote}


def codes(validation):
    return {issue.code for issue in validation.issues}


def test_met_not_met은_인용이_필수다():
    for result in ("MET", "NOT_MET"):
        validation = validate_citations(output(result), context(chunk()), expected_evidence_id="EV1")
        assert not validation.passed
        assert "P2E501" in codes(validation)


def test_unknown은_무인용이_정상이다():
    validation = validate_citations(output("UNKNOWN"), context(chunk()), expected_evidence_id="EV1")
    assert validation.passed
    assert not validation.warnings


def test_unknown도_인용을_냈으면_검사한다():
    validation = validate_citations(
        output("UNKNOWN", citations=[citation(quote="원문에 없는 문장")]),
        context(chunk()),
        expected_evidence_id="EV1",
    )
    assert "E403" in codes(validation)


def test_없는_청크를_잡는다():
    validation = validate_citations(
        output(citations=[citation("EV1_v1_c9999")]),
        context(chunk()),
        expected_evidence_id="EV1",
    )
    assert "E402" in codes(validation)


def test_다른_증적_인용을_잡는다():
    foreign = chunk("EV2_v1_c0000")
    validation = validate_citations(
        output(citations=[citation("EV2_v1_c0000")]),
        context(foreign),
        expected_evidence_id="EV1",
    )
    assert "E406" in codes(validation)


def test_빈_인용문을_잡는다():
    validation = validate_citations(
        output(citations=[citation(quote=" \n\t")]),
        context(chunk()),
        expected_evidence_id="EV1",
    )
    assert "E405" in codes(validation)


def test_페이지_불일치를_잡는다():
    validation = validate_citations(
        output(citations=[citation(page=7)]),
        context(chunk()),
        expected_evidence_id="EV1",
    )
    assert "E404" in codes(validation)


def test_페이지형_청크의_page_null은_경고다():
    validation = validate_citations(
        output(citations=[citation(page=None)]),
        context(chunk()),
        expected_evidence_id="EV1",
    )
    assert validation.passed
    assert {warning.code for warning in validation.warnings} == {"E407"}


def test_공백만_정규화해_원문을_대조한다():
    source = chunk(text="승인된 담당자가\n  점검을 완료했다.")
    validation = validate_citations(
        output(citations=[citation(quote="담당자가 점검을 완료했다.")]),
        context(source),
        expected_evidence_id="EV1",
    )
    assert validation.passed


def test_evidence와_context_role을_모두_검사_대상으로_받는다():
    supporting = chunk(role="context")
    validation = validate_citations(
        output(citations=[citation()]),
        context(supporting),
        expected_evidence_id="EV1",
    )
    assert validation.passed


def test_알_수_없는_role을_잡는다():
    invalid = chunk(role="system")
    validation = validate_citations(
        output(citations=[citation()]),
        context(invalid),
        expected_evidence_id="EV1",
    )
    assert "P2E004" in codes(validation)
