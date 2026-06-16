"""Tests for the single-exec pathfile stat + parent-mtime cache path."""

from unittest.mock import MagicMock

import docker.errors

from vaibify.gui.fileStatusManager import (
    _LIST_CONTAINER_KEYED_CACHES,
    _fdictGetModTimes,
    _fdictStatViaPathfile,
    fnInvalidateParentCacheForContainer,
    fnSweepAllContainerCaches,
    fnSweepParentMtimeCache,
)


def _fmockDockerWithStatOutput(sOutput):
    """Build a MagicMock connectionDocker that returns sOutput on exec."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, sOutput)
    return mockDocker


def _fsBuildStatOutput(listPaths, iMtime=100):
    """Build a fake 'name mtime' stat output for the given paths."""
    return "".join(f"{sPath} {iMtime}\n" for sPath in listPaths)


# ---------------------------------------------------------------
# WI-1 / WI-9 #1: single exec per poll regardless of path count
# ---------------------------------------------------------------


def testStatViaPathfileSingleExec():
    listPaths = [f"/ws/parent/file{iIndex}.dat" for iIndex in range(600)]
    mockDocker = _fmockDockerWithStatOutput(_fsBuildStatOutput(listPaths))
    dictResult = _fdictStatViaPathfile(mockDocker, "cid", listPaths)
    assert mockDocker.ftResultExecuteCommand.call_count == 1
    assert mockDocker.fnWriteFileViaTar.call_count == 1
    assert len(dictResult) == 600


# ---------------------------------------------------------------
# WI-4 / WI-9 #3: NotFound while writing pathfile -> {}
# ---------------------------------------------------------------


def testStatViaPathfileSwallowsNotFound():
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = docker.errors.NotFound(
        "container gone",
    )
    dictResult = _fdictStatViaPathfile(
        mockDocker, "cid", ["/ws/a.dat"],
    )
    assert dictResult == {}
    mockDocker.ftResultExecuteCommand.assert_not_called()


def testStatViaPathfileSwallowsApiError():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = docker.errors.APIError(
        "409 conflict",
    )
    dictResult = _fdictStatViaPathfile(
        mockDocker, "cid", ["/ws/a.dat"],
    )
    assert dictResult == {}


# ---------------------------------------------------------------
# WI-2 / WI-9 #2: cached poll reuses children of unchanged parents
# ---------------------------------------------------------------


def _fdictMakeStatResponder(dictPathToMtime):
    """Return a side_effect that mimics stat output for queried paths.

    The connectionDocker.fnWriteFileViaTar call carries the queried
    pathlist as its 3rd positional arg (baContent). We capture that to
    emit only the matching subset of dictPathToMtime in the next
    ftResultExecuteCommand.
    """
    dictState = {"listLastQueried": []}

    def fnCaptureWrite(sContainerId, sPathFile, baContent, *args, **kwargs):
        sText = baContent.decode("utf-8")
        dictState["listLastQueried"] = [
            sLine for sLine in sText.split("\n") if sLine
        ]

    def fnRespondExec(sContainerId, sCommand, *args, **kwargs):
        listLines = []
        for sPath in dictState["listLastQueried"]:
            sMtime = dictPathToMtime.get(sPath)
            if sMtime is not None:
                listLines.append(f"{sPath} {sMtime}\n")
        return (0, "".join(listLines))

    return fnCaptureWrite, fnRespondExec, dictState


def testParentMtimeCacheReusesUnchangedChildren():
    sParent = "/ws/parent"
    listChildren = [f"{sParent}/file{iIndex}.dat" for iIndex in range(5)]
    dictPathToMtime = {sParent: "1000"}
    for sChild in listChildren:
        dictPathToMtime[sChild] = "500"
    fnWrite, fnExec, dictState = _fdictMakeStatResponder(dictPathToMtime)
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = fnWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnExec
    dictCtx = {}
    dictFirst = _fdictGetModTimes(
        mockDocker, "cid", listChildren, dictCtx=dictCtx,
    )
    iWritesAfterFirst = mockDocker.fnWriteFileViaTar.call_count
    dictSecond = _fdictGetModTimes(
        mockDocker, "cid", listChildren, dictCtx=dictCtx,
    )
    assert dictFirst == dictSecond
    assert dictSecond[listChildren[0]] == "500"
    iSecondWrites = (
        mockDocker.fnWriteFileViaTar.call_count - iWritesAfterFirst
    )
    assert iSecondWrites == 1
    sSecondBatchText = (
        mockDocker.fnWriteFileViaTar.call_args_list[-1][0][2].decode("utf-8")
    )
    assert sParent in sSecondBatchText
    for sChild in listChildren:
        assert sChild not in sSecondBatchText


# ---------------------------------------------------------------
# WI-9 #9: invalidation hook clears the entry for one container
# ---------------------------------------------------------------


def testParentCacheInvalidatesOnWorkflowReload():
    sParent = "/ws/parent"
    listChildren = [f"{sParent}/a.dat", f"{sParent}/b.dat"]
    dictPathToMtime = {sParent: "1000"}
    for sChild in listChildren:
        dictPathToMtime[sChild] = "500"
    fnWrite, fnExec, dictState = _fdictMakeStatResponder(dictPathToMtime)
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = fnWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnExec
    dictCtx = {}
    _fdictGetModTimes(
        mockDocker, "cid", listChildren, dictCtx=dictCtx,
    )
    fnInvalidateParentCacheForContainer(dictCtx, "cid")
    assert "cid" not in dictCtx["dictParentMtimeCache"]
    iBefore = mockDocker.fnWriteFileViaTar.call_count
    _fdictGetModTimes(
        mockDocker, "cid", listChildren, dictCtx=dictCtx,
    )
    iAfter = mockDocker.fnWriteFileViaTar.call_count
    assert (iAfter - iBefore) == 2
    sChildrenBatchText = (
        mockDocker.fnWriteFileViaTar.call_args_list[-1][0][2].decode("utf-8")
    )
    for sChild in listChildren:
        assert sChild in sChildrenBatchText


# ---------------------------------------------------------------
# WI-9 #10: a newly seen parent dir gets stat'd next call
# ---------------------------------------------------------------


def testNewParentDirGetsStatted():
    sParentA = "/ws/a"
    sParentB = "/ws/b"
    listFirstPaths = [f"{sParentA}/x.dat"]
    listSecondPaths = [f"{sParentA}/x.dat", f"{sParentB}/y.dat"]
    dictPathToMtime = {
        sParentA: "1000", sParentB: "2000",
        f"{sParentA}/x.dat": "500", f"{sParentB}/y.dat": "600",
    }
    fnWrite, fnExec, _dictState = _fdictMakeStatResponder(dictPathToMtime)
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = fnWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnExec
    dictCtx = {}
    _fdictGetModTimes(
        mockDocker, "cid", listFirstPaths, dictCtx=dictCtx,
    )
    iBefore = mockDocker.fnWriteFileViaTar.call_count
    dictSecond = _fdictGetModTimes(
        mockDocker, "cid", listSecondPaths, dictCtx=dictCtx,
    )
    iAfter = mockDocker.fnWriteFileViaTar.call_count
    assert (iAfter - iBefore) == 2
    sParentBatchText = (
        mockDocker.fnWriteFileViaTar.call_args_list[iBefore][0][2].decode(
            "utf-8",
        )
    )
    assert sParentB in sParentBatchText
    assert dictSecond[f"{sParentB}/y.dat"] == "600"


# ---------------------------------------------------------------
# WI-9 #11: bPipelineRunning bypasses the cache entirely
# ---------------------------------------------------------------


def testCacheBypassedWhenPipelineRunning():
    sParent = "/ws/parent"
    listChildren = [f"{sParent}/file{iIndex}.dat" for iIndex in range(3)]
    dictPathToMtime = {sParent: "1000"}
    for sChild in listChildren:
        dictPathToMtime[sChild] = "500"
    fnWrite, fnExec, _dictState = _fdictMakeStatResponder(dictPathToMtime)
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = fnWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnExec
    dictCtx = {}
    _fdictGetModTimes(
        mockDocker, "cid", listChildren,
        dictCtx=dictCtx, bPipelineRunning=True,
    )
    iAfterFirst = mockDocker.fnWriteFileViaTar.call_count
    _fdictGetModTimes(
        mockDocker, "cid", listChildren,
        dictCtx=dictCtx, bPipelineRunning=True,
    )
    iAfterSecond = mockDocker.fnWriteFileViaTar.call_count
    assert iAfterFirst == 1
    assert (iAfterSecond - iAfterFirst) == 1
    for tCall in mockDocker.fnWriteFileViaTar.call_args_list:
        sBatchText = tCall[0][2].decode("utf-8")
        for sChild in listChildren:
            assert sChild in sBatchText


# ---------------------------------------------------------------
# audit HIGH #13: dictParentMtimeCache eviction sweep
# ---------------------------------------------------------------


def test_sweep_evicts_absent_containers():
    """Containers not in the running list have their cache entries dropped."""
    dictCtx = {
        "dictParentMtimeCache": {
            "alive": {"dictParentMtime": {"/a": "1"}, "dictChildMtimes": {}},
            "dead":  {"dictParentMtime": {"/b": "1"}, "dictChildMtimes": {}},
            "gone":  {"dictParentMtime": {"/c": "1"}, "dictChildMtimes": {}},
        }
    }
    listEvicted = fnSweepParentMtimeCache(dictCtx, ["alive"])
    assert set(listEvicted) == {"dead", "gone"}
    assert set(dictCtx["dictParentMtimeCache"].keys()) == {"alive"}


def test_sweep_preserves_all_when_all_running():
    dictCtx = {
        "dictParentMtimeCache": {
            "a": {"dictParentMtime": {}, "dictChildMtimes": {}},
            "b": {"dictParentMtime": {}, "dictChildMtimes": {}},
        }
    }
    listEvicted = fnSweepParentMtimeCache(dictCtx, ["a", "b"])
    assert listEvicted == []
    assert set(dictCtx["dictParentMtimeCache"].keys()) == {"a", "b"}


def test_sweep_no_cache_returns_empty_safely():
    assert fnSweepParentMtimeCache({}, ["any"]) == []
    assert fnSweepParentMtimeCache(None, ["any"]) == []


def test_sweep_handles_none_running_list():
    dictCtx = {
        "dictParentMtimeCache": {
            "a": {"dictParentMtime": {}, "dictChildMtimes": {}},
        }
    }
    listEvicted = fnSweepParentMtimeCache(dictCtx, None)
    assert listEvicted == ["a"]
    assert dictCtx["dictParentMtimeCache"] == {}


# ---------------------------------------------------------------
# Lifecycle completeness: fnSweepAllContainerCaches fans across every
# container-keyed dict and out to sibling modules (docker pool +
# host incidents).
# ---------------------------------------------------------------


def _fdictBuildStaleAndRunningCtx(listStale, listRunning):
    """Seed a fake dictCtx with stale + running entries in every cache."""
    dictCtx = {"docker": None}
    for sCacheName in _LIST_CONTAINER_KEYED_CACHES:
        dictCtx[sCacheName] = {
            sCid: {"sCacheName": sCacheName}
            for sCid in (listStale + listRunning)
        }
    dictCtx["dictParentMtimeCache"] = {
        sCid: {"dictParentMtime": {}, "dictChildMtimes": {}}
        for sCid in (listStale + listRunning)
    }
    return dictCtx


def test_sweep_evicts_stale_from_every_container_keyed_cache():
    listStale = ["dead-1", "dead-2", "dead-3"]
    listRunning = ["alive-1", "alive-2"]
    dictCtx = _fdictBuildStaleAndRunningCtx(listStale, listRunning)

    setEvicted = fnSweepAllContainerCaches(dictCtx, listRunning)

    for sCacheName in _LIST_CONTAINER_KEYED_CACHES:
        assert set(dictCtx[sCacheName].keys()) == set(listRunning), (
            f"cache {sCacheName!r} retained stale ids"
        )
    assert set(dictCtx["dictParentMtimeCache"].keys()) == set(listRunning)
    assert set(listStale).issubset(setEvicted)


def test_sweep_includes_interactive_contexts_dict():
    """Module-level interactive contexts get pruned in the same sweep."""
    from vaibify.gui import pipelineServer
    dictContexts = pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER
    dictContexts["ghost-cid"] = {"fake": True}
    dictContexts["live-cid"] = {"fake": True}
    try:
        fnSweepAllContainerCaches({"docker": None}, ["live-cid"])
        assert "ghost-cid" not in dictContexts
        assert "live-cid" in dictContexts
    finally:
        dictContexts.pop("ghost-cid", None)
        dictContexts.pop("live-cid", None)


def test_sweep_fans_out_to_host_incidents():
    from vaibify.gui import hostIncidents
    hostIncidents.fnResetHostIncidents()
    try:
        hostIncidents.fnRecordHostIncident("zombie", {"sMessage": "x"})
        hostIncidents.fnRecordHostIncident("alive", {"sMessage": "y"})
        fnSweepAllContainerCaches({"docker": None}, ["alive"])
        assert hostIncidents.flistIncidentsForContainer("zombie") == []
        assert (
            hostIncidents.flistIncidentsForContainer("alive")[0]["sMessage"]
            == "y"
        )
    finally:
        hostIncidents.fnResetHostIncidents()


def test_sweep_fans_out_to_docker_pool_eviction():
    """The docker connection.fnEvictAbsentContainers receives the running set."""
    mockConnection = MagicMock()
    dictCtx = {"docker": mockConnection}
    fnSweepAllContainerCaches(dictCtx, ["a", "b"])
    mockConnection.fnEvictAbsentContainers.assert_called_once_with(
        {"a", "b"},
    )
