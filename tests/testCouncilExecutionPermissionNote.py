"""Each council tells its members what its kind lets them do.

A member that is not told its copy is writable will not use it, and a
member of a read-only council that is not told it cannot run anything
will try. So every protocol turn carries a note composed for its
campaign's kind, and the chairbot chat carries its own, because the chat
is read-only with search in every council (charter 1.9.0).
"""

import pytest

from vaibify.gui import (
    agentCouncilCampaign,
    agentCouncilCharter,
    agentCouncilController,
)

I_MEBIBYTE = 1024 * 1024


def _fdictCampaign(sExecutionPermission):
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "Is the tolerance adequate?",
        [agentCouncilCampaign.fdictCreateParticipant("claude", "opus"),
         agentCouncilCampaign.fdictCreateParticipant("codex", "modelBeta")],
        dictSettings={"sExecutionPermission": sExecutionPermission})
    return dictCampaign


def _fdictManifest(iSnapshotBytes, iFileCount=10):
    return {
        "iTotalContentBytes": iSnapshotBytes,
        "iIncludedMemberCount": iFileCount,
        "sPreObservationDigest": "observed",
        "dictRunnerSpaceAtCapture": {
            "iRunnerWorkingTreeBytes": 512 * I_MEBIBYTE,
            "iRunnerScratchBytes": 64 * I_MEBIBYTE},
    }


def _fsNoteFor(sExecutionPermission, dictManifest):
    dictCampaign = _fdictCampaign(sExecutionPermission)
    return agentCouncilController._fsComposeExecutionPermissionNote(
        dictCampaign, dictManifest)


def testTheCharterIsVersionOneNineAndSaysTheCopyIsTheMembers():
    assert agentCouncilCharter.S_CHARTER_VERSION == "1.9.0"
    sText = agentCouncilCharter.S_CHARTER_TEXT
    assert "Your copy is yours to change." in sText
    assert "nothing from the web" in sText
    assert "is ever confirmed" in sText


def testAFullSandboxNoteNamesTheRoomAndTheGitBaseline():
    sNote = _fsNoteFor("fullSandbox", _fdictManifest(300 * I_MEBIBYTE))
    assert sNote.startswith("EXECUTION PERMISSION: full sandbox.")
    iFreeMebibytes = (512 * I_MEBIBYTE - 300 * I_MEBIBYTE - 10 * 4096) // (
        I_MEBIBYTE)
    assert f"about {iFreeMebibytes} MB free" in sNote
    assert "/tmp is 64 MB" in sNote
    assert "cannot be executed directly" in sNote
    assert "git add --force --intent-to-add" in sNote
    assert "git add -A" not in sNote


def testASmallSnapshotIsOfferedAWholeCopyBaseline():
    sNote = _fsNoteFor("fullSandbox", _fdictManifest(10 * I_MEBIBYTE))
    assert "git add -A --force" in sNote
    assert "user.email=" in sNote


def testAnImageWithoutGitIsToldToKeepCopiesInTmp():
    sNote = agentCouncilCharter.fsDescribeExecutionPermission(
        False, {"iFreeAfterCopyBytes": I_MEBIBYTE, "iScratchBytes": I_MEBIBYTE,
                "bFullBaselineFits": True}, bGitAvailable=False)
    assert "no git" in sNote
    assert "git init" not in sNote


@pytest.mark.falsification
def testAReadOnlyCouncilIsToldItCannotEditOrRun():
    """Kills: a read-only council handed the full-sandbox note."""
    sNote = _fsNoteFor("readOnly", _fdictManifest(10 * I_MEBIBYTE))
    assert sNote == agentCouncilCharter.S_READ_ONLY_EXECUTION_NOTE
    assert "cannot edit the copy" in sNote


@pytest.mark.falsification
def testEveryProtocolTurnCarriesItsCampaignsNote():
    """Kills: the standing sections dropping the execution note."""
    dictCampaign = _fdictCampaign("fullSandbox")
    dictCampaign["dictProjectIdentity"]["sExecutionPermissionNote"] = (
        "EXECUTION PERMISSION: full sandbox. marker")
    sChannel = agentCouncilCharter.fsComposeTurnInstruction(
        dictCampaign, dictCampaign["listParticipants"][0],
        agentCouncilCharter.S_PHASE_PROPOSAL)
    assert "EXECUTION PERMISSION: full sandbox. marker" in sChannel


@pytest.mark.falsification
def testTheChatNeverInheritsTheFullSandboxNote():
    """Kills: the chat composed with its campaign's own execution note."""
    dictCampaign = _fdictCampaign("fullSandbox")
    dictCampaign["dictProjectIdentity"]["sExecutionPermissionNote"] = (
        "EXECUTION PERMISSION: full sandbox. marker")
    sChannel = agentCouncilCharter.fsComposeChatInstruction(
        dictCampaign, dictCampaign["listParticipants"][0])
    assert "full sandbox" not in sChannel
    assert agentCouncilCharter.S_CHAT_EXECUTION_NOTE in sChannel


def testACampaignConvenedBeforeTheNoteCarriesNone():
    dictCampaign = _fdictCampaign("fullSandbox")
    assert dictCampaign["dictProjectIdentity"][
        "sExecutionPermissionNote"] == ""
    sChannel = agentCouncilCharter.fsComposeTurnInstruction(
        dictCampaign, dictCampaign["listParticipants"][0],
        agentCouncilCharter.S_PHASE_PROPOSAL)
    assert "EXECUTION PERMISSION" not in sChannel
