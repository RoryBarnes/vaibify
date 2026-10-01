"""Branch coverage for the build, start and reproduce commands' edges.

These are the paths a researcher meets when something around the
command is not as expected: a project directory without an envelope, a
git that is missing, a Colima that will not list its mounts, a detached
start given a command, a rerun whose git cannot say whose attestation
the checkout carries. Docker, Colima and git are the boundaries
replaced (git is also driven for real where a local repository makes
the answer deterministic); the files each helper reads or writes are
real files under tmp_path.
"""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.cli import commandBuild, commandReproduce, commandStart
from vaibify.config import registryManager
from vaibify.reproducibility import environmentSnapshot

S_PROJECT_NAME = "projectAlpha"
S_PINNED_DIGEST = "registry.example/projectalpha@sha256:" + "a" * 64
S_REMOTE_URL = "https://git.example.test/team/projectAlpha.git"


def fconfigForBuild(**dictOverrides):
    """Return the config attributes the build helpers read."""
    dictValues = {
        "sProjectName": S_PROJECT_NAME, "sBaseImage": "ubuntu:24.04",
        "listRepositories": [],
    }
    dictValues.update(dictOverrides)
    return SimpleNamespace(**dictValues)


def fsMakeProjectDirectory(tmp_path, dictEnvironment=None):
    """Return a project directory with vaibify.yml and an optional envelope."""
    pathProject = tmp_path / "projectAlpha"
    pathProject.mkdir()
    (pathProject / "vaibify.yml").write_text("projectName: projectAlpha\n")
    if dictEnvironment is not None:
        (pathProject / ".vaibify").mkdir()
        (pathProject / ".vaibify" / "environment.json").write_text(
            json.dumps(dictEnvironment),
        )
    return str(pathProject)


def fnPinProjectDirectory(monkeypatch, objAnswer):
    """Answer the build's project-directory lookup, or raise objAnswer."""
    def fsAnswer():
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer

    monkeypatch.setattr(commandBuild, "_fsProjectDirectory", fsAnswer)


# ---------------------------------------------------------------------
# build: retention and environment pins
# ---------------------------------------------------------------------


def testContextPruneNeverMasksTheBuildFailure(
    tmp_path, monkeypatch, capsys, caplog,
):
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": [{"sMode": "host"}]}))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    commandBuild.fnPruneOlderBuildContexts(fconfigForBuild())
    assert capsys.readouterr().out == ""
    assert "Could not prune older build contexts" in caplog.text


def testNoProjectDirectoryReadsAsNoPin(monkeypatch):
    fnPinProjectDirectory(monkeypatch, RuntimeError("no vaibify.yml found"))
    assert commandBuild._ftReadPinnedEnvironment() == ("", "")


def testProjectWithoutAnEnvelopeHasNoPin(tmp_path, monkeypatch):
    fnPinProjectDirectory(monkeypatch, fsMakeProjectDirectory(tmp_path))
    assert commandBuild._ftReadPinnedEnvironment() == ("", "")


def testEnvelopeWithoutADigestHasNoPin(tmp_path, monkeypatch):
    fnPinProjectDirectory(monkeypatch, fsMakeProjectDirectory(
        tmp_path, {"sImageDigest": ""},
    ))
    assert commandBuild._ftReadPinnedEnvironment() == ("", "")


def testPinnedEnvelopeReturnsTheDigestAndItsRecipe(tmp_path, monkeypatch):
    fnPinProjectDirectory(monkeypatch, fsMakeProjectDirectory(
        tmp_path, {"dictContainer": {"sImageDigest": S_PINNED_DIGEST}},
    ))
    listAsked = []
    monkeypatch.setattr(
        environmentSnapshot, "fsReadImageRecipeLabel",
        lambda sReference: listAsked.append(sReference) or "recipe01",
    )
    assert commandBuild._ftReadPinnedEnvironment() == (
        S_PINNED_DIGEST, "recipe01",
    )
    assert listAsked == [S_PINNED_DIGEST]


def testUnchangedRebuildPrintsNothing(monkeypatch, capsys):
    monkeypatch.setattr(
        environmentSnapshot, "fdictCaptureBuiltImageIdentity",
        lambda sReference: {"sImageDigest": S_PINNED_DIGEST},
    )
    monkeypatch.setattr(
        environmentSnapshot, "fsReadImageRecipeLabel",
        lambda sReference: "recipe01",
    )
    commandBuild.fnWarnIfRebuildChangedEnvironment(
        fconfigForBuild(), (S_PINNED_DIGEST, "recipe01"),
    )
    assert capsys.readouterr().out == ""


def testChangedRebuildNamesBothImages(monkeypatch, capsys):
    sBuiltDigest = "sha256:" + "d" * 64
    monkeypatch.setattr(
        environmentSnapshot, "fdictCaptureBuiltImageIdentity",
        lambda sReference: {"sImageDigest": "", "sImageId": sBuiltDigest},
    )
    monkeypatch.setattr(
        environmentSnapshot, "fsReadImageRecipeLabel",
        lambda sReference: "recipe02",
    )
    commandBuild.fnWarnIfRebuildChangedEnvironment(
        fconfigForBuild(), (S_PINNED_DIGEST, "recipe01"),
    )
    sOut = capsys.readouterr().out
    assert "The environment changed." in sOut
    assert S_PINNED_DIGEST in sOut and sBuiltDigest in sOut


def testUnreadableArgHashIsNotReportedAsDrift(monkeypatch):
    sHashPath = commandBuild._fsBuildArgHashPath(S_PROJECT_NAME)
    os.makedirs(sHashPath)
    assert commandBuild.fbBuildArgsChangedSinceLastBuild(
        fconfigForBuild(),
    ) is False


def fnPinDockerInspect(monkeypatch, objAnswer):
    """Answer ``docker image inspect`` with a result or raise objAnswer."""
    listArgv = []

    def processRun(listCommand, **dictKeywords):
        listArgv.append(listCommand)
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer

    monkeypatch.setattr(commandBuild.subprocess, "run", processRun)
    return listArgv


def testBaseImageDigestIsEmptyWithoutABaseImage(monkeypatch):
    listArgv = fnPinDockerInspect(monkeypatch, None)
    assert commandBuild._fsResolveBaseImageDigest(
        fconfigForBuild(sBaseImage=""),
    ) == ""
    assert listArgv == []


@pytest.mark.parametrize("objAnswer", [
    FileNotFoundError("docker"),
    subprocess.TimeoutExpired("docker", 30),
    SimpleNamespace(returncode=1, stdout=""),
])
def testBaseImageDigestIsEmptyWhenDockerCannotAnswer(monkeypatch, objAnswer):
    fnPinDockerInspect(monkeypatch, objAnswer)
    assert commandBuild._fsResolveBaseImageDigest(fconfigForBuild()) == ""


def testBaseImageDigestIsTheFirstRepoDigest(monkeypatch):
    listArgv = fnPinDockerInspect(monkeypatch, SimpleNamespace(
        returncode=0, stdout=f"[{S_PINNED_DIGEST} other@sha256:{'e' * 64}]\n",
    ))
    assert commandBuild._fsResolveBaseImageDigest(fconfigForBuild()) == (
        S_PINNED_DIGEST
    )
    assert listArgv[0][-1] == "ubuntu:24.04"


def testPersistedBaseDigestIsSkippedWithoutAProject(monkeypatch, tmp_path):
    fnPinProjectDirectory(monkeypatch, RuntimeError("no project"))
    commandBuild._fnPersistBaseImageDigest(fconfigForBuild(), S_PINNED_DIGEST)
    assert list(tmp_path.iterdir()) == []


def testPersistedBaseDigestMergesIntoTheEnvelope(monkeypatch, tmp_path):
    sProject = fsMakeProjectDirectory(tmp_path, {"sImageDigest": "keepMe"})
    fnPinProjectDirectory(monkeypatch, sProject)
    commandBuild._fnPersistBaseImageDigest(fconfigForBuild(), S_PINNED_DIGEST)
    with open(os.path.join(sProject, ".vaibify", "environment.json")) as (
        fileHandle
    ):
        dictWritten = json.load(fileHandle)
    assert dictWritten["sBaseImageDigest"] == S_PINNED_DIGEST
    assert dictWritten["sConfiguredBaseImage"] == "ubuntu:24.04"
    assert dictWritten["sImageDigest"] == "keepMe"


def testPersistedBaseDigestSwallowsAWriteFailure(monkeypatch, tmp_path):
    fnPinProjectDirectory(monkeypatch, fsMakeProjectDirectory(tmp_path))

    def fnRefuseWrite(filesRepo, dictEnvironment):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(
        environmentSnapshot, "fnWriteEnvironmentJson", fnRefuseWrite,
    )
    commandBuild._fnPersistBaseImageDigest(fconfigForBuild(), S_PINNED_DIGEST)


# ---------------------------------------------------------------------
# build: curated docs and the project repository
# ---------------------------------------------------------------------


def testMissingCuratedDocIsSkippedWithAWarningAndTheRestStaged(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(commandBuild, "T_STAGED_DOCS", (
        ("vaibify/docs/doesNotExist.md", "absent.md"),
        commandBuild.T_STAGED_DOCS[0],
    ))
    commandBuild.fnStageCuratedDocs(str(tmp_path))
    assert "staged doc missing, skipped: vaibify/docs/doesNotExist.md" in (
        capsys.readouterr().out
    )
    assert sorted(os.listdir(tmp_path / "docs-staged")) == [
        commandBuild.T_STAGED_DOCS[1][1],
    ]


def fsMakeGitRepository(tmp_path, sRemoteUrl):
    """Return a committed local repository on branch featureBranch."""
    sRepo = str(tmp_path / "repository")
    os.makedirs(sRepo)
    listIdentity = [
        "-c", "user.name=Researcher", "-c", "user.email=r@example.test",
    ]
    for listArguments in (
        ["init", "-q"],
        ["remote", "add", "origin", sRemoteUrl],
        [*listIdentity, "commit", "-q", "--allow-empty", "-m", "first"],
        ["checkout", "-q", "-b", "featureBranch"],
    ):
        subprocess.run(
            ["git", "-C", sRepo, *listArguments], check=True,
            capture_output=True,
        )
    return sRepo


def testProjectRepositoryIsAppendedAsAReferenceClone(tmp_path):
    sRepo = fsMakeGitRepository(tmp_path, S_REMOTE_URL)
    config = fconfigForBuild()
    commandBuild.fnIncludeProjectRepo(config, sRepo)
    assert config.listRepositories == [{
        "name": "projectAlpha", "url": S_REMOTE_URL,
        "branch": "featureBranch", "installMethod": "reference",
    }]


def testProjectRepositoryAlreadyListedIsNotAddedTwice(tmp_path):
    sRepo = fsMakeGitRepository(tmp_path, S_REMOTE_URL)
    config = fconfigForBuild(listRepositories=[
        {"name": "projectAlpha", "url": S_REMOTE_URL + "/"},
    ])
    commandBuild.fnIncludeProjectRepo(config, sRepo)
    assert len(config.listRepositories) == 1


def testDirectoryWithoutARemoteAddsNothing(tmp_path):
    config = fconfigForBuild()
    commandBuild.fnIncludeProjectRepo(config, str(tmp_path))
    assert config.listRepositories == []


def testMissingGitBinaryDegradesToNoRemoteAndTheDefaultBranch(monkeypatch):
    def processRaise(*args, **dictKeywords):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", processRaise)
    assert commandBuild._fsGitRemoteUrl("/nowhere") == ""
    assert commandBuild._fsGitBranch("/nowhere") == "main"


def testUnclassifiedBuildErrorExitsOneWithItsText(capsys):
    with pytest.raises(SystemExit) as excinfo:
        commandBuild._fnHandleBuildError(TypeError("unexpected None"))
    assert excinfo.value.code == 1
    assert "Error: Build failed: unexpected None" in capsys.readouterr().err


def testDockerDfBlankLinesAreSkippedNotFatal():
    sRows = (
        json.dumps({"Size": "1GB"}) + "\n\n" + json.dumps({"Size": "500MB"})
    )
    assert commandBuild._fiSumDfSizeBytes(sRows) == 1_500_000_000


# ---------------------------------------------------------------------
# start: detached launch and Colima sharing
# ---------------------------------------------------------------------


def fconfigForStart(listBindMounts=None):
    """Return the config attributes the start helpers read."""
    return SimpleNamespace(
        sProjectName=S_PROJECT_NAME, listBindMounts=listBindMounts or [],
    )


def fnPinDetachedStart(monkeypatch, objAnswer):
    """Answer the detached ``docker run`` with an id or raise objAnswer."""
    from vaibify.docker import containerManager

    def fsStart(config, sDockerDir):
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer

    monkeypatch.setattr(containerManager, "fsStartContainerDetached", fsStart)


def testDetachedStartPrintsTheShortContainerId(monkeypatch, capsys):
    fnPinDetachedStart(monkeypatch, "0123456789abcdef0123")
    commandStart._fnStartContainerDetached(fconfigForStart(), "/docker")
    assert f"Started {S_PROJECT_NAME} detached (0123456789ab)." in (
        capsys.readouterr().out
    )


def testDetachedStartFailureExitsOneOnStderr(monkeypatch, capsys):
    fnPinDetachedStart(
        monkeypatch, RuntimeError("docker run failed: no such image"),
    )
    with pytest.raises(SystemExit) as excinfo:
        commandStart._fnStartContainerDetached(fconfigForStart(), "/docker")
    assert excinfo.value.code == 1
    assert "Error: docker run failed: no such image" in (
        capsys.readouterr().err
    )


def testExternallyKilledDetachedStartIsNotAnError(monkeypatch, capsys):
    fnPinDetachedStart(monkeypatch, RuntimeError("container exit 137"))
    with pytest.raises(SystemExit) as excinfo:
        commandStart._fnStartContainerDetached(fconfigForStart(), "/docker")
    assert excinfo.value.code == 0
    assert "stopped externally" in capsys.readouterr().out


def testDetachedStartRefusesACommand(capsys):
    commandStart._fnRejectCommandWithDetach(None)
    with pytest.raises(SystemExit) as excinfo:
        commandStart._fnRejectCommandWithDetach("bash")
    assert excinfo.value.code == 2
    assert "--detach starts an idle container" in capsys.readouterr().err


def testPresentImagePassesThePreflight(monkeypatch):
    import vaibify.docker
    listAsked = []
    monkeypatch.setattr(
        vaibify.docker, "fbImageExists",
        lambda sTag: listAsked.append(sTag) or True,
    )
    preflightImage = commandStart._fpreflightImage(fconfigForStart())
    assert preflightImage.sLevel == "ok"
    assert listAsked == [f"{S_PROJECT_NAME}:latest"]


def testBindMountWithoutAHostPathIsNotFormatChecked():
    assert commandStart._fpreflightBindMountPathFormat({"host": ""}) is None


def fnPinColimaList(monkeypatch, objAnswer):
    """Answer ``colima list --json`` with a result or raise objAnswer."""
    def processRun(listCommand, **dictKeywords):
        assert listCommand == ["colima", "list", "--json"]
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer

    monkeypatch.setattr(commandStart.subprocess, "run", processRun)


@pytest.mark.parametrize("objAnswer", [
    FileNotFoundError("colima"),
    subprocess.TimeoutExpired("colima", 5),
    SimpleNamespace(returncode=1, stdout=""),
])
def testUnlistableColimaFallsBackToTheDefaultRoots(
    monkeypatch, tmp_path, objAnswer,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    fnPinColimaList(monkeypatch, objAnswer)
    assert commandStart._flistColimaSharedRoots() == [
        str(tmp_path), "/Users", "/private/tmp",
    ]


def testColimaMountsAreParsedSkippingBlankAndJunkLines(monkeypatch):
    sStdout = "\n".join([
        json.dumps({"name": "default", "mounts": [
            {"location": "/Volumes/dataDisk"}, {"location": ""},
        ]}),
        "",
        "this is not json",
        json.dumps({"name": "other", "mounts": None}),
    ])
    fnPinColimaList(monkeypatch, SimpleNamespace(returncode=0, stdout=sStdout))
    assert commandStart._flistColimaSharedRoots() == ["/Volumes/dataDisk"]


def testUnsharedBindMountFailsOnlyForThePathOutsideTheRoots(
    monkeypatch, tmp_path,
):
    from vaibify.docker import dockerContext
    pathShared = tmp_path / "shared"
    pathShared.mkdir()
    pathOutside = tmp_path / "outside"
    pathOutside.mkdir()
    monkeypatch.setattr(dockerContext, "fbColimaActive", lambda: True)
    fnPinColimaList(monkeypatch, SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"mounts": [{"location": str(pathShared)}]}),
    ))
    listResults = commandStart._flistPreflightColimaSharedRoots(
        fconfigForStart([
            {"host": "", "container": "/workspace/none"},
            {"host": str(pathShared / "dataDirectory")},
            {"host": str(pathOutside)},
        ]),
    )
    assert [r.sName for r in listResults] == [
        f"colima-share:{pathOutside}",
    ]
    assert listResults[0].sLevel == "fail"
    assert f"--mount '{pathOutside}:w'" in listResults[0].sRemediation


# ---------------------------------------------------------------------
# reproduce: what a rerun reports
# ---------------------------------------------------------------------


def testRerunWhoseGitCannotAnswerIsRefusedBeforeAnyStep(
    monkeypatch, tmp_path, capsys,
):
    from vaibify.gui import gitStatus

    def processRaise(listArguments, sDirectory):
        raise OSError("git is not installed")

    monkeypatch.setattr(gitStatus, "fsRunGit", processRaise)
    listReran = []
    monkeypatch.setattr(
        commandReproduce, "fdictRerunAndVerify",
        lambda *args: listReran.append(args),
    )
    assert commandReproduce._ftRunRerunTier(str(tmp_path), "workflow") == (
        False, False,
    )
    assert listReran == []
    assert "refused before any step ran" in capsys.readouterr().out


def testFailedPipelineNamesTheStepAndItsLastOutput(capsys):
    commandReproduce._fnReportRerunExecution({
        "listDivergedHashes": [commandReproduce.S_DIVERGENCE_PIPELINE_FAILED],
        "dictRerunFailure": {
            "sStepLabel": "A03", "sStepName": "stepGamma", "iExitCode": 2,
            "listOutputTail": ["Traceback of the step", "KeyError: 'x'"],
        },
        "listCarriedPaths": ["figureManual.pdf"],
        "sShadowTeardown": "quarantined",
        "sShadowTeardownReason": "the daemon refused removal",
    })
    sOut = capsys.readouterr().out
    assert "pipeline runner exited non-zero" in sOut
    assert "step A03 'stepGamma' stopped with error code 2" in sOut
    assert "      | KeyError: 'x'" in sOut
    assert "not re-derived" in sOut and "figureManual.pdf" in sOut
    assert "NOT proven destroyed (quarantined): the daemon refused" in sOut


def testPreflightFailureNamesCommandsMissingFromTheImage(capsys):
    commandReproduce._fnReportFailingStep({
        "sKind": "preflight",
        "listCommandsMissingFromImage": ["gfortran", "latexmk"],
        "sImageGapNote": "the pinned image predates these tools",
        "listErrors": ["stepAlpha needs gfortran"],
    })
    sOut = capsys.readouterr().out
    assert "MISSING FROM THE PINNED IMAGE: gfortran, latexmk" in sOut
    assert "the pinned image predates these tools" in sOut
    assert "      | stepAlpha needs gfortran" in sOut


def testReproductionRecordIsNamedAsSuch(capsys):
    from vaibify.reproducibility import reproductionRecord
    commandReproduce._fnReportRecordWritten(
        reproductionRecord.S_RECORD_KIND_REPRODUCTION,
        [".vaibify/reproduction.json"],
    )
    sOut = capsys.readouterr().out
    assert "recorded as a REPRODUCTION, not an attestation" in sOut
    assert "wrote .vaibify/reproduction.json" in sOut


def testRerunTierIsNotDispatchedAsAStaticTier():
    assert commandReproduce._fbDispatchTier("5", "/nowhere", set()) is True


def testAcquisitionEventsReportAttemptsAndDownloadBoundaries(capsys):
    for dictEvent in (
        {"sPhase": "attempt", "sLink": "registry", "bSucceeded": False,
         "sDetail": "manifest unknown"},
        {"sPhase": "attempt", "sLink": "deposit", "bSucceeded": True,
         "sDetail": "ignored on success"},
        {"sPhase": "downloading", "iBytes": 0, "iTotalBytes": 100},
        {"sPhase": "downloading", "iBytes": 50, "iTotalBytes": 100},
        {"sPhase": "downloading", "iBytes": 100, "iTotalBytes": 100},
        {"sPhase": "downloading", "iBytes": 10, "iTotalBytes": 0},
    ):
        commandReproduce._fnPrintAcquisitionEvent(dictEvent)
    assert capsys.readouterr().out.splitlines() == [
        "  registry: failed (manifest unknown)",
        "  deposit: served",
        "  downloading the deposit: 0/100 bytes",
        "  downloading the deposit: 100/100 bytes",
    ]


def testAggregatedWorkflowsKeepTheFirstDeterminismAndProvenance(tmp_path):
    pathProjects = tmp_path / ".vaibify" / "projects"
    pathProjects.mkdir(parents=True)
    (pathProjects / "alpha.json").write_text(json.dumps({
        "listSteps": [{"sName": "stepAlpha"}],
        "dictDeterminism": {"sPythonHashSeed": "0"},
        "dictAiProvenance": {"sAnswer": "none"},
    }))
    (pathProjects / "beta.json").write_text(json.dumps({
        "listSteps": [{"sName": "stepBeta"}],
        "dictDeterminism": {"sPythonHashSeed": "1"},
    }))
    dictMerged = commandReproduce._fdictAggregateAllWorkflows(str(tmp_path))
    assert [d["sName"] for d in dictMerged["listSteps"]] == [
        "stepAlpha", "stepBeta",
    ]
    assert dictMerged["dictDeterminism"] == {"sPythonHashSeed": "0"}
    assert dictMerged["dictAiProvenance"] == {"sAnswer": "none"}
