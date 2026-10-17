"""The shadow rerun and ``reproduce.sh`` agree inside REAL containers.

The host parity test (``tests/testReproduceScriptParity.py``) runs both
lanes on this machine's interpreter. This leg runs each in a real
container built from one image with ``python3``, ``pytest`` and Pillow:

* the shadow, through ``vaibify reproduce --from ... --rerun``, which
  stages the committed project, loads the image from a loopback deposit
  and runs the rerun in a disposable container;
* the reader's lane, the project's own ``reproduce.sh`` run by ``bash``
  against the real daemon, mounting the published tree.

The script obtains its image the way a stranger's would when the
registry has forgotten it, except for where the bytes come from: its
deposit fetch may contact only Zenodo's hosts, so it cannot reach the
loopback deposit the shadow used. A ``docker`` stand-in on PATH maps
the envelope's pinned reference to the image the shadow just loaded and
hands every other argument to the real ``docker`` unchanged, and a
``curl`` stand-in fails at once, so nothing reaches the network. Every
command of the container half runs for real.

Each case must produce the verdict the precedence table gives it, the
same exit code from both lanes, and the same rendered verdict and tally.
A command's exit status cannot be read out of the script's container
(its evidence directory is removed when it exits), so the statuses are
compared in the host parity test; here the rendered verdict names any
command that failed.

Skipped automatically when no daemon is reachable, unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import hashlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from tests.reproductionSourceFixtures import (
    S_FIXTURE_WORKFLOW_PATH,
    fdictBuildEnvelope,
    fnCommitEverything,
    fnWriteJson,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from tests.rerunAssessmentHarness import S_PYTEST, fdictBuildWorkflow
from tests.testAssessedRerunLive import _S_ASSESSED_DOCKERFILE
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from tests.testImageAcquisition import LoopbackDeposit, fnPointZenodoAt
from tests.testReproduceScriptDeterminism import _fbBindMountReachesTheDaemon
from tests.testReproduceScriptParity import _LIST_CASES
from tests.testReproductionLive import (
    S_TARBALL_NAME,
    S_UNSERVED_REFERENCE,
    _fbaSaveImage,
    _fimageBuildProbeImage,
    _fsFreshScratch,
)
from vaibify.cli.commandReproduce import fnReproduceCommand
from vaibify.docker import disposableContainer
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility import reproductionSource
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    ftRenderReproductionFiles,
)


pytestmark = pytest.mark.docker_live

S_PARITY_TAG = "vaibify-reproduce-parity-probe:live"
S_DOI = "10.5281/zenodo.7000029"
S_IN_CONTAINER_PYTEST = "python3 -m pytest -q -p no:cacheprovider"


@pytest.fixture(scope="module")
def dictParityImage(tmp_path_factory):
    """Build the image once, save its tarball, and remove the build's tag."""
    fnRequireDaemonReachable()
    dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    imageProbe = _fimageBuildProbeImage(
        dockerDisposable, _S_ASSESSED_DOCKERFILE, S_PARITY_TAG)
    baTarball = _fbaSaveImage(imageProbe)
    pathZenodo = tmp_path_factory.mktemp("zenodo")
    pathFiles = pathZenodo / S_DOI / "files"
    pathFiles.mkdir(parents=True)
    (pathFiles / S_TARBALL_NAME).write_bytes(baTarball)
    dictArchive = {
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.7000028",
        "sTarballName": S_TARBALL_NAME,
        "sTarballSha256": "sha256:" + hashlib.sha256(baTarball).hexdigest(),
        "iTarballBytes": len(baTarball), "sProvenance": "original",
        "sImageDigest": S_UNSERVED_REFERENCE,
        "sArchitecture": str(imageProbe.attrs.get("Architecture") or ""),
    }
    del baTarball
    try:
        dockerDisposable.images.remove(S_PARITY_TAG, force=True)
        yield {"sImageId": imageProbe.id, "pathZenodo": pathZenodo,
               "dictArchive": dictArchive}
    finally:
        try:
            dockerDisposable.images.remove(imageProbe.id, force=True)
        except Exception:  # noqa: BLE001 -- cleanup only
            pass


def _fdictInContainerCommands(dictStep):
    """Return the step with its test commands run by the image's pytest."""
    for dictCategory in dictStep.get("dictTests", {}).values():
        dictCategory["saCommands"] = [
            sCommand.replace(S_PYTEST, S_IN_CONTAINER_PYTEST)
            for sCommand in dictCategory["saCommands"]]
    return dictStep


def _flistFilesUnder(sRoot, sDirectory):
    """Return every file under one directory, repository-relative."""
    listPaths = []
    for sCurrent, _listDirectories, listFiles in os.walk(
            os.path.join(sRoot, sDirectory)):
        listPaths += [os.path.relpath(os.path.join(sCurrent, sName), sRoot)
                      for sName in listFiles]
    return sorted(listPaths)


def _fdictPublishTheProject(sRepoPath, flistBuildSteps, dictArchive):
    """Commit a project: the case's step, its script and assessor, pinned."""
    os.makedirs(sRepoPath)
    fsRunGit(["init", "--quiet"], sRepoPath)
    fsRunGit(["symbolic-ref", "HEAD", "refs/heads/main"], sRepoPath)
    listSteps = [_fdictInContainerCommands(dictStep)
                 for dictStep in flistBuildSteps(sRepoPath)]
    dictWorkflow = fdictBuildWorkflow(listSteps)
    dictWorkflow["sPlotDirectory"] = "figures"
    fnWriteJson(sRepoPath, S_FIXTURE_WORKFLOW_PATH, dictWorkflow)
    dictEnvelope = fdictBuildEnvelope(dictArchiveRecord=dictArchive)
    dictEnvelope["dictContainer"]["sImageDigest"] = S_UNSERVED_REFERENCE
    dictEnvelope["dictContainer"]["sArchitecture"] = dictArchive[
        "sArchitecture"]
    fnWriteJson(sRepoPath, ".vaibify/environment.json", dictEnvelope)
    fnWriteText(sRepoPath, "requirements.lock", "")
    for sRelative, sText in ftRenderReproductionFiles(dictWorkflow):
        fnWriteText(sRepoPath, sRelative, sText)
    fnWriteManifest(sRepoPath, _flistFilesUnder(sRepoPath, "StepOne"))
    fnCommitEverything(sRepoPath, "publish the case")
    return dictWorkflow


def _fnInstallImageStandIns(sBin, sImageId, listRunArguments=()):
    """Map the pinned reference to the loaded image; refuse every fetch.

    ``listRunArguments`` are added to ``docker run``, for a test that
    restricts the container (``--cpuset-cpus``, say).
    """
    os.makedirs(sBin)
    sRealDocker = shutil.which("docker")
    with open(os.path.join(sBin, "docker"), "w") as fileOut:
        fileOut.write(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = image ] || [ "$1" = run ]; then\n'
            "    aArguments=()\n"
            '    for sArgument in "$@"; do\n'
            f'        if [ "$sArgument" = "{S_UNSERVED_REFERENCE}" ]; then\n'
            f'            aArguments+=("{sImageId}")\n'
            "        else\n"
            '            aArguments+=("$sArgument")\n'
            "        fi\n"
            "    done\n"
            '    set -- "${aArguments[@]}"\n'
            "fi\n"
            'if [ "$1" = run ]; then\n'
            "    shift\n"
            f'    set -- run {shlex.join(listRunArguments)} "$@"\n'
            "fi\n"
            f'exec "{sRealDocker}" "$@"\n')
    with open(os.path.join(sBin, "curl"), "w") as fileOut:
        fileOut.write("#!/usr/bin/env bash\nexit 1\n")
    for sName in ("docker", "curl"):
        os.chmod(os.path.join(sBin, sName), 0o755)


def _fnLetTheImagesUserWrite(sRoot):
    """Open the tree to the image's user, whose uid may not be this one."""
    for sCurrent, _listDirectories, listFiles in os.walk(sRoot):
        os.chmod(sCurrent, 0o777)
        for sName in listFiles:
            os.chmod(os.path.join(sCurrent, sName), 0o666)


def _fdictReadTheOnlyReport(sShadowOutput):
    """Return the one reproduction report the shadow wrote."""
    sDirectory = reproductionReport.fsReportsDirectory()
    assert os.path.isdir(sDirectory), sShadowOutput
    listReports = sorted(
        sName for sName in os.listdir(sDirectory) if sName.endswith(".json"))
    assert len(listReports) == 1
    with open(os.path.join(sDirectory, listReports[0])) as fileIn:
        return json.load(fileIn)


@pytest.mark.parametrize("flistBuildSteps, sVerdict, iExitCode", _LIST_CASES)
def testTheShadowAndTheScriptGradeOneTreeAlike(
    dictParityImage, tmp_path, monkeypatch, flistBuildSteps, sVerdict,
    iExitCode,
):
    """Same verdict, exit code, rendered verdict and tally from both lanes."""
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot])
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: _fsFreshScratch(tmp_path))
    sRepoPath = os.path.join(sRoot, "project")
    _fdictPublishTheProject(
        sRepoPath, flistBuildSteps, dictParityImage["dictArchive"])
    if not _fbBindMountReachesTheDaemon(pathlib.Path(sRepoPath)):
        pytest.skip(
            "the daemon cannot see " + sRepoPath + " through a bind mount, "
            "so /work would arrive empty; run with a temporary directory "
            "inside a shared path")
    with LoopbackDeposit(dictParityImage["pathZenodo"]) as server:
        fnPointZenodoAt(monkeypatch, server)
        resultShadow = CliRunner().invoke(
            fnReproduceCommand, ["--from", sRepoPath, "--rerun"])
    dictReport = _fdictReadTheOnlyReport(resultShadow.output)
    assert dictReport["sVerdict"] == sVerdict, resultShadow.output
    assert resultShadow.exit_code == iExitCode, resultShadow.output
    _fnLetTheImagesUserWrite(sRepoPath)
    sBin = os.path.join(sRoot, "standIns")
    _fnInstallImageStandIns(sBin, dictParityImage["sImageId"])
    dictEnvironment = dict(os.environ)
    dictEnvironment["PATH"] = sBin + os.pathsep + dictEnvironment["PATH"]
    completedScript = subprocess.run(
        ["bash", S_REPRODUCE_SCRIPT_FILENAME], cwd=sRepoPath,
        env=dictEnvironment, capture_output=True, text=True, timeout=900)
    sScriptOutput = completedScript.stdout + completedScript.stderr
    assert completedScript.returncode == iExitCode, sScriptOutput
    assert dictReport["sVerdictRendered"] in completedScript.stdout, (
        sScriptOutput)
    assert dictReport["sTallyRendered"] in completedScript.stdout, (
        sScriptOutput)
