"""A reader's verification does not pulse the author's attestation row.

A researcher verified a project they had cloned, watched the top-bar
badge pulse and turn to Reproduced -- and saw the Attestation row in
the Project block pulse too (2026-10-13). A reader's run writes a
reproduction record under ``.vaibify/reproductions/`` and never touches
the author's attestation, which is what that row describes, so its
pulse claimed a check the run was not making. The row now pulses only
for a run that writes an attestation, or one that has not yet said
which record it writes.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_WHILE_RUNNING = """(sRunningRecordKind) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            listBinaries: [],
            dictArtifacts: {},
            bRebuildAttestationRunning: true,
            sRunningRecordKind: sRunningRecordKind,
            bRebuildAttestationCurrent: false,
            dictRebuildAttestation: null,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
    });
}"""


@pytest.mark.falsification
def test_only_a_run_that_writes_the_attestation_pulses_its_row(
    pageDashboard, serverHub,
):
    """Kills: pulsing the attestation row over a reader's run."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    for sKind in ("attestation", ""):
        sRendered = pageDashboard.evaluate(_S_RENDER_WHILE_RUNNING, sKind)
        assert sRendered.count('data-checking="1"') == 2, (
            f"a run writing {sKind or 'a record not yet known'} must "
            f"pulse the row's Level 3 cells: {sRendered[:400]}"
        )

    sReader = pageDashboard.evaluate(
        _S_RENDER_WHILE_RUNNING, "reproduction")
    assert 'data-checking="1"' not in sReader, (
        "a reader's verification pulsed the attestation row, which "
        "describes the author's attestation the run cannot change"
    )
    assert "being checked now" not in sReader, sReader[:400]
    assert "Verify Level 3 reproducibility" not in sReader, (
        "a second verification was offered while one is running"
    )
    assert pageDashboard.listPageErrors == []
