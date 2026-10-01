"""Branch coverage for the image and host probes in ``environmentSnapshot``.

The ``docker`` CLI is the only external boundary here, so it is the
only thing stubbed: ``shutil.which`` answers whether docker is on PATH
and ``subprocess.run`` answers each inspect. Every test states what the
probe must RETURN for one daemon answer, and in particular that an
unreadable daemon reads as "nothing determined" (empty / None) rather
than as a mismatch or an absence -- the distinction the gates depend on.
"""

import json
import os
import subprocess

import pytest

from vaibify.reproducibility import environmentSnapshot


S_IMAGE_ID = "sha256:" + "1" * 64
S_REGISTRY_DIGEST = "registry.example/project@sha256:" + "2" * 64
S_CONTAINER_NAME = "containerAlpha"
S_CONTAINER_ID = "c0ffee00ab12"
S_RECIPE_HEX = "3" * 64


class _FakeCompletedProcess:
    """The three fields ``_fsRunCheckedCommand`` reads off a run."""

    def __init__(self, iReturnCode, sStdout, sStderr=""):
        self.returncode = iReturnCode
        self.stdout = sStdout
        self.stderr = sStderr


def fnInstallDockerDouble(monkeypatch, fnAnswer, bDockerOnPath=True):
    """Route every ``subprocess.run`` to ``fnAnswer``; record argv."""
    listCalls = []

    def fnFakeRun(saCommand, **kwargs):
        listCalls.append(list(saCommand))
        return fnAnswer(list(saCommand))

    monkeypatch.setattr(
        environmentSnapshot.shutil, "which",
        lambda sName: "/usr/bin/" + sName if bDockerOnPath else None,
    )
    monkeypatch.setattr(environmentSnapshot.subprocess, "run", fnFakeRun)
    return listCalls


def fnRaiseTimeout(listArgv):
    """Answer like a hung daemon."""
    raise subprocess.TimeoutExpired(listArgv, 30.0)


def fnWriteEnvelope(pathRepo, dictPayload):
    """Write ``.vaibify/environment.json`` under a host repo."""
    pathEnvelope = pathRepo / ".vaibify" / "environment.json"
    pathEnvelope.parent.mkdir(parents=True, exist_ok=True)
    pathEnvelope.write_text(json.dumps(dictPayload), encoding="utf-8")


# ── fsReadImageArchitecture ──────────────────────────────────────


def testReadImageArchitectureWithNoReferenceAsksNobody(monkeypatch):
    """An empty reference is undetermined and never reaches the daemon."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, "amd64"),
    )
    assert environmentSnapshot.fsReadImageArchitecture("") == ""
    assert listCalls == []


def testReadImageArchitectureReturnsTheImagesOwnPlatform(monkeypatch):
    """The image store's answer is returned stripped, for that reference."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, "arm64\n"),
    )
    assert environmentSnapshot.fsReadImageArchitecture(S_IMAGE_ID) == "arm64"
    assert listCalls == [[
        "docker", "image", "inspect", "--format", "{{.Architecture}}",
        S_IMAGE_ID,
    ]]


def testReadImageArchitectureOfAnAbsentImageIsUndetermined(monkeypatch):
    """A failing inspect is '' (UNCHECKED), never a guessed platform."""
    fnInstallDockerDouble(
        monkeypatch,
        lambda listArgv: _FakeCompletedProcess(1, "", "No such image"),
    )
    assert environmentSnapshot.fsReadImageArchitecture(S_IMAGE_ID) == ""


# ── fbImageExistsLocally ─────────────────────────────────────────


def testImageExistsLocallyWithoutReferenceIsUnknown(monkeypatch):
    """No reference means nobody could look: None, not False."""
    fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, S_IMAGE_ID),
    )
    assert environmentSnapshot.fbImageExistsLocally("") is None


def testImageExistsLocallyWithoutDockerIsUnknown(monkeypatch):
    """Docker missing from PATH is not evidence the image is gone."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, S_IMAGE_ID),
        bDockerOnPath=False,
    )
    assert environmentSnapshot.fbImageExistsLocally(S_IMAGE_ID) is None
    assert listCalls == []


def testImageExistsLocallyIsFalseOnlyWhenTheDaemonSaysSo(monkeypatch):
    """A non-zero inspect is POSITIVE absence."""
    fnInstallDockerDouble(
        monkeypatch,
        lambda listArgv: _FakeCompletedProcess(1, "", "No such image"),
    )
    assert environmentSnapshot.fbImageExistsLocally(S_IMAGE_ID) is False


def testImageExistsLocallyOnAHungDaemonIsUnknown(monkeypatch):
    """A timeout is an unanswered question, so None rather than False."""
    fnInstallDockerDouble(monkeypatch, fnRaiseTimeout)
    assert environmentSnapshot.fbImageExistsLocally(S_IMAGE_ID) is None


def testImageExistsLocallyIsTrueWhenInspectSucceeds(monkeypatch):
    """A zero inspect of that exact reference is presence."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, S_IMAGE_ID),
    )
    assert environmentSnapshot.fbImageExistsLocally(S_IMAGE_ID) is True
    assert listCalls[0][-1] == S_IMAGE_ID


# ── fdictCaptureBuiltImageIdentity ───────────────────────────────


def fnAnswerBuiltImage(sRepoDigestsOutput):
    """Return a docker double answering Id and RepoDigests inspects."""
    def fnAnswer(listArgv):
        if "{{.Id}}" in listArgv:
            return _FakeCompletedProcess(0, S_IMAGE_ID + "\n")
        return _FakeCompletedProcess(0, sRepoDigestsOutput)
    return fnAnswer


def testBuiltImageIdentityPrefersTheRegistryDigest(monkeypatch):
    """A pushed image reports its registry digest AND its raw id."""
    fnInstallDockerDouble(
        monkeypatch, fnAnswerBuiltImage("[" + S_REGISTRY_DIGEST + "]"),
    )
    dictIdentity = environmentSnapshot.fdictCaptureBuiltImageIdentity(
        "projectAlpha:latest",
    )
    assert dictIdentity == {
        "sImageDigest": S_REGISTRY_DIGEST, "sImageId": S_IMAGE_ID,
    }


def testBuiltImageIdentityFallsBackToTheContentId(monkeypatch):
    """An unpushed build has no RepoDigests, so the id is the digest."""
    fnInstallDockerDouble(monkeypatch, fnAnswerBuiltImage("[]"))
    dictIdentity = environmentSnapshot.fdictCaptureBuiltImageIdentity(
        "projectAlpha:latest",
    )
    assert dictIdentity == {"sImageDigest": S_IMAGE_ID, "sImageId": S_IMAGE_ID}


def testBuiltImageIdentityDegradesToEmptyOnAnyFailure(monkeypatch):
    """A warning feed must never abort: failures become empty strings."""
    fnInstallDockerDouble(monkeypatch, fnRaiseTimeout)
    assert environmentSnapshot.fdictCaptureBuiltImageIdentity("x:y") == {
        "sImageDigest": "", "sImageId": "",
    }


# ── fsReadImageToolchainEpoch ────────────────────────────────────


def testToolchainEpochWithNoReferenceIsEmpty(monkeypatch):
    """An empty reference is undetermined without asking docker."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, "20260101"),
    )
    assert environmentSnapshot.fsReadImageToolchainEpoch("") == ""
    assert listCalls == []


def testToolchainEpochReadsAnEightDigitLabel(monkeypatch):
    """A well-formed snapshot date is returned from the label inspect."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, "20260115\n"),
    )
    assert environmentSnapshot.fsReadImageToolchainEpoch(S_IMAGE_ID) == (
        "20260115"
    )
    assert listCalls[0][:4] == ["docker", "image", "inspect", "--format"]
    assert "Config.Labels" in listCalls[0][4]


@pytest.mark.parametrize("sLabelOutput", ["<no value>", "2026011", "2026O115"])
def testToolchainEpochRejectsAnythingButADate(monkeypatch, sLabelOutput):
    """A nil label map or a malformed value is absence, not an epoch."""
    fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, sLabelOutput),
    )
    assert environmentSnapshot.fsReadImageToolchainEpoch(S_IMAGE_ID) == ""


def testToolchainEpochOnAnUnreachableDaemonIsEmpty(monkeypatch):
    """A failed inspect reads as unknown, never as an epoch."""
    fnInstallDockerDouble(monkeypatch, fnRaiseTimeout)
    assert environmentSnapshot.fsReadImageToolchainEpoch(S_IMAGE_ID) == ""


# ── recipe and configuration labels ──────────────────────────────


def testRecipeLabelIsReadOffTheImage(monkeypatch):
    """A lowercase 64-hex label is returned from ``docker image inspect``."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, S_RECIPE_HEX),
    )
    assert environmentSnapshot.fsReadImageRecipeLabel(S_IMAGE_ID) == (
        S_RECIPE_HEX
    )
    assert listCalls[0][:3] == ["docker", "image", "inspect"]
    assert listCalls[0][-1] == S_IMAGE_ID


def testConfigurationLabelIsReadOffTheContainerNotItsImage(monkeypatch):
    """The container verb is used, so a rebuilt tag cannot answer for it."""
    listCalls = fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, S_RECIPE_HEX),
    )
    assert environmentSnapshot.fsReadContainerConfigurationLabel(
        S_CONTAINER_ID,
    ) == S_RECIPE_HEX
    assert listCalls[0][:3] == ["docker", "inspect", "--format"]
    assert listCalls[0][-1] == S_CONTAINER_ID


@pytest.mark.parametrize(
    "sLabelOutput", ["<no value>", "A" * 64, "3" * 63, "g" * 64],
)
def testHexLabelRejectsAnythingButALowercaseDigest(monkeypatch, sLabelOutput):
    """Uppercase, short, or non-hex values are an absent label."""
    fnInstallDockerDouble(
        monkeypatch, lambda listArgv: _FakeCompletedProcess(0, sLabelOutput),
    )
    assert environmentSnapshot.fsReadImageRecipeLabel(S_IMAGE_ID) == ""


def testHexLabelOnAFailingInspectIsEmpty(monkeypatch):
    """An unreachable daemon reads as 'nothing determined', never drift."""
    fnInstallDockerDouble(
        monkeypatch,
        lambda listArgv: _FakeCompletedProcess(1, "", "Cannot connect"),
    )
    assert environmentSnapshot.fsReadContainerConfigurationLabel(
        S_CONTAINER_ID,
    ) == ""


# ── host binaries and system tools ───────────────────────────────


def testBinaryCapturedSkipsMalformedEntriesAndFindsTheRealOne():
    """A non-dict entry is skipped rather than aborting the search."""
    dictEnvironment = {"dictHostBinaries": {"listBinaries": [
        "not-a-record",
        {"sBinaryPath": "/opt/toolAlpha", "sSha256": "sha256:" + "4" * 64},
    ]}}
    assert environmentSnapshot.fbBinaryCaptured(
        dictEnvironment, "/opt/toolAlpha",
    ) is True
    assert environmentSnapshot.fbBinaryCaptured(
        dictEnvironment, "/opt/toolBeta",
    ) is False


class _ContainerRootedFiles:
    """A container-rooted adapter: no local root, answers probes by argv."""

    sRootPath = "/workspace/projectAlpha"

    def __init__(self, dictAnswers):
        self.dictAnswers = dictAnswers

    def fsLocalRootOrNone(self):
        return None

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        return self.dictAnswers[saCommand[0]]


def testContainerSystemToolsReportBlankOutputAsUnknown():
    """Blank python output is None, a failing gcc is None, os-release kept."""
    filesContainer = _ContainerRootedFiles({
        "python3": (0, "  \n \n", ""),
        "gcc": (127, "", "gcc: not found"),
        "cat": (0, "ID=debian\n", ""),
    })
    dictTools = environmentSnapshot.fdictCaptureSystemTools(filesContainer)
    assert dictTools == {
        "sPython": None, "sGcc": None, "sLibc": None,
        "sOsRelease": "ID=debian\n",
    }


def testHostGccThatExitsNonZeroIsNotRecorded(monkeypatch, tmp_path):
    """A gcc on PATH that fails its version probe records None, not stderr."""
    def fnAnswer(listArgv):
        return _FakeCompletedProcess(1, "", "gcc: fatal error")
    fnInstallDockerDouble(monkeypatch, fnAnswer)
    dictTools = environmentSnapshot.fdictCaptureSystemTools(str(tmp_path))
    assert dictTools["sGcc"] is None
    assert dictTools["sPython"]


def testHostGccVersionIsItsFirstLine(monkeypatch, tmp_path):
    """The recorded gcc version is the first non-blank line of stdout."""
    fnInstallDockerDouble(
        monkeypatch,
        lambda listArgv: _FakeCompletedProcess(
            0, "\ngcc (Fixture) 12.2.0\nCopyright\n",
        ),
    )
    dictTools = environmentSnapshot.fdictCaptureSystemTools(str(tmp_path))
    assert dictTools["sGcc"] == "gcc (Fixture) 12.2.0"


# ── fbImageDigestPullable ────────────────────────────────────────


def testDigestPullableWithoutADigestIsLeftToTheSnapshotCriterion(tmp_path):
    """An envelope with no digest passes here; another criterion owns it."""
    fnWriteEnvelope(tmp_path, {"dictContainer": {"sContainerName": "x"}})
    assert environmentSnapshot.fbImageDigestPullable(str(tmp_path)) is True


def testDigestPullableRefusesABareImageId(tmp_path):
    """A local-only content id is pinned but cannot be pulled elsewhere."""
    fnWriteEnvelope(tmp_path, {"dictContainer": {"sImageDigest": S_IMAGE_ID}})
    assert environmentSnapshot.fbImageDigestPullable(str(tmp_path)) is False


def testDigestPullableAcceptsARegistryDigest(tmp_path):
    """The registry form is what a fresh host's docker pull accepts."""
    fnWriteEnvelope(
        tmp_path, {"dictContainer": {"sImageDigest": S_REGISTRY_DIGEST}},
    )
    assert environmentSnapshot.fbImageDigestPullable(str(tmp_path)) is True
    assert os.path.isfile(tmp_path / ".vaibify" / "environment.json")
