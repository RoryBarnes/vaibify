"""The assessed rerun inside a REAL shadow container, against a real daemon.

The unit lanes run the assessor's sub-commands through a container
stand-in on this machine. Three things only a real container can show:

* ``prepare``, ``clear`` and ``assess`` run inside the shadow, under its
  own admission, as the image's declared user;
* the published figure is set aside BEFORE the clearing deletes it, so
  the image check has a reference to compare the redrawn figure with;
* the evidence directory vaibify creates is writable by the user the
  steps run as, so the JUnit report pytest writes there lands.

The fixture builds an image from a stock python base with pytest, Pillow
and NumPy installed, saves it as the project's archived deposit, and
reproduces the project with ``vaibify reproduce --from ... --rerun``.
The step regenerates its data byte for byte and redraws its figure with
one pixel one level different, so the honest verdict is "published
comparisons passed; 1 of 2 files differ in their bytes".

Skipped automatically when no daemon is reachable, unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import hashlib
import io
import json
import os

import pytest
from click.testing import CliRunner
from PIL import Image

from tests.reproductionSourceFixtures import (
    fdictBuildEnvelope,
    fnCommitEverything,
    fnWriteJson,
    fnWriteManifest,
    fnWriteText,
    fsBuildPublishedProject,
)
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from tests.testImageAcquisition import LoopbackDeposit, fnPointZenodoAt
from tests.testReproductionLive import (
    S_BASE_IMAGE,
    S_TARBALL_NAME,
    S_UNSERVED_REFERENCE,
    _fbaSaveImage,
    _fimageBuildProbeImage,
    _fsFreshScratch,
)
from vaibify.cli.commandReproduce import fnReproduceCommand
from vaibify.gui import templateManager
from vaibify.docker import disposableContainer
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility import reproductionSource


pytestmark = pytest.mark.docker_live

S_ASSESSED_TAG = "vaibify-assessed-rerun-probe:live"
S_DOI = "10.5281/zenodo.7000019"
S_STEP = "MakeNumbers"
_S_ASSESSED_DOCKERFILE = (
    f"FROM {S_BASE_IMAGE}\n"
    "RUN pip install --no-cache-dir pytest==8.3.3 pillow==11.0.0 "
    "numpy==2.1.3\n"
    "RUN useradd --uid 1000 --create-home researcher\n"
    "USER researcher\n"
)
_S_DATA_COMMAND = (
    "python3 -c \"import json; "
    "open('values.json', 'w').write(json.dumps({'x': 1.0}))\"")
_S_PLOT_COMMAND = (
    "python3 -c \"from PIL import Image; "
    "im = Image.new('RGB', (16, 16), (100, 100, 100)); "
    "im.putpixel((0, 0), (101, 100, 100)); im.save('figure.png')\"")


def _fbaUniformFigure():
    """Return the published figure: uniform, the rerun's minus one pixel."""
    bufferOut = io.BytesIO()
    Image.new("RGB", (16, 16), (100, 100, 100)).save(bufferOut, format="PNG")
    return bufferOut.getvalue()


def _fdictPublishTheAssessedProject(sRepoPath, dictEnvelope):
    """Publish a step with a standard over its data and a redrawn figure."""
    fsBuildPublishedProject(sRepoPath, dictEnvelope=dictEnvelope)
    sTest = f"{S_STEP}/tests/test_quantitative_makeNumbers.py"
    sStandards = f"{S_STEP}/tests/quantitative_standards_makeNumbers.json"
    fnWriteText(sRepoPath, sTest, templateManager.fsBuildQuantitativeTestCode())
    fnWriteJson(sRepoPath, sStandards, {"listStandards": [{
        "sName": "xValue", "sDataFile": "values.json",
        "sAccessPath": "key:x", "fValue": 1.0, "sFormat": "json"}]})
    fnWriteText(sRepoPath, f"{S_STEP}/values.json", json.dumps({"x": 1.0}))
    with open(os.path.join(sRepoPath, S_STEP, "figure.png"), "wb") as fileOut:
        fileOut.write(_fbaUniformFigure())
    fnWriteJson(sRepoPath, ".vaibify/projects/project.json", {
        "sWorkflowName": "Assessed", "sPlotDirectory": "figures",
        "listSteps": [{
            "sName": "Make Numbers", "sStepId": "makeNumbers",
            "sDirectory": S_STEP, "bRunEnabled": True,
            "saDataCommands": [_S_DATA_COMMAND],
            "saOutputDataFiles": ["values.json"],
            "saPlotCommands": [_S_PLOT_COMMAND],
            "saPlotFiles": ["figure.png"],
            "dictTests": {"dictQuantitative": {
                "saCommands": [
                    "python3 -m pytest tests/test_quantitative_makeNumbers.py"],
                "sFilePath": sTest, "sStandardsPath": sStandards}},
        }]})
    fnWriteManifest(sRepoPath, [
        f"{S_STEP}/generate.py", f"{S_STEP}/values.json",
        f"{S_STEP}/figure.png", sTest, sStandards])
    fnCommitEverything(sRepoPath, "pin the assessed project")


@pytest.fixture
def dictAssessedProbe(tmp_path, monkeypatch):
    """A built image with pytest and Pillow, deposited, and a project pinning it."""
    fnRequireDaemonReachable()
    dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    imageProbe = _fimageBuildProbeImage(
        dockerDisposable, _S_ASSESSED_DOCKERFILE, S_ASSESSED_TAG)
    sArchitecture = str(imageProbe.attrs.get("Architecture") or "")
    baTarball = _fbaSaveImage(imageProbe)
    pathFiles = tmp_path / "zenodo" / S_DOI / "files"
    pathFiles.mkdir(parents=True)
    (pathFiles / S_TARBALL_NAME).write_bytes(baTarball)
    dictEnvelope = fdictBuildEnvelope(dictArchiveRecord={
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.7000018",
        "sTarballName": S_TARBALL_NAME,
        "sTarballSha256": "sha256:" + hashlib.sha256(baTarball).hexdigest(),
        "iTarballBytes": len(baTarball), "sProvenance": "original",
        "sImageDigest": S_UNSERVED_REFERENCE, "sArchitecture": sArchitecture,
    })
    dictEnvelope["dictContainer"]["sImageDigest"] = S_UNSERVED_REFERENCE
    dictEnvelope["dictContainer"]["sArchitecture"] = sArchitecture
    sRoot = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(
        reproductionSource, "flistAdmittedLocalCloneRoots", lambda: [sRoot])
    sRepoPath = os.path.join(sRoot, "assessedProject")
    _fdictPublishTheAssessedProject(sRepoPath, dictEnvelope)
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: _fsFreshScratch(tmp_path))
    try:
        dockerDisposable.images.remove(S_ASSESSED_TAG, force=True)
        yield {"sRepoPath": sRepoPath, "pathZenodo": tmp_path / "zenodo"}
    finally:
        for sReference in (S_ASSESSED_TAG, imageProbe.id):
            try:
                dockerDisposable.images.remove(sReference, force=True)
            except Exception:  # noqa: BLE001 -- cleanup only
                pass


def _fdictReadTheOnlyReport():
    """Return the one reproduction report this test wrote."""
    sDirectory = reproductionReport.fsReportsDirectory()
    listReports = sorted(
        sName for sName in os.listdir(sDirectory) if sName.endswith(".json"))
    assert len(listReports) == 1
    with open(os.path.join(sDirectory, listReports[0])) as fileIn:
        return json.load(fileIn)


def test_the_shadow_prepares_clears_and_assesses_in_a_real_container(
    dictAssessedProbe, monkeypatch,
):
    """Every sub-command ran in the shadow; the figure and the report are real.

    The image check compared the redrawn figure with a reference that
    could only exist if ``prepare`` set it aside before ``clear``
    deleted the published copy; the quantitative check passed from a
    JUnit report pytest wrote, as the image's user, into the evidence
    directory vaibify created.
    """
    with LoopbackDeposit(dictAssessedProbe["pathZenodo"]) as server:
        fnPointZenodoAt(monkeypatch, server)
        result = CliRunner().invoke(
            fnReproduceCommand,
            ["--from", dictAssessedProbe["sRepoPath"], "--rerun"])
    assert result.exit_code == 0, result.output
    assert ("Verdict: Reproduced: published comparisons passed; 1 of 2 "
            "files differ in their bytes") in result.output
    dictReport = _fdictReadTheOnlyReport()
    assert sorted(dictReport["dictPreRerunClearing"][
        "listDeletedOutputs"]) == [f"{S_STEP}/figure.png",
                                   f"{S_STEP}/values.json"]
    dictAssessment = dictReport["dictAssessmentReport"]
    dictOutputs = {dictOutput["sPath"]: dictOutput
                   for dictOutput in dictAssessment["listOutputs"]}
    dictFigure = dictOutputs[f"{S_STEP}/figure.png"]
    assert dictFigure["dictImageCheck"]["bRan"] is True
    assert dictFigure["dictImageCheck"]["iLargestLevelChange"] == 1
    assert dictFigure["sOutcome"] == "comparisons passed"
    assert dictOutputs[f"{S_STEP}/values.json"]["sOutcome"] == (
        "byte-identical")
    dictTest = [dictRecord for dictRecord in dictAssessment["listInvocations"]
                if dictRecord["sInvocationId"] == "A01.quantitative.1"][0]
    assert dictTest["iExitStatus"] == 0
    assert dictTest["sReportState"] == "read"
    assert dictTest["listTestcases"][0]["sResult"] == "passed"
    assert dictReport["sShadowTeardown"] == "destroyed"
