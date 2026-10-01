"""The Overleaf sync script's git, filesystem and CLI edges.

Git is run for REAL against local repositories: a bare repository
stands in for the Overleaf remote, and a ``pre-receive`` hook on it is
how a remote refusal (including a rate limit) is produced, so the
error text the script parses is text git itself printed. Only the one
call that would reach ``git.overleaf.com`` -- ``git ls-remote`` -- is
answered by a double, and only that argv is intercepted.
"""

import argparse
import io
import os
import stat
import subprocess
import sys

import pytest

from vaibify.reproducibility import overleafSync


_REAL_RUN = subprocess.run
S_TOKEN = "overleafTokenFixture"


def fsRunGit(listArguments, sCwd):
    """Run git for real with a fixed identity; return stripped stdout."""
    processGit = _REAL_RUN(
        ["git", "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", "-c", "commit.gpgsign=false",
         *listArguments],
        cwd=sCwd, capture_output=True, text=True, check=True,
    )
    return processGit.stdout.strip()


@pytest.fixture
def tRemoteAndClone(tmp_path):
    """Return ``(sBare, sClone)``: a seeded bare remote and a clone of it."""
    sSeed = str(tmp_path / "seed")
    sBare = str(tmp_path / "remote.git")
    sClone = str(tmp_path / "clone")
    os.makedirs(sSeed)
    fsRunGit(["init", "-q"], sSeed)
    with open(os.path.join(sSeed, "main.tex"), "w") as fileTex:
        fileTex.write("\\documentclass{article}\n")
    fsRunGit(["add", "main.tex"], sSeed)
    fsRunGit(["commit", "-q", "-m", "seed"], sSeed)
    fsRunGit(["clone", "-q", "--bare", sSeed, sBare], str(tmp_path))
    fsRunGit(["clone", "-q", sBare, sClone], str(tmp_path))
    return sBare, sClone


def fnInstallRefusingHook(sBare, sMessage):
    """Make the bare remote refuse every push, printing ``sMessage``."""
    sHook = os.path.join(sBare, "hooks", "pre-receive")
    with open(sHook, "w") as fileHook:
        fileHook.write(f"#!/bin/sh\necho '{sMessage}' >&2\nexit 1\n")
    os.chmod(sHook, 0o755)


def fnAddFigure(sClone):
    """Put one new figure into the clone's working tree."""
    os.makedirs(os.path.join(sClone, "figures"), exist_ok=True)
    sFigurePath = os.path.join(sClone, "figures", "plotAlpha.pdf")
    with open(sFigurePath, "wb") as fileFigure:
        fileFigure.write(b"%PDF-1.4 fixture")


# ── commit and push ──────────────────────────────────────────────


def testAPushRecordsTheMirrorShaAndReportsTheNewHead(
    tRemoteAndClone, capsys,
):
    """The commit names the mirror snapshot; HEAD_SHA is the remote's head."""
    sBare, sClone = tRemoteAndClone
    fnAddFigure(sClone)
    overleafSync._fnCommitAndPush(sClone, sMirrorSha="0123456789abcdef")
    sOutput = capsys.readouterr().out
    sRemoteHead = fsRunGit(["rev-parse", "HEAD"], sBare)
    assert "PUSH_STATUS=pushed\n" in sOutput
    assert f"HEAD_SHA={sRemoteHead}\n" in sOutput
    assert fsRunGit(["log", "-1", "--format=%s"], sBare) == (
        "[vaibify] Update figures (from mirror 0123456)"
    )


def testARateLimitedPushRaisesTheRateLimitError(tRemoteAndClone, capsys):
    """A remote answering 'rate limit' is its own error, not a generic one."""
    sBare, sClone = tRemoteAndClone
    fnInstallRefusingHook(sBare, "rate limit exceeded, try later")
    fnAddFigure(sClone)
    with pytest.raises(overleafSync.OverleafRateLimitError):
        overleafSync._fnCommitAndPush(sClone)
    assert "PUSH_STATUS=pushed" not in capsys.readouterr().out


def testAnyOtherRefusedPushIsAGenericOverleafError(tRemoteAndClone):
    """A refusal without the rate-limit hint stays a plain OverleafError."""
    sBare, sClone = tRemoteAndClone
    fnInstallRefusingHook(sBare, "branch is protected")
    fnAddFigure(sClone)
    with pytest.raises(overleafSync.OverleafError) as infoError:
        overleafSync._fnCommitAndPush(sClone)
    assert type(infoError.value) is overleafSync.OverleafError
    assert "git push failed" in str(infoError.value)
    assert "branch is protected" in str(infoError.value)


def testNoGitOnPathEmitsNoHeadSha(monkeypatch, tmp_path, capsys):
    """A missing git binary means no HEAD line, never a fabricated one."""
    def fnRaiseNotFound(listCommand, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(overleafSync.subprocess, "run", fnRaiseNotFound)
    overleafSync._fnEmitHeadSha(str(tmp_path))
    assert capsys.readouterr().out == ""


def testADirectoryWithNoHeadEmitsNoHeadSha(tmp_path, capsys):
    """A failing rev-parse (no commit yet) prints nothing."""
    fsRunGit(["init", "-q"], str(tmp_path))
    overleafSync._fnEmitHeadSha(str(tmp_path))
    assert capsys.readouterr().out == ""


# ── copy guards ──────────────────────────────────────────────────


def testAPulledSymlinkIsRefusedEvenWhenItPointsInside(tmp_path):
    """A symlink in the Overleaf clone is refused; its target is not copied."""
    pathRepo = tmp_path / "clone"
    pathRepo.mkdir()
    (pathRepo / "main.tex").write_text("real", encoding="utf-8")
    os.symlink(str(pathRepo / "main.tex"), str(pathRepo / "alias.tex"))
    pathTarget = tmp_path / "target"
    with pytest.raises(overleafSync.OverleafError, match="symlink"):
        overleafSync._fnCopyPulledFiles(
            str(pathRepo), ["alias.tex"], str(pathTarget),
        )
    assert list(pathTarget.iterdir()) == []


def testAPulledRegularFileIsCopied(tmp_path):
    """The same pull of the real file lands it under its basename."""
    pathRepo = tmp_path / "clone"
    (pathRepo / "sections").mkdir(parents=True)
    (pathRepo / "sections" / "main.tex").write_text("real", "utf-8")
    pathTarget = tmp_path / "target"
    overleafSync._fnCopyPulledFiles(
        str(pathRepo), ["sections/main.tex"], str(pathTarget),
    )
    assert (pathTarget / "main.tex").read_text("utf-8") == "real"


def testADestinationWhoseParentEscapesTheRootIsRefused(tmp_path):
    """A parent directory that resolves outside the root is refused."""
    pathRoot = tmp_path / "root"
    pathRoot.mkdir()
    pathOutside = tmp_path / "outside"
    pathOutside.mkdir()
    os.symlink(str(pathOutside), str(pathRoot / "escape"))
    with pytest.raises(overleafSync.OverleafError, match="via symlink"):
        overleafSync._fnAssertDestinationParentSafe(
            str(pathRoot / "escape" / "figure.pdf"), str(pathRoot),
        )
    overleafSync._fnAssertDestinationParentSafe(
        str(pathRoot / "figure.pdf"), str(pathRoot),
    )
    (pathRoot / "nested").mkdir()
    overleafSync._fnAssertDestinationParentSafe(
        str(pathRoot / "nested" / "figure.pdf"), str(pathRoot),
    )


# ── the push manifest ────────────────────────────────────────────


def testRecordingWithoutACommitOrARootWritesNothing(tmp_path):
    """No commit hash, or an adapter with no root, is a no-op."""
    overleafSync.fnRecordOverleafPushManifest(str(tmp_path), "", ["a.pdf"])
    overleafSync.fnRecordOverleafPushManifest(None, "abc123", ["a.pdf"])
    assert not (tmp_path / ".vaibify").exists()
    assert overleafSync.fdictOverleafRemotePathsAt(None, "abc123") == {}


def testAnUnparseablePushManifestReadsAsNothingPushed(tmp_path):
    """A corrupt manifest is the absence of evidence, never an exception."""
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "overleafPushManifest.json").write_text(
        "{not json", encoding="utf-8",
    )
    assert overleafSync.flistOverleafPushedFiguresAt(
        str(tmp_path), "abc123",
    ) == []


def testARecordedPushIsReadBackForItsCommitOnly(tmp_path):
    """A recorded push answers for its commit and no other."""
    overleafSync.fnRecordOverleafPushManifest(
        str(tmp_path), "abc123", ["plots/plotAlpha.pdf"], "figures",
    )
    assert overleafSync.fdictOverleafRemotePathsAt(
        str(tmp_path), "abc123",
    ) == {"plots/plotAlpha.pdf": "figures/plotAlpha.pdf"}
    assert overleafSync.flistOverleafPushedFiguresAt(
        str(tmp_path), "def456",
    ) == []


# ── the token file and its directory ─────────────────────────────


def testTheEphemeralRootFallsBackWhenRunAsAStandaloneScript(
    monkeypatch, tmp_path,
):
    """Without the vaibify package, ~/.vaibify/tmp is created mode 0700."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "vaibify.config.ephemeralStore", None)
    sRoot = overleafSync._fsEphemeralRoot()
    assert sRoot == str(tmp_path / ".vaibify" / "tmp")
    assert (os.stat(sRoot).st_mode & 0o777) == 0o700


# ── ls-remote ────────────────────────────────────────────────────


def fnInstallLsRemoteDouble(monkeypatch, iExitCode, sStderr, listSeen):
    """Answer ``git ls-remote`` only; record argv and the token file."""
    def fnRun(listCommand, *args, **kwargs):
        if "ls-remote" not in listCommand:
            return _REAL_RUN(listCommand, *args, **kwargs)
        sHelper = next(
            sArgument for sArgument in listCommand
            if sArgument.startswith("credential.https://")
        )
        sTokenPath = sHelper.split("cat ", 1)[1].split(" |", 1)[0].strip("'")
        listSeen.append({
            "listCommand": list(listCommand),
            "sTokenContent": open(sTokenPath).read(),
            "iTokenMode": stat.S_IMODE(os.stat(sTokenPath).st_mode),
            "sTokenPath": sTokenPath,
        })
        return subprocess.CompletedProcess(listCommand, iExitCode, "", sStderr)

    monkeypatch.setattr(overleafSync.subprocess, "run", fnRun)
    monkeypatch.setattr(sys, "stdin", io.StringIO(S_TOKEN + "\n"))


def testLsRemotePassesTheTokenThroughAPrivateFileOnly(
    monkeypatch, tmp_path,
):
    """The token is in a 0600 file for the call, never in argv, then gone."""
    monkeypatch.setenv("HOME", str(tmp_path))
    listSeen = []
    fnInstallLsRemoteDouble(monkeypatch, 0, "", listSeen)
    with pytest.raises(SystemExit) as infoExit:
        overleafSync._fnRunLsRemote(argparse.Namespace(project="projAlpha42"))
    assert infoExit.value.code == 0
    dictSeen = listSeen[0]
    assert S_TOKEN not in " ".join(dictSeen["listCommand"])
    assert dictSeen["listCommand"][-2:] == [
        "https://git.overleaf.com/projAlpha42", "HEAD",
    ]
    assert "protocol.file.allow=never" in dictSeen["listCommand"]
    assert dictSeen["sTokenContent"] == S_TOKEN
    assert dictSeen["iTokenMode"] == 0o600
    assert not os.path.exists(dictSeen["sTokenPath"])


def testAFailedLsRemoteExitsWithGitsCodeAndARedactedReason(
    monkeypatch, tmp_path, capsys,
):
    """git's exit code is mirrored and a credential URL never echoed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    listSeen = []
    fnInstallLsRemoteDouble(
        monkeypatch, 128,
        "fatal: could not read from "
        "https://git:secretValue@git.overleaf.com/x",
        listSeen,
    )
    with pytest.raises(SystemExit) as infoExit:
        overleafSync._fnRunLsRemote(argparse.Namespace(project="projAlpha42"))
    assert infoExit.value.code == 128
    sStderr = capsys.readouterr().err
    assert "secretValue" not in sStderr
    assert "https://<redacted>@git.overleaf.com/x" in sStderr
    assert not os.path.exists(listSeen[0]["sTokenPath"])


def testAMalformedProjectIdNeverReachesGit(monkeypatch, capsys):
    """An id that could smuggle arguments exits with the usage code."""
    listSeen = []
    fnInstallLsRemoteDouble(monkeypatch, 0, "", listSeen)
    with pytest.raises(SystemExit) as infoExit:
        overleafSync._fnRunLsRemote(
            argparse.Namespace(project="proj/../--upload-pack=x"),
        )
    assert infoExit.value.code == 2
    assert listSeen == []
    assert "Invalid Overleaf project ID" in capsys.readouterr().err


# ── main's error mapping ─────────────────────────────────────────


@pytest.mark.parametrize("errorRaised, iExpectedExit", [
    (overleafSync.OverleafAuthError("bad token"), 3),
    (overleafSync.OverleafRateLimitError("slow down"), 4),
    (overleafSync.OverleafError("clone failed"), 1),
])
def testMainMapsEachErrorKindToItsExitCode(
    monkeypatch, capsys, errorRaised, iExpectedExit,
):
    """Each error class exits with its own code and prints its message."""
    def fnRaise(args):
        raise errorRaised

    monkeypatch.setattr(overleafSync, "_fnRunLsRemote", fnRaise)
    with pytest.raises(SystemExit) as infoExit:
        overleafSync.main(["ls-remote", "--project", "projAlpha42"])
    assert infoExit.value.code == iExpectedExit
    assert str(errorRaised) in capsys.readouterr().err
