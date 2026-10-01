"""Names the container writes cannot point a council snapshot elsewhere.

A git index key, a tracked-repo sidecar entry and a tar member name are
all written by code running inside the container, and each is later
joined onto a root and handed to the daemon. An absolute key replaces the
root outright under ``posixpath.join``; a ``..`` component climbs out of
it; a string-prefix test (``startswith("..")``) refuses the legitimate
``..data`` while proving nothing about the components. These tests drive
each boundary with names a hostile container could write.
"""

import io
import json
import tarfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from vaibify.docker import pathContainment
from vaibify.gui import (
    agentCouncilRunner, agentCouncilSnapshotScope, councilRouteGuards,
    trackedReposManager,
)

LIST_HOSTILE_NAMES = [
    "/etc/passwd", "/home/user/.config/gh/hosts.yml", "../outside",
    "a/../../outside", "a//b", "a/./b", "a/", "", ".", "..", "a\x00b",
]


def _fdictIndexEntry():
    return {"sMode": "100644", "listStages": [0], "bSkipWorktree": False,
            "sType": "file", "sIdentity": "ab" * 20, "iSizeBytes": 10}


# ---------------------------------------------------------------------
# The pure predicates
# ---------------------------------------------------------------------


@pytest.mark.parametrize("sPath", ["a", "a/b.txt", "..data", "x.y/z", "a/..b"])
def testOrdinaryRelativePathsArePlain(sPath):
    assert pathContainment.fbIsPlainRelativePath(sPath)


@pytest.mark.parametrize("sPath", LIST_HOSTILE_NAMES + [None, 7, b"a"])
def testEscapingOrMalformedPathsAreNotPlain(sPath):
    assert not pathContainment.fbIsPlainRelativePath(sPath)


def testADirectoryNameIsExactlyOneComponent():
    assert pathContainment.fbIsPlainDirectoryName("project")
    assert pathContainment.fbIsPlainDirectoryName("..data")
    assert not pathContainment.fbIsPlainDirectoryName("a/b")
    assert not pathContainment.fbIsPlainDirectoryName("/project")


@pytest.mark.parametrize("sPath,bEscapes", [
    ("a/b", False), ("./a", False), ("a/../b", False), ("..data/x", False),
    ("...", False), (".hidden", False),
    ("..", True), ("../x", True), ("a/../../x", True), ("/abs", True),
    ("a/../..", True),
])
def testNormalizedEscapeIsReadFromComponentsNotPrefixes(sPath, bEscapes):
    assert pathContainment.fbNormalizedPathEscapesTheRoot(sPath) is bEscapes


# ---------------------------------------------------------------------
# The tracked index
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAnEscapingIndexKeyRefusesTheScopeBeforeAnythingIsRead():
    """Kills: dropping the plain-path check from the index classification."""
    dictRead = {"bSuccess": True, "sReason": "", "sHeadSha": "h",
                "sPorcelainDigest": "p", "iChangedCount": 0,
                "dictEntries": {"ok/file.py": _fdictIndexEntry()}}
    for sHostile in ("/etc", "../outside", "a/./b", "a\x00b"):
        dictRead["dictEntries"][sHostile] = _fdictIndexEntry()
    connection = MagicMock()
    connection.fdictFetchTrackedIdentities.return_value = dictRead
    listRefusals = []

    def fnRefuse(sMessage):
        listRefusals.append(sMessage)
        raise RuntimeError(sMessage)

    with pytest.raises(RuntimeError):
        agentCouncilSnapshotScope.fdictObserveTrackedScope(
            connection, "cid", "/workspace/repo", lambda sPath: None,
            fnRefuse)
    assert "outside the project" in listRefusals[0]
    assert "'/etc'" in listRefusals[0]


def testOrdinaryKeysIncludingDotDotPrefixedNamesStayEligible():
    dictRead = {"dictEntries": {
        "code/step.py": _fdictIndexEntry(), "..data/x.csv": _fdictIndexEntry(),
    }}
    dictAnswer = agentCouncilSnapshotScope.fdictInterpretTrackedIndex(
        dictRead, lambda sPath: None)
    assert sorted(dictAnswer["dictEligible"]) == [
        "..data/x.csv", "code/step.py"]
    assert dictAnswer["listEscapingNames"] == []


# ---------------------------------------------------------------------
# The tracked-repos sidecar
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testASidecarEntryThatIsNotAPlainNameIsDroppedAtTheRead():
    """Kills: returning the sidecar document as the container wrote it."""
    dictSidecar = {
        "iSchemaVersion": 1,
        "listTracked": [
            {"sName": "good", "sUrl": None},
            {"sName": "/etc", "sUrl": None},
            {"sName": "../x", "sUrl": None},
            {"sName": "a/b", "sUrl": None},
            {"sName": "..data", "sUrl": None},
        ],
        "listIgnored": [{"sName": "/home/u"}, {"sName": "kept"}],
    }
    connection = MagicMock()
    connection.fbaFetchFile.return_value = json.dumps(
        dictSidecar).encode("utf-8")
    dictRead = trackedReposManager.fdictReadSidecar(connection, "cid")
    assert [d["sName"] for d in dictRead["listTracked"]] == [
        "good", "..data"]
    assert [d["sName"] for d in dictRead["listIgnored"]] == ["kept"]


# ---------------------------------------------------------------------
# The council's chosen directory
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testATrackedNameThatEscapesTheRootIsRefusedWhenJoined():
    """Kills: joining the tracked name onto the root without validation."""
    for sHostile in ("/etc", "../escape", "a/../../escape"):
        with patch.object(
            councilRouteGuards, "flistTrackedDirectoryNames",
            return_value=[sHostile],
        ):
            with pytest.raises(HTTPException) as errorChosen:
                councilRouteGuards.fsResolveDominantRepositoryPath(
                    {}, "cid", sHostile)
            assert errorChosen.value.status_code == 403
            with pytest.raises(HTTPException) as errorSingle:
                councilRouteGuards.fsResolveDominantRepositoryPath({}, "cid")
            assert errorSingle.value.status_code == 403


def testAnOrdinaryTrackedDirectoryStillResolves():
    with patch.object(
        councilRouteGuards, "flistTrackedDirectoryNames",
        return_value=["project"],
    ):
        sPath = councilRouteGuards.fsResolveDominantRepositoryPath(
            {}, "cid")
    assert sPath.endswith("/project")


# ---------------------------------------------------------------------
# The runner's member check
# ---------------------------------------------------------------------


def _finfoMember(sName, sLink=None, bHard=False):
    infoMember = tarfile.TarInfo(name=sName)
    if sLink is not None:
        infoMember.type = tarfile.LNKTYPE if bHard else tarfile.SYMTYPE
        infoMember.linkname = sLink
    return infoMember


@pytest.mark.falsification
def testARunnerMemberIsJudgedByComponentsNotByPrefix():
    """Kills: restoring the startswith("..") prefix test on member names."""
    agentCouncilRunner._fnValidateSnapshotMember(_finfoMember("..data/x"))
    agentCouncilRunner._fnValidateSnapshotMember(
        _finfoMember("a/b", "..data"))
    for sHostile in ("../x", "a/../../x", "/abs"):
        with pytest.raises(ValueError):
            agentCouncilRunner._fnValidateSnapshotMember(
                _finfoMember(sHostile))
    with pytest.raises(ValueError):
        agentCouncilRunner._fnValidateSnapshotMember(
            _finfoMember("a/b", "../../x"))
