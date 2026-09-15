"""An interrupted promotion is surfaced on LOAD, and offers nothing yet.

A promotion mints a permanent DOI in the middle of a long upload. The
browser that started it may be gone by the time it fails, so the card
must come from the poll payload rather than from a toast the
researcher had to keep.

And it must offer NOTHING until Zenodo has been asked. Vaibify does not
know whether a draft holds two files or five, nor whether a publish
that never returned nevertheless succeeded; every action here mutates a
live archive, so offering one on a guess is how a real DOI gets thrown
away.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_PENDING = """(dictArgs) => {
    if (dictArgs.dictOutcome) {
        VaibifyWorkflowRequirements.fnRecordPromotionOutcome(
            'promotion-1', dictArgs.dictOutcome);
    }
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            listPendingPromotions: dictArgs.listPending,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['promotions']),
        setExpandedRequirementRows: new Set(['promotion-promotion-1']),
    });
}"""


_LIST_ONE_PENDING = [{
    "sPromotionId": "promotion-1",
    "sLane": "image",
    "sTargetService": "zenodo",
    "iDepositId": 4242,
    "sPhase": "drafted",
    "sStartedIso": "2026-09-14T10:00:00+00:00",
    "listFiles": [],
}]


def test_the_card_appears_on_load_and_waits_for_zenodos_answer(
    pageDashboard, serverHub,
):
    """ONE open, every assertion — the seeded project is leased."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- no pending promotion: no section at all. A heading about a
    # situation nobody is in is noise on every healthy project.
    sClean = pageDashboard.evaluate(
        _S_RENDER_PENDING, {"listPending": [], "dictOutcome": None},
    )
    assert "Interrupted promotions" not in sClean

    # --- one pending, not yet reconciled: the card is there and the
    # only control asks Zenodo.
    sPending = pageDashboard.evaluate(_S_RENDER_PENDING, {
        "listPending": _LIST_ONE_PENDING, "dictOutcome": None,
    })
    assert "Interrupted promotions" in sPending
    assert "environment archive" in sPending
    assert "4242" in sPending
    assert 'data-wf-action="reconcile-promotion"' in sPending
    for sAction in ("resume-promotion", "adopt-promotion",
                    "discard-promotion"):
        assert sAction not in sPending, (
            sAction + " was offered before Zenodo had been asked"
        )

    # --- published: adoption only, and no discard anywhere near it.
    sPublished = pageDashboard.evaluate(_S_RENDER_PENDING, {
        "listPending": _LIST_ONE_PENDING,
        "dictOutcome": {
            "sOutcome": "published",
            "sMessage": "This promotion completed.",
            "listActions": ["adopt"],
        },
    })
    assert 'data-wf-action="adopt-promotion"' in sPublished
    assert "discard-promotion" not in sPublished

    # --- unknown: nothing is offered, and the card SAYS the record is
    # kept. A question nobody answered is not a "no".
    sUnknown = pageDashboard.evaluate(_S_RENDER_PENDING, {
        "listPending": _LIST_ONE_PENDING,
        "dictOutcome": {
            "sOutcome": "unknown",
            "sMessage": "Vaibify could not ask Zenodo.",
            "listActions": [],
        },
    })
    assert "The record is kept." in sUnknown
    for sAction in ("resume-promotion", "adopt-promotion",
                    "discard-promotion"):
        assert sAction not in sUnknown, (
            sAction + " was offered over an unanswered question"
        )
