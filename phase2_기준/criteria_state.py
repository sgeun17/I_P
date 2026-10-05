"""Criteria approval is separate from approval of an evidence judgment."""


def valid_state(document):
    approved = document.get('approved')
    return ((approved is False and document.get('status') == 'DRAFT_FOR_TEAM_REVIEW')
            or (approved is True and document.get('status') == 'APPROVED_FOR_USE'))
