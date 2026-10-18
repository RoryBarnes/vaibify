"""The assessed rerun's verdicts reach the screen as the server worded them.

Every string a row or a card shows here comes from the record the server
wrote: the comparison policy's verdict and its tally, each file's
outcome and reason, and whether a record is a previous check. The page
renders them and decides none of them (``dashboard-state-honesty``).
Four ways the screen could still lie are pinned: a comparison verdict
shown without the count of files that differ, a pass with byte
differences worded or coloured as byte identity or divergence, a failed
command not named, and a previous check shown as a current one.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


S_COMPARISON_VERDICT = (
    "Reproduced: published comparisons passed; 1 of 2 files differ in "
    "their bytes")
S_TALLY = (
    "Tally: 2 regenerated outputs: 1 byte-identical, 1 comparisons "
    "passed, 0 failed, 0 not enough evidence; 0 carried as given; "
    "step-level checks: 0 passed, 0 failed, 0 without evidence.")
S_FAILED_VERDICT = (
    "Not reproduced: Step/values.json (the data section did not "
    "complete (failed: A01.data.1 exited 1)) failed")

_S_RENDER_THE_ATTESTATION_ROW = """(dictAttestation) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            listBinaries: [],
            dictArtifacts: {},
            bRebuildAttestationRunning: false,
            sRunningRecordKind: "",
            bRebuildAttestationCurrent:
                dictAttestation.bCurrentForTheTest === true,
            dictRebuildAttestation: dictAttestation,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
    });
}"""


def _fsRenderRow(pageDashboard, dictAttestation):
    """Return the Project block's HTML with this attestation summary."""
    return pageDashboard.evaluate(_S_RENDER_THE_ATTESTATION_ROW, dictAttestation)


@pytest.mark.falsification
def test_a_comparison_verdict_always_carries_its_tally(
    pageDashboard, serverHub,
):
    """Kills: dropping the tally from the attestation row's verdict."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRendered = _fsRenderRow(pageDashboard, {
        "sStatus": "passed", "sVerdict": "comparisons-passed",
        "sVerdictRendered": S_COMPARISON_VERDICT, "sTallyRendered": S_TALLY,
        "bHistorical": False, "bCurrentForTheTest": True,
        "iOutputHashesMatched": 1, "iOutputHashesTotal": 2,
    })
    assert S_COMPARISON_VERDICT in sRendered, sRendered[:600]
    assert S_TALLY in sRendered, sRendered[:600]
    assert "every file byte-identical" not in sRendered
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_pass_with_byte_differences_is_worded_and_coloured_as_a_pass(
    pageDashboard, serverHub,
):
    """The outcome and its reason, never the byte-only word or red.

    Kills: rendering a file outcome from its bytes alone, which calls a
    figure within vaibify's threshold "diverged" and paints it red.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRendered = pageDashboard.evaluate("""() =>
        VaibifyFileOutcomes.fsRenderFileOutcomesTable([{
            sPath: "Step/figure.png", sStatus: "diverged",
            sRole: "output", sExpected: "aa", sObserved: "bb",
            sOutcome: "comparisons passed",
            sOutcomeReason:
                "within vaibify's image-difference threshold (policy v1)",
        }])""")
    sText = pageDashboard.evaluate("""(sHtml) => {
        const elHolder = document.createElement("div");
        elHolder.innerHTML = sHtml;
        return elHolder.textContent;
    }""", sRendered)
    assert ("within vaibify's image-difference threshold (policy v1)"
            in sText), sText
    assert "file-outcome-diverged" not in sRendered
    assert "file-outcome-comparisons-passed" in sRendered
    assert ">diverged<" not in sRendered
    assert "byte-identical" not in sRendered
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_failed_rebuild_names_the_failed_command(pageDashboard, serverHub):
    """Kills: describing an assessed failure by its hash counts alone."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRendered = _fsRenderRow(pageDashboard, {
        "sStatus": "failed", "sVerdict": "not-reproduced",
        "sVerdictRendered": S_FAILED_VERDICT, "sTallyRendered": S_TALLY,
        "bHistorical": False, "listDivergedHashes": [S_FAILED_VERDICT],
        "dictRerunFailure": {}, "iOutputHashesMatched": 1,
        "iOutputHashesTotal": 1,
    })
    assert ("The last rebuild did NOT pass: " + S_FAILED_VERDICT
            in sRendered), sRendered[:800]
    assert "A01.data.1 exited 1" in sRendered
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_byte_only_attestation_reads_as_a_previous_check(
    pageDashboard, serverHub,
):
    """F11: the byte-only verifier's pass is history, never current.

    Kills: describing a historical attestation as a current one.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRendered = _fsRenderRow(pageDashboard, {
        "sStatus": "passed", "bHistorical": True,
        "sAttestedAtUtc": "2026-09-01T10:00:00Z",
        "iOutputHashesMatched": 3, "iOutputHashesTotal": 3,
    })
    assert ("Byte-identical under the previous check (before 2026-09-01)"
            in sRendered), sRendered[:800]
    assert "does not accept it" in sRendered
    assert "The last rebuild passed, but" not in sRendered
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_an_attestation_under_an_older_policy_reads_as_a_previous_check(
    pageDashboard, serverHub,
):
    """A verdict graded under a policy that is no longer current is history.

    Kills: the PROOF card showing a historical record's verdict as if
    it were current.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRendered = pageDashboard.evaluate("""() =>
        VaibifyProofTab.fsRenderAssessmentVerdict({
            sStatus: "passed", bHistorical: true,
            sAttestedAtUtc: "2026-10-01T10:00:00Z",
            sComparisonPolicy: "comparison-v0",
            sVerdictRendered: "Reproduced: every file byte-identical",
        })""")
    assert "graded under comparison-v0, a previous check" in sRendered
    assert "proof-attestation-historical" in sRendered
    assert "does not accept a previous check" in sRendered
    assert pageDashboard.listPageErrors == []
