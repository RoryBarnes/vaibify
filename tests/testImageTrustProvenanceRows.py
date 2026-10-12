"""Every provenance row the image-trust question shows describes the image it names.

A reader who obtains the author's pinned image and adds agents is asked
about the image vaibify STACKED on the obtained base. The deposit
delivered the base, not the stack, so "obtained from the archive" and
"matches the deposit's recorded digest" are true of the base and false
of the stack. The rows are the server's words (the modal and the
command line render them); these tests hold them to the image each one
names, with the base, the stack and the pin all distinct IDs.
"""

import pytest

from vaibify.cli import imageTrustPrompt
from vaibify.config import imageOrigins, imageTrust

S_PIN = "sha256:" + "1" * 64
S_BASE_ID = "sha256:8f54" + "0" * 60
S_STACKED_ID = "sha256:ee65" + "0" * 60
S_STRANGER_ID = "sha256:" + "5" * 64
S_DOI = "10.5281/zenodo.1"


def fdictOrigin():
    return imageOrigins.fdictBuildOriginRecord(
        S_PIN, S_BASE_ID, S_STACKED_ID, ["claude", "gemini", "node"],
        "archive", "zenodo", S_DOI, "linux/amd64", "linux/amd64", False,
    )


def fdictObtainedProject(listAuthorOverlays=("claude",)):
    dictSource = {"sSource": "archive"}
    if listAuthorOverlays is not None:
        dictSource["listAuthorOverlays"] = list(listAuthorOverlays)
    return {"sName": "reader", "dictImageSource": dictSource}


def fdictImage(sId):
    return {"sId": sId, "dictLabels": {}, "iSizeBytes": 1,
            "sUser": "researcher", "listEntrypoint": ["/entry"]}


def flistBuildRowsFor(sImageId, dictProject=None, dictOrigin=None):
    dictPrompt = imageTrust.fdictBuildTrustPrompt(
        dictProject or fdictObtainedProject(), fdictImage(sImageId),
        fdictOrigin() if dictOrigin is None else dictOrigin,
    )
    assert dictPrompt["sImageDigest"] == sImageId
    return [(dictRow["sLabel"], dictRow["sValue"])
            for dictRow in dictPrompt["listProvenanceRows"]]


@pytest.mark.falsification
def testAStackedImageNeverClaimsTheBasesProvenanceAsItsOwn():
    """Kills: the stacked image's rows saying it was obtained from the
    archive and matches the deposit's digest -- facts of the base.

    The digest row names the stack; every origin fact names the base,
    by its own ID.
    """
    assert S_BASE_ID != S_STACKED_ID
    listRows = flistBuildRowsFor(S_STACKED_ID)
    dictRows = dict(listRows)
    assert "Obtained from" not in dictRows
    assert "Matches the deposit's recorded digest" not in dictRows
    assert listRows == [
        ("Built by", "vaibify, on the base image below"),
        ("Added by vaibify", "gemini, node"),
        ("Base image", S_BASE_ID),
        ("Base obtained from", "archive"),
        ("Base matches the deposit's recorded digest", "yes"),
    ]


@pytest.mark.falsification
def testTheAddedRowNamesOnlyWhatVaibifyStacked():
    """Kills: the author's own overlays listed as vaibify's additions."""
    dictRows = dict(flistBuildRowsFor(S_STACKED_ID))
    assert "claude" not in dictRows["Added by vaibify"]


def testWithoutTheAuthorsSetTheAddedRowSaysItIsNotRecorded():
    """No proven baseline in the registry: no agent is named as added."""
    dictRows = dict(flistBuildRowsFor(
        S_STACKED_ID, dictProject=fdictObtainedProject(None)))
    assert dictRows["Added by vaibify"] == (
        "agent overlays (which ones is not recorded)")


def testTheObtainedBaseStatesItsOwnOrigin():
    assert flistBuildRowsFor(S_BASE_ID) == [
        ("Obtained from", "archive"),
        ("Matches the deposit's recorded digest", "yes"),
    ]


@pytest.mark.falsification
def testAnImageTheRecordDoesNotNameHasNoRecordedOrigin():
    """Kills: describing any image as stacked on the base once a record
    exists, whatever image the tag now names."""
    assert flistBuildRowsFor(S_STRANGER_ID) == [
        ("Obtained from", "unknown"),
        ("Matches the deposit's recorded digest",
         "no record to compare against"),
    ]


def testWithNoOriginRecordNothingIsClaimed():
    assert flistBuildRowsFor(S_BASE_ID, dictOrigin={}) == [
        ("Obtained from", "unknown"),
        ("Matches the deposit's recorded digest",
         "no record to compare against"),
    ]


@pytest.mark.falsification
def testTheCommandLinePrintsTheServersRows(capsys):
    """Kills: the command line printing its own origin line instead of
    the server's rows, which said the stack came from the archive."""
    imageTrustPrompt._fnPrintPrompt(imageTrust.fdictBuildTrustPrompt(
        fdictObtainedProject(), fdictImage(S_STACKED_ID), fdictOrigin()))
    sPrinted = capsys.readouterr().out
    assert f"  Base image: {S_BASE_ID}\n" in sPrinted
    assert "  Base obtained from: archive\n" in sPrinted
    assert "  Obtained from:" not in sPrinted
