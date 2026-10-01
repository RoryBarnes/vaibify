"""The L3 rerun must regenerate every output it grades.

The shadow holds a copy of the WHOLE repository, outputs included. A
step that exits zero without writing used to leave every pinned output
exactly as the archive had it, so every hash matched and the attestation
read ``passed N of N`` for a run that computed nothing. These tests drive
the real preparation program (a ``python3`` subprocess against a real
directory) and the real comparison; only the pipeline runner is a
stand-in, because what a step WRITES is exactly the variable under test.

Each stand-in asserts, at the moment the run begins, what the shadow
looks like then. A test that only inspected the outcome afterwards could
not tell "the run wrote it" from "the archive left it there".
"""

import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import pipelineRunner
from vaibify.reproducibility import rerunPreparation
from vaibify.reproducibility.l3Attestation import fdictBuildAttestation
from vaibify.reproducibility.rerunPreparation import (
    fdictClassifyRerunPaths,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    flistRenderStepCommands,
)
from vaibify.reproducibility.rerunVerification import (
    S_DIVERGENCE_NO_OUTPUT_REGENERABLE,
    S_FILE_MATCHED,
    S_FILE_MISSING,
    S_ROLE_OUTPUT,
    S_ROLE_PINNED_INPUT,
    fdictRerunAndVerifyWorkflow,
)


S_RUNNER_PATCH_TARGET = (
    "vaibify.reproducibility.rerunVerification.fbRunWorkflowInContainer"
)
S_ANSWER = "answer = 42\n"


def _fnWriteFile(pathRepo, sRelative, sText):
    pathFile = pathRepo / sRelative
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_text(sText)
    return sText.encode("utf-8")


def _fnPinManifest(pathRepo, listRelativePaths):
    """Pin the CURRENT bytes of each path, as the author's manifest does."""
    sLines = "".join(
        hashlib.sha256((pathRepo / sPath).read_bytes()).hexdigest()
        + "  " + sPath + "\n"
        for sPath in listRelativePaths
    )
    (pathRepo / "MANIFEST.sha256").write_text(sLines)


def _fsWorkflowPathIn(pathRepo):
    return str(pathRepo) + "/.vaibify/projects/project.json"


def _fdictStep(sName, saOutputs, **dictExtra):
    dictStep = {
        "sName": sName, "bRunEnabled": True,
        "saCommands": ["true"], "saOutputDataFiles": list(saOutputs),
    }
    dictStep.update(dictExtra)
    return dictStep


@pytest.fixture
def pathPinnedRepo(tmp_path):
    """A repo pinning one regenerable output and one script."""
    _fnWriteFile(tmp_path, "result.txt", S_ANSWER)
    _fnWriteFile(tmp_path, "make.py", "print('answer = 42')\n")
    _fnPinManifest(tmp_path, ["make.py", "result.txt"])
    return tmp_path


def _fdictRerun(pathRepo, dictWorkflow, fbRun):
    with patch(S_RUNNER_PATCH_TARGET, side_effect=fbRun):
        return fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow, _fsWorkflowPathIn(pathRepo),
            str(pathRepo),
        )


def _fbRunThatWritesNothing(*taArguments, **dictArguments):
    return True


def _ffbRunThatRegenerates(pathRepo, dictRelativeToText):
    """A runner stand-in that proves the outputs were gone, then writes."""
    def fbRegenerate(*taArguments, **dictArguments):
        for sRelative in dictRelativeToText:
            assert not (pathRepo / sRelative).exists(), (
                f"{sRelative} was still in the shadow when the run began"
            )
        for sRelative, sText in dictRelativeToText.items():
            _fnWriteFile(pathRepo, sRelative, sText)
        return True
    return fbRegenerate


def _fdictOutcomeByPath(dictOutcome):
    return {
        dictFile["sPath"]: dictFile
        for dictFile in dictOutcome["listFileOutcomes"]
    }


@pytest.mark.falsification
def testAStepThatExitsZeroAndWritesNothingIsMissing(pathPinnedRepo):
    """A pinned output the run never wrote must read ``missing``.

    Before the fix the shadow still held the archive's copy, the hash
    matched, and the attestation passed.

    Kills: rerunVerification.fdictRerunAndVerifyWorkflow: the pre-run
    deletion `dictPreRerunClearing = fdictClearShadowBeforeRerun(...)`
    replaced by `dictPreRerunClearing = {}`.
    """
    dictWorkflow = {"listSteps": [_fdictStep("Compute", ["result.txt"])]}
    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, _fbRunThatWritesNothing,
    )
    assert dictOutcome["bPassed"] is False
    dictResult = _fdictOutcomeByPath(dictOutcome)["result.txt"]
    assert dictResult["sStatus"] == S_FILE_MISSING
    assert dictResult["sObserved"] is None
    assert dictOutcome["iOutputHashesMatched"] == 0
    assert dictOutcome["iOutputHashesTotal"] == 1
    assert "result.txt" in dictOutcome["listDivergedHashes"]


def testAStepThatRegeneratesIdenticalBytesMatches(pathPinnedRepo):
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], saDataCommands=["python make.py"],
    )]}
    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow,
        _ffbRunThatRegenerates(pathPinnedRepo, {"result.txt": S_ANSWER}),
    )
    assert dictOutcome["bPassed"] is True
    dictByPath = _fdictOutcomeByPath(dictOutcome)
    assert dictByPath["result.txt"]["sStatus"] == S_FILE_MATCHED
    assert dictByPath["result.txt"]["sRole"] == S_ROLE_OUTPUT
    assert dictByPath["make.py"]["sRole"] == S_ROLE_PINNED_INPUT
    assert dictOutcome["iOutputHashesMatched"] == 1
    assert dictOutcome["iOutputHashesTotal"] == 1
    assert dictOutcome["iPinnedInputsUnchanged"] == 1
    assert dictOutcome["iPinnedInputsTotal"] == 1
    assert dictOutcome["dictPreRerunClearing"]["listDeletedOutputs"] == [
        "result.txt"]


def testAScriptListedInTheManifestIsNeverDeleted(pathPinnedRepo):
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], saDataCommands=["python make.py"],
    )]}
    _fdictRerun(
        pathPinnedRepo, dictWorkflow,
        _ffbRunThatRegenerates(pathPinnedRepo, {"result.txt": S_ANSWER}),
    )
    assert (pathPinnedRepo / "make.py").read_text() == (
        "print('answer = 42')\n")


def testARewrittenPinnedInputIsADivergenceNotARegeneration(pathPinnedRepo):
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], saDataCommands=["python make.py"],
    )]}

    def fbRewritesItsOwnScript(*taArguments, **dictArguments):
        _fnWriteFile(pathPinnedRepo, "result.txt", S_ANSWER)
        _fnWriteFile(pathPinnedRepo, "make.py", "print('changed')\n")
        return True

    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, fbRewritesItsOwnScript,
    )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["iOutputHashesMatched"] == 1
    assert dictOutcome["iPinnedInputsUnchanged"] == 0
    assert "make.py" in dictOutcome["listDivergedHashes"]


def testAChainWhereStepTwoConsumesStepOnesOutputPasses(tmp_path):
    _fnWriteFile(tmp_path, "one.txt", "one\n")
    _fnWriteFile(tmp_path, "two.txt", "two\n")
    _fnPinManifest(tmp_path, ["one.txt", "two.txt"])
    dictWorkflow = {"listSteps": [
        _fdictStep("First", ["one.txt"]),
        _fdictStep(
            "Second", ["two.txt"], saInputDataFiles=["one.txt"],
        ),
    ]}
    dictOutcome = _fdictRerun(
        tmp_path, dictWorkflow,
        _ffbRunThatRegenerates(
            tmp_path, {"one.txt": "one\n", "two.txt": "two\n"}),
    )
    assert dictOutcome.get("bRerunAttempted", True) is True
    assert dictOutcome["bPassed"] is True
    assert sorted(dictOutcome["dictPreRerunClearing"][
        "listDeletedOutputs"]) == ["one.txt", "two.txt"]


def testAGeneratedIntermediateIsNotProtected():
    dictClassification = fdictClassifyRerunPaths({"listSteps": [
        _fdictStep("First", ["one.txt"]),
        _fdictStep("Second", ["two.txt"], saInputDataFiles=["one.txt"]),
    ]})
    assert dictClassification["setProduced"] == {"one.txt", "two.txt"}
    assert "one.txt" not in dictClassification["setProtected"]


def testARawInputNoStepProducesIsProtected():
    dictClassification = fdictClassifyRerunPaths({"listSteps": [
        _fdictStep("Only", ["out.txt"], saInputDataFiles=["raw/in.csv"]),
    ]})
    assert "raw/in.csv" in dictClassification["setProtected"]
    assert "MANIFEST.sha256" in dictClassification["setProtected"]
    assert "Dockerfile" in dictClassification["setProtected"]


@pytest.mark.falsification
def testACarriedOutputAnExecutedStepAlsoDeclaresIsRefusedByName(
    pathPinnedRepo,
):
    """Keeping a given file and counting it regenerated is the false pass.

    Kills: rerunPreparation._fnRefuseAmbiguousOverlap: `if setAmbiguous:`
    neutralized to `if False:`.
    """
    _fnWriteFile(pathPinnedRepo, "shared.json", "{}\n")
    _fnPinManifest(pathPinnedRepo, ["result.txt", "shared.json"])
    dictWorkflow = {"listSteps": [
        {"sName": "Collect", "bInteractive": True,
         "saOutputDataFiles": ["shared.json"]},
        _fdictStep("Compute", ["result.txt", "shared.json"]),
    ]}
    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, _fbRunThatWritesNothing,
    )
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["bPassed"] is False
    sReason = dictOutcome["listDivergedHashes"][0]
    assert "shared.json" in sReason
    assert "cannot be graded" in sReason, (
        "the overlap is named as an overlap, not as a failed delete")
    assert (pathPinnedRepo / "result.txt").read_text() == S_ANSWER, (
        "a refusal must leave the shadow untouched"
    )


def testAnExecutedStepMayNotDeclareAnEnvironmentFileAsAnOutput(
    pathPinnedRepo,
):
    _fnWriteFile(pathPinnedRepo, "Dockerfile", "FROM scratch\n")
    dictWorkflow = {"listSteps": [
        _fdictStep("Compute", ["result.txt", "Dockerfile"])]}
    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, _fbRunThatWritesNothing,
    )
    assert dictOutcome["bRerunAttempted"] is False
    sReason = dictOutcome["listDivergedHashes"][0]
    assert "Dockerfile" in sReason and "cannot be graded" in sReason
    assert (pathPinnedRepo / "Dockerfile").exists()


def testAManifestWithNoRegenerableOutputIsNotAPass(tmp_path):
    _fnWriteFile(tmp_path, "make.py", "pass\n")
    _fnPinManifest(tmp_path, ["make.py"])
    dictWorkflow = {"listSteps": [
        _fdictStep("Compute", [], saDataCommands=["python make.py"])]}
    dictOutcome = _fdictRerun(tmp_path, dictWorkflow, _fbRunThatWritesNothing)
    assert dictOutcome["bPassed"] is False
    assert S_DIVERGENCE_NO_OUTPUT_REGENERABLE in dictOutcome[
        "listDivergedHashes"]


# ---------------------------------------------------------------------
# Scratch directories
# ---------------------------------------------------------------------


@pytest.fixture
def pathCachingRepo(tmp_path):
    """A step whose cache holds exactly the pinned answer."""
    _fnWriteFile(tmp_path, "stepDir/result.txt", S_ANSWER)
    _fnWriteFile(tmp_path, "stepDir/cache/result.txt", S_ANSWER)
    _fnPinManifest(tmp_path, ["stepDir/result.txt"])
    return tmp_path


@pytest.mark.falsification
def testACacheInAScratchDirectoryCannotShortcutRegeneration(
    pathCachingRepo,
):
    """The step copies from its cache when it can; the cache must be gone.

    Kills: rerunPreparation.fdictClearShadowBeforeRerun: the job's
    `"listScratch": flistResolveScratchRepoPaths(...)` replaced by
    `"listScratch": []`.

    The stand-in computes a DIFFERENT answer when it has to compute, so
    a surviving cache would make the run match and a cleared one makes
    it diverge: the divergence is the proof the shortcut is closed.
    """
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], sDirectory="stepDir",
        saScratchDirs=["cache"],
    )]}
    listCacheSeen = []

    def fbCopyFromCacheElseCompute(*taArguments, **dictArguments):
        pathCache = pathCachingRepo / "stepDir" / "cache" / "result.txt"
        listCacheSeen.append(pathCache.exists())
        sText = pathCache.read_text() if pathCache.exists() else "computed\n"
        _fnWriteFile(pathCachingRepo, "stepDir/result.txt", sText)
        return True

    dictOutcome = _fdictRerun(
        pathCachingRepo, dictWorkflow, fbCopyFromCacheElseCompute,
    )
    assert listCacheSeen == [False]
    assert dictOutcome["bPassed"] is False
    assert _fdictOutcomeByPath(dictOutcome)["stepDir/result.txt"][
        "sStatus"] == "diverged"
    assert dictOutcome["dictPreRerunClearing"][
        "listClearedScratch"] == ["stepDir/cache"]


@pytest.mark.falsification
def testAScratchDirectoryHoldingAProtectedScriptIsRefusedAndNothingDeleted(
    tmp_path,
):
    """Clearing a scratch directory must never delete a pinned script.

    Kills: rerunPreparation's in-shadow program: the containment check
    `if fbIsWithin(sProtected, sLocated):` neutralized to `if False:`.
    """
    _fnWriteFile(tmp_path, "work/result.txt", S_ANSWER)
    _fnWriteFile(tmp_path, "work/tmp/helper.py", "pass\n")
    _fnPinManifest(tmp_path, ["work/result.txt", "work/tmp/helper.py"])
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], sDirectory="work",
        saDataCommands=["python tmp/helper.py"], saScratchDirs=["tmp"],
    )]}
    dictOutcome = _fdictRerun(tmp_path, dictWorkflow, _fbRunThatWritesNothing)
    assert dictOutcome["bRerunAttempted"] is False
    sReason = dictOutcome["listDivergedHashes"][0]
    assert "work/tmp" in sReason and "work/tmp/helper.py" in sReason
    assert (tmp_path / "work" / "tmp" / "helper.py").exists()
    assert (tmp_path / "work" / "result.txt").read_text() == S_ANSWER, (
        "the refusal must come before ANY deletion"
    )


def testASymlinkInAScratchDirectoryNeverReachesItsTarget(tmp_path):
    _fnWriteFile(tmp_path, "inputs/raw.dat", "raw\n")
    _fnWriteFile(tmp_path, "stepDir/result.txt", S_ANSWER)
    (tmp_path / "stepDir" / "cache").mkdir()
    os.symlink(
        str(tmp_path / "inputs" / "raw.dat"),
        str(tmp_path / "stepDir" / "cache" / "link"),
    )
    os.symlink(
        str(tmp_path / "inputs"), str(tmp_path / "stepDir" / "cache" / "dir"))
    _fnPinManifest(tmp_path, ["inputs/raw.dat", "stepDir/result.txt"])
    dictWorkflow = {"listSteps": [_fdictStep(
        "Compute", ["result.txt"], sDirectory="stepDir",
        saInputDataFiles=["inputs/raw.dat"], saScratchDirs=["cache"],
    )]}
    dictOutcome = _fdictRerun(
        tmp_path, dictWorkflow,
        _ffbRunThatRegenerates(
            tmp_path, {"stepDir/result.txt": S_ANSWER}),
    )
    assert dictOutcome["bPassed"] is True
    assert (tmp_path / "inputs" / "raw.dat").read_text() == "raw\n"
    assert not (tmp_path / "stepDir" / "cache").exists()


def testAnOutputReachedThroughASymlinkedParentOfAProtectedFileIsRefused(
    tmp_path,
):
    _fnWriteFile(tmp_path, "inputs/raw.dat", "raw\n")
    os.symlink(str(tmp_path / "inputs"), str(tmp_path / "alias"))
    _fnPinManifest(tmp_path, ["inputs/raw.dat"])
    dictWorkflow = {"listSteps": [
        _fdictStep("Compute", ["alias/raw.dat"],
                   saInputDataFiles=["inputs/raw.dat"])]}
    dictOutcome = _fdictRerun(tmp_path, dictWorkflow, _fbRunThatWritesNothing)
    assert dictOutcome["bRerunAttempted"] is False
    assert (tmp_path / "inputs" / "raw.dat").read_text() == "raw\n"


def testAnOutputOutsideTheRepositoryIsRefused(pathPinnedRepo):
    dictWorkflow = {"listSteps": [
        _fdictStep("Compute", ["result.txt", "/etc/hostname"])]}
    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, _fbRunThatWritesNothing)
    assert dictOutcome["bRerunAttempted"] is False
    assert "/etc/hostname" in dictOutcome["listDivergedHashes"][0]


def testAnUnrunnablePreparationIsRefusedNotSkipped(pathPinnedRepo):
    dictWorkflow = {"listSteps": [_fdictStep("Compute", ["result.txt"])]}
    with patch.object(
        rerunPreparation, "_S_PREPARATION_PROGRAM", "raise SystemExit(7)",
    ):
        dictOutcome = _fdictRerun(
            pathPinnedRepo, dictWorkflow, _fbRunThatWritesNothing)
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["bPassed"] is False


def testAManifestThatMovesDuringTheRerunIsStillDetected(pathPinnedRepo):
    dictWorkflow = {"listSteps": [_fdictStep("Compute", ["result.txt"])]}

    def fbRegenerateAndRepin(*taArguments, **dictArguments):
        _fnWriteFile(pathPinnedRepo, "result.txt", "answer = 43\n")
        _fnPinManifest(pathPinnedRepo, ["make.py", "result.txt"])
        return True

    dictOutcome = _fdictRerun(
        pathPinnedRepo, dictWorkflow, fbRegenerateAndRepin)
    assert dictOutcome["bPassed"] is False
    assert any("MANIFEST" in sLine for sLine in dictOutcome[
        "listDivergedHashes"])


# ---------------------------------------------------------------------
# bPlotOnly
# ---------------------------------------------------------------------


def _fnRunOneStepInMode(sRunMode):
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    mockDocker.ftRunInContainerStreamedWithChunks.return_value = MagicMock(
        iExitCode=0, sStdout="", sStderr="")
    mockDocker.ftRunInContainerStreamed.return_value = MagicMock(
        iExitCode=0, sStdout="", sStderr="")
    dictStep = {
        "sDirectory": "/ws/step", "bPlotOnly": True,
        "saDataCommands": ["python data.py"], "saPlotCommands": [],
    }
    import asyncio
    asyncio.run(pipelineRunner.ftRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, AsyncMock(),
        sRunMode=sRunMode,
    ))
    return " ".join(
        str(call) for call in
        mockDocker.ftResultExecuteCommand.call_args_list
        + mockDocker.ftRunInContainerStreamedWithChunks.call_args_list
        + mockDocker.ftRunInContainerStreamed.call_args_list
    )


@pytest.mark.falsification
def testARerunRunsTheDataCommandsOfAPlotOnlyStep():
    """``bPlotOnly`` skips data commands for an ordinary dashboard run only.

    Kills: pipelineRunner.ftRunStepCommands: `bIgnorePlotOnly=(sRunMode
    == S_RUN_MODE_RERUN)` replaced by `bIgnorePlotOnly=False`.
    """
    assert "data.py" in _fnRunOneStepInMode(pipelineRunner.S_RUN_MODE_RERUN)
    assert "data.py" not in _fnRunOneStepInMode("full")


@pytest.mark.falsification
def testTheRerunLaneAsksTheRunnerForTheRerunMode():
    """Kills: rerunVerification.fbRunWorkflowInContainer: the
    `sRunMode=S_RUN_MODE_RERUN` argument removed."""
    from vaibify.reproducibility import rerunVerification
    dictSeen = {}

    async def fiRecordingRun(*taArguments, **dictArguments):
        dictSeen.update(dictArguments)
        return 0

    with patch("vaibify.gui.pipelineRunner.fiRunAllSteps", fiRecordingRun):
        assert rerunVerification.fbRunWorkflowInContainer(
            None, "cid", {}, "/r/.vaibify/w.json", "/r/.vaibify",
        ) is True
    assert dictSeen["sRunMode"] == pipelineRunner.S_RUN_MODE_RERUN
    assert pipelineRunner.S_RUN_MODE_RERUN not in (
        pipelineRunner.SET_VALID_RUN_MODES), (
        "a browser or an agent must not be able to request the rerun mode"
    )


# ---------------------------------------------------------------------
# reproduce.sh and the rerun execute the same steps
# ---------------------------------------------------------------------


def _fdictWorkflowWithEveryStepKind():
    return {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            {"sName": "Automated", "bRunEnabled": True,
             "saDataCommands": ["python data.py"], "saPlotCommands": []},
            {"sName": "HumanDriven", "bInteractive": True,
             "saDataCommands": ["python human.py"], "saPlotCommands": []},
            {"sName": "PlotOnlyWithData", "bRunEnabled": True,
             "bPlotOnly": True, "saDataCommands": ["python more.py"],
             "saPlotCommands": ["python plot.py"]},
        ],
    }


def testReproduceScriptAndRerunExecuteTheSameSteps():
    """The oracle is the runner's own step loop, not the shared selector."""
    import asyncio
    dictWorkflow = _fdictWorkflowWithEveryStepKind()
    listRunnerSteps = []

    async def fiRecordOneStep(
        connectionDocker, sContainerId, dictStep, *taArguments, **dictKw,
    ):
        listRunnerSteps.append(dictStep["sName"])
        return 0

    async def fiSkipInteractive(*taArguments, **dictArguments):
        return 0

    with patch.object(pipelineRunner, "_fiRunOneStep", fiRecordOneStep), \
            patch(
                "vaibify.gui.interactiveSteps._fiHandleInteractiveStep",
                fiSkipInteractive):
        asyncio.run(pipelineRunner._fiRunStepList(
            MagicMock(), "cid", dictWorkflow, "/ws", {}, AsyncMock(),
        ))
    listScriptSteps = [
        sLine[len("# Step: "):]
        for sLine in flistRenderStepCommands(dictWorkflow)
        if sLine.startswith("# Step: ")
    ]
    assert listScriptSteps == listRunnerSteps == [
        "Automated", "PlotOnlyWithData"]


# ---------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------


def testTheAttestationRecordsThePinnedInputSplitAndTheDeletions():
    dictAttestation = fdictBuildAttestation(
        "passed", "sha256:m", "img@sha256:i", 1.0, 1, 1,
        iPinnedInputsUnchanged=4, iPinnedInputsTotal=4,
        dictPreRerunClearing={
            "listDeletedOutputs": ["result.txt"], "listClearedScratch": []},
    )
    assert dictAttestation["iSchemaVersion"] == 6
    assert dictAttestation["iPinnedInputsUnchanged"] == 4
    assert dictAttestation["dictPreRerunClearing"][
        "listDeletedOutputs"] == ["result.txt"]
