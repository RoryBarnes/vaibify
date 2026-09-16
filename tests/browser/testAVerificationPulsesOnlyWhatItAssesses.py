"""A running rerun says what it is doing, and only about Level 3.

Two things a researcher watched happen on a real project, 2026-09-16.

A Level 3 verification started, and every level cell on the Attestation
section pulsed orange -- including the Level 1 and Level 2 cells, which
are dashes for levels the rerun says nothing about. The pulse was a
property of the GROUP, applied by CSS to every cell inside a checking
header, so it animated cells that were not being assessed.

Then the rerun finished, reported "24 of 26 re-derived files matched"
and named the two that differed -- and underneath that, told the
researcher that vaibify could not determine which part of the run had
failed and asked them to report it. Nothing had gone unexplained. The
capture record was empty because NO STEP FAILED, which is the normal
and complete state for a hash divergence; the renderer read empty as
unknown and sent them looking for a defect while the diagnosis sat two
lines above on the same screen.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ATTESTATION = """(dictArgs) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            listBinaries: [],
            dictArtifacts: {},
            bRebuildAttestationRunning: dictArgs.bRunning,
            bRebuildAttestationCurrent: false,
            dictRebuildAttestation: dictArgs.dictAttestation,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
    });
}"""

_DICT_DIVERGED = {
    "sStatus": "failed",
    "iOutputHashesMatched": 24,
    "iOutputHashesTotal": 26,
    "listCarriedPaths": ["AI_USAGE.md"],
    "listDivergedHashes": [".vaibify/environment.json", "requirements.lock"],
    "dictRerunFailure": {},
}


@pytest.mark.falsification
def test_a_running_verification_pulses_and_explains_only_level_three(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Three claims that only a rendered page can hold together, so they
    share the single lease this file is allowed. Each is asserted
    against its own render of the same component.

    Kills: marking every cell in a checking group -- which is what the
    ``.requirement-group-checking > .requirement-group-header
    .step-level-cell`` selector did, animating Level 1 and Level 2
    dashes over a rerun that assesses neither.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- running: only the Level 3 cells are marked, and the line
    # names the signal the researcher should watch.
    sRunning = pageDashboard.evaluate(_S_RENDER_ATTESTATION, {
        "bRunning": True, "dictAttestation": None,
    })
    assert 'data-checking="1"' in sRunning, (
        "no cell was marked as being checked, so this run proves "
        "nothing about which ones are"
    )
    # One row cell plus one banner cell -- Level 3 in each, and
    # nothing at Level 1 or Level 2.
    iMarked = sRunning.count('data-checking="1"')
    assert iMarked == 2, (
        "a verification marked cells at levels it does not assess; "
        f"expected the two Level 3 cells, found {iMarked} marked"
    )
    assert "Level 3: being checked now" in sRunning, sRunning[:400]
    for sLevel in ("Level 1: being checked now",
                   "Level 2: being checked now"):
        assert sLevel not in sRunning, (
            f"a rerun claimed to be assessing {sLevel[:7]}"
        )
    assert ("Level 3 lights will pulse until the pipeline run is "
            "complete") in sRunning, (
        "the running line promised a verdict and named no signal to "
        "watch for as long as the workflow takes to run"
    )

    # --- settled on a pure hash divergence: an EMPTY capture record
    # means no step failed, which is an answer, not a mystery.
    sDiverged = pageDashboard.evaluate(_S_RENDER_ATTESTATION, {
        "bRunning": False, "dictAttestation": _DICT_DIVERGED,
    })
    assert "requirements.lock" in sDiverged, (
        "the diverged paths were not rendered, so the note below them "
        "cannot be judged"
    )
    assert "Every step ran and exited cleanly" in sDiverged, (
        sDiverged[-1200:]
    )
    assert "Please report this" not in sDiverged, (
        "a fully diagnosed hash divergence was reported to the "
        "researcher as an unexplained defect"
    )

    # --- and with nothing diverged either, it IS unexplained.
    dictSilent = dict(_DICT_DIVERGED)
    dictSilent["listDivergedHashes"] = []
    sSilent = pageDashboard.evaluate(_S_RENDER_ATTESTATION, {
        "bRunning": False, "dictAttestation": dictSilent,
    })
    assert "Please report this" in sSilent, (
        "a failure that named no step AND no diverged file was "
        "presented as though it had been explained"
    )
