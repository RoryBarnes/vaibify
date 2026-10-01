"""Level-gate edges, each asserted from both sides.

A gate that is only ever shown passing proves nothing about whether it
can fail, so every PASS here has a sibling that changes one input --
one byte of a file, one hash, one answer -- and asserts the same gate
FAILS. Fixtures are real repositories under ``tmp_path`` with real
``MANIFEST.sha256`` files hashed from the bytes on disk. No gate
function is stubbed; the only replacement anywhere is the reproduce
script renderer in the single test about a render that raises.
"""

import hashlib
import json

import pytest

from vaibify.reproducibility import levelGates, reproduceScriptGenerator
from vaibify.reproducibility.imageArchive import S_IMAGE_ARCHIVE_KEY


S_STEP_DIRECTORY = "stepAlpha"
S_OUTPUT_NAME = "dataFile.csv"
S_OUTPUT_PATH = S_STEP_DIRECTORY + "/" + S_OUTPUT_NAME
BA_OUTPUT = b"x,y\n1,2\n"
S_IMAGE_DIGEST = "registry.example/projectAlpha@sha256:" + "a" * 64


def fsSha256Hex(baContent):
    """Return the lowercase hex sha256 of some bytes."""
    return hashlib.sha256(baContent).hexdigest()


def fnWriteFile(pathRepo, sRelativePath, baContent):
    """Write bytes at a repo-relative path, creating parent directories."""
    pathFile = pathRepo / sRelativePath
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_bytes(baContent)


def fnWriteManifest(pathRepo, dictPathToHex):
    """Write MANIFEST.sha256 with the given path-to-hex pins."""
    (pathRepo / "MANIFEST.sha256").write_text("".join(
        sHex + "  " + sPath + "\n" for sPath, sHex in dictPathToHex.items()
    ), encoding="utf-8")


def fdictStepWithOneOutput():
    """Return a step declaring one data output in its directory."""
    return {
        "sName": "Step Alpha",
        "sDirectory": S_STEP_DIRECTORY,
        "saOutputDataFiles": [S_OUTPUT_NAME],
        "saPlotFiles": [],
    }


# ── outputs against the manifest (script-stale suppression) ──────


def testOutputsMatchingTheManifestSuppressScriptStaleness(tmp_path):
    """Declared outputs pinned with their true hashes match."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifest(tmp_path, {S_OUTPUT_PATH: fsSha256Hex(BA_OUTPUT)})
    assert levelGates._fbStepHashesMatchManifest(
        fdictStepWithOneOutput(), str(tmp_path),
    ) is True


def testAnOutputThatDriftedFromTheManifestDoesNotMatch(tmp_path):
    """One changed output byte and the same step no longer matches."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT + b"3,4\n")
    fnWriteManifest(tmp_path, {S_OUTPUT_PATH: fsSha256Hex(BA_OUTPUT)})
    assert levelGates._fbStepHashesMatchManifest(
        fdictStepWithOneOutput(), str(tmp_path),
    ) is False


def testNoManifestMeansNoMatch(tmp_path):
    """Without MANIFEST.sha256 the suppression cannot apply."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    assert levelGates._fbStepHashesMatchManifest(
        fdictStepWithOneOutput(), str(tmp_path),
    ) is False


def testAManifestPinningNothingMeansNoMatch(tmp_path):
    """A manifest of comments only has no entries to agree with."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    (tmp_path / "MANIFEST.sha256").write_text("# nothing pinned\n", "utf-8")
    assert levelGates._fbStepHashesMatchManifest(
        fdictStepWithOneOutput(), str(tmp_path),
    ) is False


def testAnOutputTheManifestDoesNotListMeansNoMatch(tmp_path):
    """An output absent from the manifest is not vouched for by it."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifest(tmp_path, {"otherStep/other.csv": fsSha256Hex(b"z")})
    assert levelGates._fbStepHashesMatchManifest(
        fdictStepWithOneOutput(), str(tmp_path),
    ) is False


# ── pinned entries that contradict the files ─────────────────────


def testAPresentFileWithAWrongPinIsAContradiction(tmp_path):
    """A pinned hash the file on disk does not have is reported."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifest(tmp_path, {S_OUTPUT_PATH: fsSha256Hex(b"other bytes")})
    listContradicting = levelGates.flistManifestEntriesContradictingTheFiles(
        str(tmp_path),
    )
    assert [dictEntry["sPath"] for dictEntry in listContradicting] == [
        S_OUTPUT_PATH,
    ]


def testATruePinIsNoContradiction(tmp_path):
    """The same file pinned with its real hash contradicts nothing."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifest(tmp_path, {S_OUTPUT_PATH: fsSha256Hex(BA_OUTPUT)})
    assert levelGates.flistManifestEntriesContradictingTheFiles(
        str(tmp_path),
    ) == []


def testAMalformedManifestContradictsNothingRatherThanRaising(tmp_path):
    """A manifest that does not parse is another gate's business."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    (tmp_path / "MANIFEST.sha256").write_text(
        "this line has no two-space separator\n", "utf-8",
    )
    assert levelGates.flistManifestEntriesContradictingTheFiles(
        str(tmp_path),
    ) == []


# ── the environment-archive question ─────────────────────────────


def fnWriteContainerEnvelope(pathRepo, dictContainer):
    """Write an envelope carrying one container block."""
    fnWriteFile(pathRepo, ".vaibify/environment.json", json.dumps(
        {"dictContainer": dictContainer},
    ).encode("utf-8"))


def testAnEnvelopeWithoutAPinnedImageIsNeverAsked(tmp_path):
    """No image digest means no image to archive: settled."""
    fnWriteContainerEnvelope(tmp_path, {"sContainerName": "containerAlpha"})
    assert levelGates.fbImageArchiveQuestionSettled({}, str(tmp_path)) is True


def testADepositRecordSettlesTheQuestionWithoutAnAnswer(tmp_path):
    """Having deposited is having decided, with no recorded answer."""
    fnWriteContainerEnvelope(tmp_path, {
        "sImageDigest": S_IMAGE_DIGEST,
        S_IMAGE_ARCHIVE_KEY: {"sVersionDoi": "10.5281/zenodo.1"},
    })
    assert levelGates.fbImageArchiveQuestionSettled({}, str(tmp_path)) is True


def testAPinnedImageWithNoRecordAndNoAnswerIsUnsettled(tmp_path):
    """The same envelope without the record, and silence, fails."""
    fnWriteContainerEnvelope(tmp_path, {"sImageDigest": S_IMAGE_DIGEST})
    assert levelGates.fbImageArchiveQuestionSettled({}, str(tmp_path)) is (
        False
    )


# ── the reproduce script's currency ──────────────────────────────


def testAReproduceScriptThatCannotBeRenderedIsNotCalledStale(
    monkeypatch, tmp_path,
):
    """A render that raises passes here; other gates own that failure."""
    fnWriteFile(tmp_path, "reproduce.sh", b"#!/bin/sh\necho old\n")

    def fsRefuseToRender(dictWorkflow):
        raise ValueError("workflow cannot be rendered")

    monkeypatch.setattr(
        reproduceScriptGenerator, "fsRenderReproduceScript", fsRefuseToRender,
    )
    assert levelGates.fbVerifyReproduceScriptCurrent(
        str(tmp_path), {"listSteps": []},
    ) is True


def testAReproduceScriptThatDiffersFromTheRenderIsStale(tmp_path):
    """An on-disk script that is not today's render fails the gate."""
    dictWorkflow = {"sWorkflowName": "workflowAlpha", "listSteps": []}
    sCurrent = reproduceScriptGenerator.fsRenderReproduceScript(dictWorkflow)
    fnWriteFile(tmp_path, "reproduce.sh", sCurrent.encode("utf-8"))
    assert levelGates.fbVerifyReproduceScriptCurrent(
        str(tmp_path), dictWorkflow,
    ) is True
    fnWriteFile(tmp_path, "reproduce.sh", (sCurrent + "# edit\n").encode())
    assert levelGates.fbVerifyReproduceScriptCurrent(
        str(tmp_path), dictWorkflow,
    ) is False


# ── scripts drifted from the manifest ────────────────────────────


def fdictScriptContext(sOnDiskHex):
    """Return a context pinning one script, with one on-disk hash."""
    sScript = S_STEP_DIRECTORY + "/generate.py"
    return {
        "dictManifestPathHashes": {sScript: fsSha256Hex(b"print(1)\n")},
        "dictScriptHashesOnDisk": {sScript: {"sSha256": sOnDiskHex}},
    }


def fdictStepRunningOneScript():
    """Return a step whose one command runs ``generate.py``."""
    return {
        "sName": "Step Alpha", "sDirectory": S_STEP_DIRECTORY,
        "saDataCommands": ["python generate.py", "python unpinned.py"],
        "saPlotCommands": [],
    }


def testAScriptMatchingItsPinHasNotDrifted():
    """A pinned script whose on-disk hash agrees is not listed."""
    assert levelGates._flistStepScriptsDriftedFromManifest(
        fdictStepRunningOneScript(),
        fdictScriptContext(fsSha256Hex(b"print(1)\n")),
    ) == []


@pytest.mark.parametrize("sOnDiskHex", [fsSha256Hex(b"print(2)\n"), None])
def testAScriptDifferingFromOrMissingItsPinHasDrifted(sOnDiskHex):
    """A different hash, or none at all, lists the script as drifted."""
    assert levelGates._flistStepScriptsDriftedFromManifest(
        fdictStepRunningOneScript(), fdictScriptContext(sOnDiskHex),
    ) == [S_STEP_DIRECTORY + "/generate.py"]


# ── corrupt step verification blocks ─────────────────────────────


def testACorruptVerificationBlockReadsAsNoActivityAndNoFailure():
    """A non-dict dictVerification is empty, not a crash."""
    dictStep = {"sName": "Step Alpha", "dictVerification": "corrupt"}
    assert levelGates._fbStepHasNoActivity(dictStep) is True
    assert levelGates._fbStepHasFailedAxis(dictStep) is False
    listRequirements = levelGates._flistStepLevel1Requirements(dictStep, set())
    assert [sKey for sKey, _bMet in listRequirements] == [
        "user-attestation", "timing-clean", "input-data-declared",
    ]
    assert dict(listRequirements)["user-attestation"] is False


def testARealFailedAxisIsSeenAndIsActivity():
    """The same step with one failed test axis is active and failing."""
    dictStep = {
        "sName": "Step Alpha",
        "dictVerification": {"sUnitTest": "failed", "sUser": "untested"},
    }
    assert levelGates._fbStepHasFailedAxis(dictStep) is True
    assert levelGates._fbStepHasNoActivity(dictStep) is False
    assert dict(levelGates._flistStepLevel1Requirements(
        dictStep, set(),
    ))["sUnitTest"] is False
