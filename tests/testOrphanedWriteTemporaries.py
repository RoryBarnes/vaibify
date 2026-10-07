"""``vaibify doctor`` lists the partial files a killed write left behind.

Source: the ``orphanedWriteTemporaries`` typed read in
``vaibify/docker/dockerConnection.py`` and
``doctorProjectChecks.flistCheckOrphanedWriteTemporaries``.

The confined writer stages a file as ``.vaibify-write-<hex>`` and renames
or removes it in every outcome but a kill, so one that remains is a
crashed write's partial bytes, which can be gigabytes. The walk program is
run for real over a real tree; the doctor's renderings are driven with the
answers it can get, because the dangerous one is the quiet one: a walk
that stopped early must never read as "nothing found".
"""

import json
import os
import subprocess
import sys

import pytest

from vaibify.cli import doctorProjectChecks
from vaibify.cli.doctorProjectChecks import (
    S_LEVEL_NOT_CHECKED,
    S_LEVEL_OK,
    S_LEVEL_WARN,
)
from vaibify.docker import dockerConnection


def _fdictWalk(sRoot, iMaxNamed=20, iMaxVisits=200000):
    sProgram = dockerConnection.fsRenderBatchedTypedReadProgram(
        dockerConnection.S_TYPED_READ_ORPHANED_WRITE_TEMPORARIES,
        [sRoot, str(iMaxNamed), str(iMaxVisits)])
    resultProc = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, text=True,
        timeout=60)
    assert resultProc.returncode == 0, resultProc.stderr
    return json.loads(resultProc.stdout)


def _fnWrite(sPath, baContent=b"x"):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileOut:
        fileOut.write(baContent)


@pytest.fixture()
def sWorkspace(tmp_path):
    sRoot = os.path.realpath(str(tmp_path / "workspace"))
    _fnWrite(sRoot + "/repo/.vaibify-write-aaaa1111", b"12345")
    _fnWrite(sRoot + "/repo/sub/deep/.vaibify-write-bbbb2222", b"1234567890")
    _fnWrite(sRoot + "/repo/.git/.vaibify-write-ignored", b"x")
    _fnWrite(sRoot + "/repo/data.csv", b"a,b")
    _fnWrite(sRoot + "/repo/vaibify-write-notahiddenfile", b"x")
    return sRoot


@pytest.mark.falsification
def testTheWalkFindsEveryPartialFileWithItsSizeAndAge(sWorkspace):
    """Kills: matching any name instead of the writer's private prefix."""
    dictAnswer = _fdictWalk(sWorkspace)
    dictBySuffix = {
        os.path.basename(dictFile["sPath"]): dictFile
        for dictFile in dictAnswer["listFiles"]}
    assert sorted(dictBySuffix) == [
        ".vaibify-write-aaaa1111", ".vaibify-write-bbbb2222"]
    assert dictBySuffix[".vaibify-write-aaaa1111"]["iBytes"] == 5
    assert dictBySuffix[".vaibify-write-bbbb2222"]["iBytes"] == 10
    assert all(dictFile["fAgeSeconds"] >= 0
               for dictFile in dictAnswer["listFiles"])
    assert dictAnswer["bTruncated"] is False


@pytest.mark.falsification
def testTheWalkNeverDescendsIntoGit(sWorkspace):
    """Kills: walking into ``.git``, where a repository holds its objects."""
    dictAnswer = _fdictWalk(sWorkspace)
    assert not [dictFile for dictFile in dictAnswer["listFiles"]
                if "/.git/" in dictFile["sPath"]]


@pytest.mark.falsification
def testTheWalkNamesNoMoreThanItWasAskedTo(sWorkspace):
    """Kills: removing the ceiling on how many paths are named."""
    assert len(_fdictWalk(sWorkspace, iMaxNamed=1)["listFiles"]) == 1


@pytest.mark.falsification
def testTheWalkStopsAtItsVisitCeilingAndSaysSo(sWorkspace):
    """Kills: removing the visit ceiling, so a million-file workspace is
    walked on a diagnostic's budget and the answer claims to be whole.
    """
    assert _fdictWalk(sWorkspace, iMaxVisits=1)["bTruncated"] is True


def testTheWalkNeverFollowsALinkOutOfTheWorkspace(tmp_path, sWorkspace):
    sOutside = os.path.realpath(str(tmp_path / "outside"))
    _fnWrite(sOutside + "/.vaibify-write-outside", b"x")
    os.symlink(sOutside, sWorkspace + "/repo/linkedOut")
    dictAnswer = _fdictWalk(sWorkspace)
    assert not [dictFile for dictFile in dictAnswer["listFiles"]
                if "outside" in dictFile["sPath"]]


class _Connection:
    def __init__(self, dictAnswer):
        self.dictAnswer = dictAnswer

    def fdictFindOrphanedWriteTemporaries(self, sName, sRoot):
        return self.dictAnswer


def _fresultCheck(dictAnswer):
    listResults = doctorProjectChecks.flistCheckOrphanedWriteTemporaries(
        _Connection(dictAnswer), "proj", "/workspace")
    assert len(listResults) == 1
    return listResults[0]


def testNothingFoundIsOk():
    result = _fresultCheck(
        {"bAnswered": True, "listFiles": [], "bTruncated": False})
    assert result.sLevel == S_LEVEL_OK


@pytest.mark.falsification
def testAWalkThatStoppedEarlyAndFoundNothingIsNotOk():
    """Silence is not a verdict: the part not walked is unknown.

    Kills: reporting a truncated, empty walk as ``ok``.
    """
    result = _fresultCheck(
        {"bAnswered": True, "listFiles": [], "bTruncated": True})
    assert result.sLevel == S_LEVEL_NOT_CHECKED
    assert "nothing is known about the rest" in result.sMessage


def testAProbeThatCouldNotRunIsNotCheckedNotOk():
    result = _fresultCheck({"bAnswered": False, "sError": "exec failed"})
    assert result.sLevel == S_LEVEL_NOT_CHECKED
    assert "exec failed" in result.sMessage


@pytest.mark.falsification
def testPartialFilesAreNamedWithTheirSizesAndNeverDeleted():
    """Kills: dropping the file names or the never-deleted statement."""
    result = _fresultCheck({"bAnswered": True, "bTruncated": False,
                            "listFiles": [
        {"sPath": "/workspace/r/.vaibify-write-aa", "iBytes": 2500000000,
         "fAgeSeconds": 60.0},
        {"sPath": "/workspace/r/.vaibify-write-bb", "iBytes": 12,
         "fAgeSeconds": 1.0}]})
    assert result.sLevel == S_LEVEL_WARN
    assert ".vaibify-write-aa (2.5 GB)" in result.sMessage
    assert ".vaibify-write-bb (12 B)" in result.sMessage
    assert "2 partial file(s)" in result.sMessage
    assert "never deletes" in result.sRemediation
