"""The GitHub nudge appears, says what it is, and gates nothing.

Level 3 asks for the rebuild attestation in the ARCHIVE, because a
repository can be renamed, made private or deleted and so cannot
support a permanence claim. The GitHub copy is still the one a reader
who clones the repo will look for, so the row encourages it.

An encouragement that reads like a requirement is the failure mode
here: a researcher who takes this for a blocker goes hunting for a
Level 3 problem they do not have. So these assert what the nudge SAYS,
not merely that an element rendered.

Driven through ``fsRenderProjectBlock`` -- the module's public entry
point -- in a real browser, rather than through a private hook. The
whole feature is frontend and its input is a THREE-state field that a
boolean would flatten, so a green Python suite says nothing about it.
"""

import pytest



pytestmark = pytest.mark.browser

_S_RENDER_BLOCK = """(dictDetail) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: dictDetail,
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
    });
}"""


def _fnLoadTheDashboardScripts(page, serverHub):
    """Load the hub page only, claiming no project.

    The renderer under test is a pure function of its context, so a
    claimed project buys nothing -- and costs something real: a
    browser context that claims a project holds the record through a
    30-second reconnect grace, so with a module-scoped serverHub every
    SECOND test that opened one was refused (measured: a scratch
    module whose tests did nothing but open the project failed on the
    second). Loading the scripts and calling the module directly keeps
    these four independent.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_function(
        "() => typeof VaibifyWorkflowRequirements !== 'undefined'",
        timeout=15000,
    )


def _fsRenderAttestationRow(page, valueGithubState, bCurrent=True):
    """Render the Project block's attestation group and return its HTML."""
    return page.evaluate(_S_RENDER_BLOCK, {
        "bRebuildAttestationCurrent": bCurrent,
        "dictRebuildAttestation": {"sStatus": "passed"},
        "dictAttestationPublication": {"github": valueGithubState},
    })


def testTheNudgeSaysItDoesNotAffectTheProofLevel(
    pageDashboard, serverHub,
):
    """The sentence that keeps an encouragement from reading as a gate.

    Kills removing the disclaimer, which is the entire difference
    between a nudge and a criterion as far as the reader is concerned.
    """
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sHtml = _fsRenderAttestationRow(pageDashboard, False)
    assert "not on GitHub" in sHtml, sHtml
    assert "does not affect your PROOF level" in sHtml, sHtml
    assert "attestation-nudge-mark" in sHtml, "the octocat is missing"
    assert pageDashboard.listPageErrors == []


def testAnUncheckedRemoteIsNotDescribedAsAbsent(
    pageDashboard, serverHub,
):
    """``null`` and ``false`` are different sentences.

    Kills collapsing the tri-state in the renderer: telling a
    researcher their attestation "is not on GitHub" when no verify has
    ever looked is a claim the hub has not earned.
    """
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sHtml = _fsRenderAttestationRow(pageDashboard, None)
    assert "No verification has checked" in sHtml, sHtml
    assert "is not on GitHub" not in sHtml, sHtml
    assert "does not affect your PROOF level" in sHtml
    assert pageDashboard.listPageErrors == []


def testAPublishedAttestationDrawsNoNudge(pageDashboard, serverHub):
    """Nothing to encourage once the copy is there."""
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sHtml = _fsRenderAttestationRow(pageDashboard, True)
    assert "attestation-nudge" not in sHtml, sHtml
    assert pageDashboard.listPageErrors == []


def testAStaleAttestationIsNeverAdvertisedForPublishing(
    pageDashboard, serverHub,
):
    """A stale or failed record must not be urged onto GitHub.

    Kills dropping the ``bRebuildAttestationCurrent`` guard, which
    would ask a researcher to publish a claim about bytes they no
    longer have.
    """
    _fnLoadTheDashboardScripts(pageDashboard, serverHub)
    sHtml = _fsRenderAttestationRow(
        pageDashboard, False, bCurrent=False,
    )
    assert "attestation-nudge" not in sHtml, sHtml
    assert pageDashboard.listPageErrors == []
