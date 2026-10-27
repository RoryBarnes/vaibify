"""The manifest a replay applies cannot reach outside the working tree.

A modified-state claim's manifest is model-written: paths, contents,
modes and symlink targets. Vaibify applies it to a fresh evidence
sandbox with a fixed program (``agentCouncilManifest``) before re-running
the claim's command. These tests run that exact program text on a real
temporary filesystem, with a root standing in for ``/council`` beside an
``outside`` directory it must never touch, and seed the root with the
symlinks a hostile snapshot could carry.

The lexical member check elsewhere in the council accepts ``a -> .``
together with ``b -> a/../outside``, although ``b`` resolves outside the
root on disk; that pair is the first probe here. Every probe must be
REFUSED (the program's refusal exit), and the outside directory must be
byte-for-byte and mode-for-mode unchanged. The same probes are repeated
on a container's filesystem in ``tests/testAgentCouncilRunnerLive.py``.
"""

import json
import os
import stat
import subprocess
import sys

import pytest

from vaibify.gui import agentCouncilManifest

S_VICTIM_TEXT = "the researcher's file\n"


def _fdictFullManifest(**dictKinds):
    dictManifest = {"listModifiedFiles": [], "listDeletedPaths": [],
                    "listChangedFileModes": [], "listSymlinkTargets": []}
    dictManifest.update(dictKinds)
    return dictManifest


def _ftBuildHostileTree(pathBase):
    """Return (root, outside) with the snapshot's planted symlinks."""
    pathRoot = pathBase / "council"
    pathOutside = pathBase / "outside"
    pathRoot.mkdir()
    pathOutside.mkdir()
    (pathOutside / "victim").write_text(S_VICTIM_TEXT)
    os.chmod(pathOutside / "victim", 0o644)
    os.symlink(".", pathRoot / "a")
    os.symlink("a/../outside", pathRoot / "b")
    os.symlink("../outside", pathRoot / "out")
    os.symlink("../outside/victim", pathRoot / "victimLink")
    (pathRoot / "run.sh").write_text("echo run\n")
    return pathRoot, pathOutside


def _fdictDescribeTree(pathDirectory):
    """Return every entry below a directory with its bytes and mode."""
    dictTree = {}
    for sDirectory, listNames, listFiles in os.walk(pathDirectory):
        for sName in listNames + listFiles:
            sPath = os.path.join(sDirectory, sName)
            infoStat = os.lstat(sPath)
            baContent = (open(sPath, "rb").read()
                         if stat.S_ISREG(infoStat.st_mode) else b"")
            dictTree[os.path.relpath(sPath, pathDirectory)] = (
                infoStat.st_mode, baContent)
    return dictTree


def _ftApply(pathRoot, dictManifest):
    processApply = subprocess.run(
        [sys.executable, "-c", agentCouncilManifest.S_MANIFEST_APPLY_PROGRAM,
         str(pathRoot)],
        input=agentCouncilManifest.fbaEncodeManifestForProgram(dictManifest),
        capture_output=True, timeout=60)
    return processApply.returncode, processApply.stderr.decode("utf-8")


LIST_ESCAPE_PROBES = [
    ("writeThroughTheDotDotPair", _fdictFullManifest(listModifiedFiles=[
        {"sPath": "b/planted", "sContent": "x"}])),
    ("writeThroughTheSelfLink", _fdictFullManifest(listModifiedFiles=[
        {"sPath": "a/b/planted", "sContent": "x"}])),
    ("overwriteASymlinkedFile", _fdictFullManifest(listModifiedFiles=[
        {"sPath": "victimLink", "sContent": "x"}])),
    ("deleteThroughASymlinkedDirectory", _fdictFullManifest(
        listDeletedPaths=["out/victim"])),
    ("chmodASymlinkedFile", _fdictFullManifest(listChangedFileModes=[
        {"sPath": "victimLink", "sMode": "777"}])),
    ("chmodThroughASymlinkedDirectory", _fdictFullManifest(
        listChangedFileModes=[{"sPath": "out/victim", "sMode": "777"}])),
    ("dotDotPath", _fdictFullManifest(listModifiedFiles=[
        {"sPath": "../outside/victim", "sContent": "x"}])),
    ("absolutePath", _fdictFullManifest(listModifiedFiles=[
        {"sPath": str("/etc/planted"), "sContent": "x"}])),
    ("createASymlinkThenWriteThroughIt", _fdictFullManifest(
        listSymlinkTargets=[{"sPath": "escape", "sTarget": "../outside"}],
        listModifiedFiles=[{"sPath": "escape/victim", "sContent": "x"}])),
    ("aPathListedTwice", _fdictFullManifest(listModifiedFiles=[
        {"sPath": "run.sh", "sContent": "x"},
        {"sPath": "run.sh", "sContent": "y"}])),
]


@pytest.mark.falsification
def testEveryEscapeProbeIsRefusedAndTouchesNothingOutside(tmp_path):
    """Kills: opening the walk's directories without O_NOFOLLOW."""
    listEscapes = []
    for iProbe, (sName, dictManifest) in enumerate(LIST_ESCAPE_PROBES):
        pathBase = tmp_path / f"probe{iProbe}"
        pathBase.mkdir()
        pathRoot, pathOutside = _ftBuildHostileTree(pathBase)
        dictOutsideBefore = _fdictDescribeTree(pathOutside)
        iExitCode, sError = _ftApply(pathRoot, dictManifest)
        if iExitCode != agentCouncilManifest.I_MANIFEST_REFUSED_EXIT_CODE:
            listEscapes.append(f"{sName}: exit {iExitCode} {sError}")
        if _fdictDescribeTree(pathOutside) != dictOutsideBefore:
            listEscapes.append(f"{sName}: changed the outside directory")
    assert not listEscapes, "\n".join(listEscapes)


def testAValidManifestRebuildsTheStateItDescribes(tmp_path):
    pathRoot, pathOutside = _ftBuildHostileTree(tmp_path)
    dictManifest = _fdictFullManifest(
        listModifiedFiles=[{"sPath": "src/model.py", "sContent": "f = 1\n"},
                           {"sPath": "run.sh", "sContent": "echo new\n"}],
        listDeletedPaths=["victimLink"],
        listChangedFileModes=[{"sPath": "run.sh", "sMode": "755"}],
        listSymlinkTargets=[{"sPath": "latest", "sTarget": "src/model.py"}])
    assert _ftApply(pathRoot, dictManifest) == (0, "")
    assert (pathRoot / "src" / "model.py").read_text() == "f = 1\n"
    assert (pathRoot / "run.sh").read_text() == "echo new\n"
    assert stat.S_IMODE(os.stat(pathRoot / "run.sh").st_mode) == 0o755
    assert not os.path.lexists(pathRoot / "victimLink")
    assert os.readlink(pathRoot / "latest") == "src/model.py"
    assert (pathOutside / "victim").read_text() == S_VICTIM_TEXT


def testASymlinkTargetIsStoredAsGivenNeverResolved(tmp_path):
    pathRoot, pathOutside = _ftBuildHostileTree(tmp_path)
    dictManifest = _fdictFullManifest(listSymlinkTargets=[
        {"sPath": "dangling", "sTarget": "../outside/victim"}])
    assert _ftApply(pathRoot, dictManifest)[0] == 0
    assert os.readlink(pathRoot / "dangling") == "../outside/victim"
    assert (pathOutside / "victim").read_text() == S_VICTIM_TEXT


def testAManifestThatIsNotJsonIsRefused(tmp_path):
    pathRoot, _ = _ftBuildHostileTree(tmp_path)
    processApply = subprocess.run(
        [sys.executable, "-c", agentCouncilManifest.S_MANIFEST_APPLY_PROGRAM,
         str(pathRoot)], input=b"not json", capture_output=True, timeout=60)
    assert processApply.returncode == (
        agentCouncilManifest.I_MANIFEST_REFUSED_EXIT_CODE)


def testTheLedgerFormKeysEveryChangeByPath():
    dictManifest = _fdictFullManifest(
        listModifiedFiles=[{"sPath": "a.py", "sContent": "x"}],
        listDeletedPaths=["b.py"],
        listChangedFileModes=[{"sPath": "run.sh", "sMode": "755"}],
        listSymlinkTargets=[{"sPath": "latest", "sTarget": "a.py"}])
    assert agentCouncilManifest.fdictTranslateManifestForLedger(
        dictManifest) == {
            "dictModifiedFileContents": {"a.py": "x"},
            "listDeletedPaths": ["b.py"],
            "dictChangedFileModes": {"run.sh": "755"},
            "dictSymlinkTargets": {"latest": "a.py"},
        }


def testTheManifestNeverReachesTheCommandLine():
    """Model text rides stdin; the argv is fixed but for the root."""
    listCommand = agentCouncilManifest.flistComposeManifestApplyCommand(
        "/council")
    assert listCommand == [
        "python3", "-c", agentCouncilManifest.S_MANIFEST_APPLY_PROGRAM,
        "/council"]
    assert json.loads(agentCouncilManifest.fbaEncodeManifestForProgram(
        _fdictFullManifest()).decode("utf-8")) == _fdictFullManifest()
