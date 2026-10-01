from checklist_adapter import get_checklist_item, load_reason_code_catalog
from remediation_guides import build_guide


def test_every_current_draft_reason_code_has_a_guide():
    item = get_checklist_item("2.5.6-Q07", allow_draft=True)
    catalog = load_reason_code_catalog(allow_draft=True)
    guides = [build_guide(row, item) for row in catalog["codes"]]

    assert len(guides) == len(catalog["codes"])
    assert {g["guide_type"] for g in guides} == {"REMEDIATION", "ADDITIONAL_EVIDENCE"}
    assert all(g["draft"] is True for g in guides)
    assert all(g["recommended_action"] for g in guides)
