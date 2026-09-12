"""The shadow rerun seeded from a STAGED SNAPSHOT, and the CLI around it.

Three trees are kept genuinely distinct, as the rerun tests keep
theirs: the researcher's source repository (what ``--from`` names),
the staged clone (what staging made of it), and the shadow copy (what
the rerun writes and the comparison reads). The container stand-in
runs its commands for real against real directories and the fake
daemon materialises the tarball on disk, so the seam under test --
the snapshot archive arriving in the shadow, the platform reaching
the create, the marker written before any step, the comparison rooted
on the shadow -- is exercised by the shipped code with only the daemon
local. Docker itself is exercised in ``tests/testReproductionLive.py``.
"""

import hashlib
import os
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from tests.reproductionSourceFixtures import (
    S_FIXTURE_OUTPUT,
    fnCommitEverything,
    fnWriteText,
    fsBuildPublishedProject,
)
from tests.testRerunVerifiesWhatItRan import (
    FakeDisposableDaemon,
    LocalShellContainer,
)
from vaibify.cli import commandReproduce
from vaibify.cli.commandReproduce import fnReproduceCommand
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility import reproductionSource
from vaibify.reproducibility import shadowRerun
from vaibify.reproducibility.imageArchive import S_LOADED_FROM_ARCHIVE_MARKER
from vaibify.reproducibility.l3Attestation import fbL3AttestationCurrent
from vaibify.reproducibility.reproductionSource import (
    fbaExportStagedSnapshot,
    fdictDescribeStagedSource,
    fdictLoadStagedWorkflow,
    fdictStageSource,
)
from vaibify.reproducibility.shadowRerun import (
    ShadowRerunRefusedError,
    fdictRerunAndVerifyFromSnapshot,
)


S_REQUIRED_PLATFORM = "linux/amd64"


@pytest.fixture(autouse=True)
def pathShadowRoot(tmp_path, monkeypatch):
    """Put the shadow workspace somewhere this machine can create."""
    pathRoot = tmp_path / "shadowRoot"
    pathRoot.mkdir()
    monkeypatch.setattr(shadowRerun, "S_SHADOW_WORKSPACE_ROOT", str(pathRoot))
    return pathRoot


@pytest.fixture
def sSourceRepo(tmp_path, monkeypatch):
    """A published project under the one admitted local root."""
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot],
    )
    sRepoPath = os.path.join(sRoot, "publishedProject")
    fsBuildPublishedProject(sRepoPath)
    return sRepoPath


def _fdictAcquired(sObtainedFrom="registry", bEmulated=False):
    return {
        "sImageReference": (
            "sha256:" + "e" * 64 if sObtainedFrom == "archive"
            else "registry.example/demo@sha256:" + "a" * 64
        ),
        "sObtainedFrom": sObtainedFrom,
        "sRequiredPlatform": S_REQUIRED_PLATFORM,
        "sObtainedPlatform": S_REQUIRED_PLATFORM,
        "sDaemonArchitecture": "arm64" if bEmulated else "amd64",
        "bEmulated": bEmulated,
        "listAttempts": [],
    }


def _fdictHashTree(sRoot):
    """Return ``{relative path: sha256}`` for every file under a tree."""
    dictHashes = {}
    for sParent, _listDirectories, listFiles in os.walk(sRoot):
        for sName in listFiles:
            sPath = os.path.join(sParent, sName)
            with open(sPath, "rb") as fileHandle:
                dictHashes[os.path.relpath(sPath, sRoot)] = hashlib.sha256(
                    fileHandle.read(),
                ).hexdigest()
    return dictHashes


def _fcontextPatchTheDaemon(daemonDisposable, fnRunSideEffect):
    """Patch the daemon and the runner, leaving every other seam real."""
    async def _fiRunAllSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        sWorkdir, fnStatusCallback, **kwargs,
    ):
        fnRunSideEffect(dictWorkflow, sWorkflowPath)
        return 0

    return [
        patch(
            "vaibify.docker.disposableContainer.fdockerCreateDisposableClient",
            return_value=daemonDisposable,
        ),
        patch(
            "vaibify.docker.disposableContainer._fmoduleGetDocker",
            return_value=type("_M", (), {"errors": type(
                "_E", (), {"NotFound": FakeDisposableDaemon._NotFound})}),
        ),
        patch("vaibify.gui.pipelineRunner.fiRunAllSteps", side_effect=_fiRunAllSteps),
    ]


def _fdictRunFromSnapshot(sSourceRepo, dictAcquired, fnRunSideEffect):
    """Stage, export and rerun through the seam; return outcome and facts."""
    dictStaged = fdictStageSource(sSourceRepo)
    sToken = dictStaged["sToken"]
    daemonDisposable = FakeDisposableDaemon()
    listPatches = _fcontextPatchTheDaemon(daemonDisposable, fnRunSideEffect)
    for patcher in listPatches:
        patcher.start()
    try:
        dictOutcome = fdictRerunAndVerifyFromSnapshot(
            LocalShellContainer(), fbaExportStagedSnapshot(sToken),
            dictAcquired, fdictLoadStagedWorkflow(sToken),
            dictStaged["sWorkflowPath"], dictStaged["sRepositoryName"],
            sResourceName=f"reproduction-{sToken}",
        )
    finally:
        for patcher in listPatches:
            patcher.stop()
    return {
        "dictOutcome": dictOutcome, "daemon": daemonDisposable,
        "sToken": sToken, "dictStaged": dictStaged,
    }


def _fnLeaveTheShadowAlone(dictWorkflow, sWorkflowPath):
    del dictWorkflow, sWorkflowPath


def _ffnRewriteTheShadowOutput(sBody):
    def fnRewrite(dictWorkflow, sWorkflowPath):
        sShadowRepo = dictWorkflow["sProjectRepoPath"]
        with open(os.path.join(sShadowRepo, S_FIXTURE_OUTPUT), "w") as fileHandle:
            fileHandle.write(sBody)
    return fnRewrite


# ---------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_comparison_is_rooted_on_the_shadow_never_on_the_staged_clone(
    sSourceRepo, pathShadowRoot,
):
    """A rerun that changes the shadow's output is graded as diverged.

    Kills: pointing the comparison at the nominal snapshot path instead
    of the shadow copy.
    """
    dictRun = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired(), _ffnRewriteTheShadowOutput("2\n"),
    )
    dictOutcome = dictRun["dictOutcome"]
    assert dictOutcome.get("bRerunAttempted", True) is True
    assert dictOutcome["bPassed"] is False
    assert S_FIXTURE_OUTPUT in dictOutcome["listDivergedHashes"]
    sShadowRepo = os.path.join(str(pathShadowRoot), "publishedProject")
    assert os.path.isfile(os.path.join(sShadowRepo, S_FIXTURE_OUTPUT))
    sStagedClone = reproductionSource.fsStagedClonePath(dictRun["sToken"])
    assert os.path.realpath(sStagedClone) != os.path.realpath(sShadowRepo)
    with open(os.path.join(sStagedClone, S_FIXTURE_OUTPUT)) as fileHandle:
        assert fileHandle.read() == "1\n"
    with open(os.path.join(sSourceRepo, S_FIXTURE_OUTPUT)) as fileHandle:
        assert fileHandle.read() == "1\n"


def test_an_untouched_shadow_reproduces(sSourceRepo):
    dictRun = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired(), _fnLeaveTheShadowAlone,
    )
    assert dictRun["dictOutcome"]["bPassed"] is True
    assert dictRun["dictOutcome"]["iOutputHashesTotal"] == 2
    assert dictRun["dictOutcome"]["sShadowTeardown"] == "destroyed"
    assert dictRun["dictOutcome"]["sImageDigest"] == _fdictAcquired()["sImageReference"]


@pytest.mark.falsification
def test_the_create_requests_the_required_platform(sSourceRepo):
    """Kills: composing the create without the platform."""
    dictRun = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired(), _fnLeaveTheShadowAlone,
    )
    sImage, dictKeywords = dictRun["daemon"].listCreated[0]
    assert sImage == _fdictAcquired()["sImageReference"]
    assert dictKeywords["platform"] == S_REQUIRED_PLATFORM


@pytest.mark.falsification
def test_no_credential_port_gpu_or_mount_reaches_the_shadow(
    sSourceRepo, monkeypatch,
):
    """The snapshot's vaibify.yml is data, never the runtime specification.

    Every secret is configured on the host and the snapshot declares
    ports, a GPU and bind mounts; the create the shadow receives
    carries none of them and no network.

    Kills: defaulting the network mode to ``bridge``.
    """
    fnWriteText(sSourceRepo, "vaibify.yml", (
        "sProjectName: published\n"
        "listPorts: ['8888:8888']\n"
        "bGpu: true\n"
        "listBindMounts: ['/etc:/hostEtc']\n"
        "dictFeatures: {bGithubAuth: true, bOverleafAuth: true}\n"
    ))
    fnCommitEverything(sSourceRepo, "declare everything")
    monkeypatch.setattr(
        "vaibify.config.secretManager.fbSecretExists",
        lambda *args, **kwargs: True, raising=False,
    )
    dictRun = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired(), _fnLeaveTheShadowAlone,
    )
    _sImage, dictKeywords = dictRun["daemon"].listCreated[0]
    assert dictKeywords["network_mode"] == "none"
    for sKey in ("volumes", "mounts", "ports", "device_requests",
                 "environment", "runtime", "privileged"):
        assert sKey not in dictKeywords, sKey
    assert dictKeywords["cap_drop"] == ["ALL"]


@pytest.mark.falsification
def test_an_archive_loaded_image_marks_the_shadow_before_any_step(
    sSourceRepo, pathShadowRoot, monkeypatch,
):
    """Kills: writing the marker only when the image came from the registry."""
    listSeen = []

    def fnObserveTheMarker(dictWorkflow, sWorkflowPath):
        listSeen.append(os.path.isfile(os.path.join(
            dictWorkflow["sProjectRepoPath"], S_LOADED_FROM_ARCHIVE_MARKER,
        )))
    dictRun = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired("archive"), fnObserveTheMarker,
    )
    assert listSeen == [True]
    assert dictRun["dictOutcome"]["bPassed"] is True
    # A fresh shadow root for the second run: the fake daemon removes
    # its container record, not the directory the first shadow used.
    pathSecondRoot = pathShadowRoot / "second"
    pathSecondRoot.mkdir()
    monkeypatch.setattr(
        shadowRerun, "S_SHADOW_WORKSPACE_ROOT", str(pathSecondRoot),
    )
    dictRegistry = _fdictRunFromSnapshot(
        sSourceRepo, _fdictAcquired("registry"), fnObserveTheMarker,
    )
    assert listSeen == [True, False]
    assert dictRegistry["dictOutcome"]["bPassed"] is True


# ---------------------------------------------------------------------
# The CLI: --from --rerun and --from --prepare
# ---------------------------------------------------------------------


def _flistPatchTheCli(daemonDisposable, fnRunSideEffect, dictAcquired):
    return _fcontextPatchTheDaemon(daemonDisposable, fnRunSideEffect) + [
        patch(
            "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
            return_value=LocalShellContainer(),
        ),
        patch.object(
            imageAcquisition, "fdictAcquirePinnedImage",
            return_value=dictAcquired,
        ),
    ]


def _fdictInvokeRerun(sSourceRepo, fnRunSideEffect, dictAcquired=None,
                      listExtraArguments=()):
    daemonDisposable = FakeDisposableDaemon()
    listPatches = _flistPatchTheCli(
        daemonDisposable, fnRunSideEffect, dictAcquired or _fdictAcquired(),
    )
    for patcher in listPatches:
        patcher.start()
    try:
        result = CliRunner().invoke(
            fnReproduceCommand,
            ["--from", sSourceRepo, "--rerun", *listExtraArguments],
        )
    finally:
        for patcher in listPatches:
            patcher.stop()
    return {"result": result, "daemon": daemonDisposable}


def _flistStagingTokens():
    sRoot = reproductionSource._fsStagingRoot()
    return sorted(os.listdir(sRoot)) if os.path.isdir(sRoot) else []


def _flistReports():
    """The report FILES only: a report's reproduced manifest sits beside it."""
    sRoot = reproductionReport.fsReportsDirectory()
    if not os.path.isdir(sRoot):
        return []
    return sorted(sName for sName in os.listdir(sRoot) if sName.endswith(".json"))


@pytest.mark.falsification
def test_a_rerun_writes_a_report_and_never_an_attestation(sSourceRepo):
    """The staged clone and the source are byte-identical afterwards.

    Kills: writing the attestation file into the staged clone.
    """
    dictBefore = _fdictHashTree(sSourceRepo)
    assert fbL3AttestationCurrent(sSourceRepo) is False
    dictRun = _fdictInvokeRerun(sSourceRepo, _fnLeaveTheShadowAlone)
    result = dictRun["result"]
    assert result.exit_code == 0, result.output
    assert "Verdict: reproduced" in result.output
    assert "attest" not in result.output.lower().replace(
        "not the author's attestation", "",
    )
    assert _fdictHashTree(sSourceRepo) == dictBefore
    assert fbL3AttestationCurrent(sSourceRepo) is False
    assert not os.path.exists(
        os.path.join(sSourceRepo, ".vaibify", "l3_attestation.json"),
    )
    listReports = _flistReports()
    assert len(listReports) == 1
    dictReport = reproductionReport.fdictReadReproductionReport(
        listReports[0][:-len(".json")],
    )
    assert dictReport["sVerdict"] == "reproduced"
    assert dictReport["dictSource"]["sResolvedCommit"]
    assert sSourceRepo not in open(
        os.path.join(reproductionReport.fsReportsDirectory(), listReports[0]),
    ).read()
    assert _flistStagingTokens() == []


@pytest.mark.falsification
def test_staging_is_deleted_and_the_report_survives_on_every_outcome(
    sSourceRepo,
):
    """Success, divergence and a refused rerun all leave a report and no staging.

    Kills: keeping the staging directory after the run.
    """
    dictSuccess = _fdictInvokeRerun(sSourceRepo, _fnLeaveTheShadowAlone)
    assert dictSuccess["result"].exit_code == 0, dictSuccess["result"].output
    dictDiverged = _fdictInvokeRerun(
        sSourceRepo, _ffnRewriteTheShadowOutput("changed\n"),
    )
    assert dictDiverged["result"].exit_code == 1
    assert "Verdict: diverged" in dictDiverged["result"].output
    with patch.object(
        commandReproduce, "fdictRerunAndVerifyFromSnapshot",
        side_effect=ShadowRerunRefusedError("the image vanished"),
    ):
        dictRefused = _fdictInvokeRerun(sSourceRepo, _fnLeaveTheShadowAlone)
    assert dictRefused["result"].exit_code == 1
    assert "no verdict" in dictRefused["result"].output
    assert _flistStagingTokens() == []
    listVerdicts = sorted(
        reproductionReport.fdictReadReproductionReport(sName[:-5])["sVerdict"]
        for sName in _flistReports()
    )
    assert listVerdicts == ["diverged", "no-verdict", "reproduced"]


def test_an_emulated_run_is_reported_as_reproduced_under_emulation(
    sSourceRepo,
):
    dictRun = _fdictInvokeRerun(
        sSourceRepo, _fnLeaveTheShadowAlone,
        dictAcquired=_fdictAcquired(bEmulated=True),
        listExtraArguments=["--allow-emulation"],
    )
    assert dictRun["result"].exit_code == 0, dictRun["result"].output
    assert "reproduced under emulation (linux/amd64 image on a arm64 host)" in (
        dictRun["result"].output
    )


@pytest.mark.falsification
def test_from_never_spawns_pip_on_the_host(sSourceRepo, monkeypatch):
    """Kills: falling through into the tier loop after the --from lane."""
    listLaunches = []
    fRealRun = subprocess.run

    def fprocessRecordingRun(listArguments, *args, **kwargs):
        listLaunches.append(list(listArguments))
        return fRealRun(listArguments, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", fprocessRecordingRun)
    with patch.object(
        commandReproduce, "_ftRunPipInstall",
        side_effect=AssertionError("pip must never run for --from"),
    ):
        dictRun = _fdictInvokeRerun(sSourceRepo, _fnLeaveTheShadowAlone)
    assert dictRun["result"].exit_code == 0, dictRun["result"].output
    listPipLaunches = [
        listArguments for listArguments in listLaunches
        if any(
            sWord == "pip" or sWord.endswith("/pip")
            for sWord in listArguments[:4]
        ) or listArguments[:3] and listArguments[1:3] == ["-m", "pip"]
    ]
    assert listPipLaunches == []
    assert "[2/5]" not in dictRun["result"].output


def test_prepare_obtains_the_image_and_stops(sSourceRepo):
    dictAcquired = _fdictAcquired("archive")
    with patch.object(
        imageAcquisition, "fdictAcquirePinnedImage", return_value=dictAcquired,
    ), patch.object(
        commandReproduce, "fdictRerunAndVerifyFromSnapshot",
        side_effect=AssertionError("--prepare must not run"),
    ):
        result = CliRunner().invoke(
            fnReproduceCommand, ["--from", sSourceRepo, "--prepare"],
        )
    assert result.exit_code == 0, result.output
    assert "obtained from:   archive" in result.output
    assert "nothing was run" in result.output
    assert _flistStagingTokens() == []
    assert _flistReports() == []


def test_an_acquisition_refusal_is_printed_and_leaves_no_staging(sSourceRepo):
    with patch.object(
        imageAcquisition, "fdictAcquirePinnedImage",
        side_effect=imageAcquisition.ImageAcquisitionRefusedError(
            "no link of the chain yielded the image",
        ),
    ):
        result = CliRunner().invoke(
            fnReproduceCommand, ["--from", sSourceRepo, "--rerun"],
        )
    assert result.exit_code == 1
    assert "Refused: no link" in result.output
    assert _flistStagingTokens() == []
    assert _flistReports() == []


def test_the_described_source_feeds_the_report(sSourceRepo):
    dictRun = _fdictInvokeRerun(sSourceRepo, _fnLeaveTheShadowAlone)
    assert dictRun["result"].exit_code == 0
    dictReport = reproductionReport.fdictReadReproductionReport(
        _flistReports()[0][:-5],
    )
    dictStaged = fdictStageSource(sSourceRepo)
    dictDescribed = fdictDescribeStagedSource(dictStaged["sToken"])
    assert dictReport["dictSource"]["sResolvedCommit"] == dictDescribed["sResolvedCommit"]
    assert dictReport["dictSource"]["sWorkflowName"] == "Demo"
