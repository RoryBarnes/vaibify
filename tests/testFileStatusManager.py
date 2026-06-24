"""Tests for the single-exec pathfile stat used by the poll loop."""

from unittest.mock import MagicMock

import docker.errors

from vaibify.gui.fileStatusManager import (
    _LIST_CONTAINER_KEYED_CACHES,
    _fdictGetModTimes,
    _fdictStatViaPathfile,
    fnSweepAllContainerCaches,
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
# Regression: in-place file edits (parent dir mtime unchanged)
# must still surface as the new child mtime on the very next poll.
# This is the contract the previous parent-mtime cache violated:
# POSIX does not bump a directory's mtime when an existing child
# is rewritten in place, so the cache returned the pre-edit child
# mtime indefinitely. Symptom: container-side agent edits to
# ``workflow.json`` (or any step script) never reached the
# dashboard until something else in the same dir was added,
# deleted, or renamed.
# ---------------------------------------------------------------


def _fdictMakeStatResponder(dictPathToMtime):
    """Return side_effects that emit stat output for the queried paths.

    ``fnCaptureWrite`` reads the pathlist tar-write made by
    ``_fdictStatViaPathfile`` and stores it; ``fnRespondExec`` then
    replays the current entry for each queried path. The shared
    ``dictPathToMtime`` is mutable so a test can simulate an
    in-place edit by bumping a single key between calls.
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


def testInPlaceEditOfChildSurfacesOnNextPoll():
    """Editing a child in place must be visible on the very next poll.

    Simulates the original bug: the parent dir's mtime stays the same
    (no add/remove/rename) but a child file's mtime advances (an
    in-place rewrite by an editor or by the in-container agent's
    ``Edit`` tool). The previous parent-mtime cache trusted the
    parent and returned the cached pre-edit child mtime; the direct
    stat path must surface the new mtime immediately.
    """
    sParent = "/ws/proj/.vaibify/workflows"
    sWorkflow = f"{sParent}/example.json"
    dictPathToMtime = {sParent: "1000", sWorkflow: "500"}
    fnWrite, fnExec, _dictState = _fdictMakeStatResponder(dictPathToMtime)
    mockDocker = MagicMock()
    mockDocker.fnWriteFileViaTar.side_effect = fnWrite
    mockDocker.ftResultExecuteCommand.side_effect = fnExec
    dictFirst = _fdictGetModTimes(mockDocker, "cid", [sWorkflow])
    assert dictFirst[sWorkflow] == "500"
    # In-place edit: child mtime moves, parent mtime stays put.
    dictPathToMtime[sWorkflow] = "750"
    dictSecond = _fdictGetModTimes(mockDocker, "cid", [sWorkflow])
    assert dictSecond[sWorkflow] == "750"


def testGetModTimesIsOneExecPerCall():
    """Every poll issues exactly one stat exec for the polled paths.

    Guards against a future "optimization" reintroducing a
    parent-stat-then-child-stat split, which is what created the
    in-place-edit blind spot in the first place.
    """
    listPaths = [f"/ws/parent/file{iIndex}.dat" for iIndex in range(4)]
    mockDocker = _fmockDockerWithStatOutput(_fsBuildStatOutput(listPaths))
    _fdictGetModTimes(mockDocker, "cid", listPaths)
    assert mockDocker.ftResultExecuteCommand.call_count == 1
    assert mockDocker.fnWriteFileViaTar.call_count == 1


def testGetModTimesEmptyPathlistDoesNoWork():
    """An empty pathlist short-circuits without touching docker."""
    mockDocker = MagicMock()
    dictResult = _fdictGetModTimes(mockDocker, "cid", [])
    assert dictResult == {}
    mockDocker.fnWriteFileViaTar.assert_not_called()
    mockDocker.ftResultExecuteCommand.assert_not_called()


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
