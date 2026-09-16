"""A blocked row refuses every action and keeps teaching.

The ordering's circle-slash was first applied per-button, inside
``_fsRenderActionButton`` -- and the rows the blocked map actually
targets draw their principal controls inline: ``wf-verify-remote``
and ``wf-push-envelope`` in the envelope rows' own detail. A row that
said "Do this later" while leaving its main button live is the
dashboard contradicting itself, found by external review 2026-09-16
before it reached a researcher.

So blocking is structural -- a class on the ROW, one click guard in
the delegated dispatcher -- and this test drives the guard through a
control the per-button path never touched.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_INTO_HARNESS = """(dictArgs) => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: ["MANIFEST.sha256"],
            listBinaries: [],
            dictArtifacts: {},
            dictBlockedRows: dictArgs.dictBlockedRows,
            dictNextOrderedStep: dictArgs.dictNextOrderedStep || null,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(["publishedCopies"]),
        setExpandedRequirementRows: new Set(["github"]),
    });
    let elHarness = document.getElementById("blockedRowHarness");
    if (!elHarness) {
        elHarness = document.createElement("div");
        elHarness.id = "blockedRowHarness";
        // INSIDE the delegation root: the dispatcher listens on
        // #panelSteps, so a harness on document.body would receive
        // no dispatch at all and every "nothing fired" assertion
        // below would pass vacuously.
        document.getElementById("panelSteps").appendChild(elHarness);
    }
    elHarness.innerHTML = sHtml;
    window.__iVerifyCalls = 0;
    VaibifySyncManager.fnVerifyRemoteFromDashboard = function () {
        window.__iVerifyCalls += 1;
    };
    return sHtml;
}"""

_S_REASON = (
    "Regenerating the envelope rewrites requirements.lock and the "
    "manifest, so a push now publishes files you are about to change."
)


@pytest.mark.falsification
def test_a_blocked_rows_inline_controls_are_inert_and_it_still_teaches(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    The control clicked is ``wf-verify-remote`` -- rendered inline by
    the envelope row, never by ``_fsRenderActionButton`` -- because
    that is exactly the surface the per-button implementation left
    live.

    Kills: neutralizing the delegated click guard, which restores the
    reviewed defect -- a row that says "Do this later" over a live
    principal action.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- blocked: the row carries the class, the reason, and its
    # teaching content; its inline button does nothing but point at
    # the reason.
    sBlocked = pageDashboard.evaluate(_S_RENDER_INTO_HARNESS, {
        "dictBlockedRows": {"envelopeMirror": _S_REASON},
        "dictNextOrderedStep": {
            "sRowKey": "envelopeArchive",
            "sReason": "publish last",
            "listBlockedRowKeys": ["envelopeMirror"],
        },
    })
    assert 'requirement-row-blocked' in sBlocked, (
        "the row never took the structural class, so neither the "
        "guard nor the styling can find it"
    )
    # The ordering speaks its own keys; the merged rows are their
    # homes. Both the arrow target and the blocked entry must land on
    # the copies rows through the ONE alias map.
    assert 'data-ordering-row="zenodo"' in sBlocked, (
        "the arrow still targets the ordering key instead of the "
        "merged row, so it points at a row that no longer exists"
    )
    assert "Do this later" in sBlocked and _S_REASON in sBlocked, (
        "the reason the row must wait is not visible on it"
    )
    assert "MANIFEST.sha256" in sBlocked, (
        "the blocked row stopped teaching: its file rows are gone"
    )
    pageDashboard.click(
        "#blockedRowHarness .requirement-row-blocked .wf-verify-remote",
    )
    assert pageDashboard.evaluate("window.__iVerifyCalls") == 0, (
        "a blocked row's inline Verify still fired; the circle-slash "
        "is a lie on exactly the control the researcher will press"
    )
    # The file BADGE is an actionable span, not a button, and its
    # picklist offers the very push the row says to postpone -- the
    # button-only guard left it live (external review, 2026-09-16).
    pageDashboard.evaluate("""() => {
        window.__iPicklistCalls = 0;
        VaibifySyncManager.fnOpenRemotePicklistForBadge = function () {
            window.__iPicklistCalls += 1;
        };
    }""")
    pageDashboard.click(
        "#blockedRowHarness .requirement-row-blocked .remote-badge",
    )
    assert pageDashboard.evaluate("window.__iPicklistCalls") == 0, (
        "a blocked row's file badge still opens the push/sync "
        "picklist; the ordering says postpone and the badge says go"
    )
    assert pageDashboard.evaluate(
        "document.querySelector('#blockedRowHarness "
        ".requirement-row-wait-reason').classList"
        ".contains('wait-reason-attention')"
    ), (
        "the refused click gave no signal; positive evidence the "
        "guard ran, not merely absence of the handler's effect"
    )

    # --- unblocked twin: the SAME controls are live, so the guard
    # is a judgment about this row's state, not a dead zone.
    pageDashboard.evaluate(_S_RENDER_INTO_HARNESS, {
        "dictBlockedRows": {},
    })
    pageDashboard.click("#blockedRowHarness .wf-verify-remote")
    assert pageDashboard.evaluate("window.__iVerifyCalls") == 1, (
        "an unblocked row's Verify did not fire; the guard refuses "
        "more than the ordering claims"
    )
    pageDashboard.evaluate("""() => {
        window.__iPicklistCalls = 0;
        VaibifySyncManager.fnOpenRemotePicklistForBadge = function () {
            window.__iPicklistCalls += 1;
        };
    }""")
    pageDashboard.click("#blockedRowHarness .remote-badge")
    assert pageDashboard.evaluate("window.__iPicklistCalls") == 1, (
        "an unblocked row's badge did not open its picklist; the "
        "guard refuses more than the ordering claims"
    )
