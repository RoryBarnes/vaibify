"""Tests for the typed-read stat batch used by the poll loop."""

from unittest.mock import MagicMock

import docker.errors
import pytest

from vaibify.gui.fileStatusManager import (
    _LIST_CONTAINER_KEYED_CACHES,
    _fdictGetModTimes,
    _fdictStatPaths,
    fsetSweepAllContainerCaches,
)


def _fmockDockerWithMtimes(dictPathToMtime):
    """Build a connection double whose typed stat answers a dict.

    The double answers the ADAPTER, not an exec: the poll no longer
    composes a command, so a double that modelled `stat -c` output
    would be modelling a mechanism the product does not have.
    """
    mockDocker = MagicMock()
    mockDocker.fdictStatPathMtimes.side_effect = (
        lambda sContainerId, listPaths: {
            sPath: dictPathToMtime[sPath]
            for sPath in listPaths if sPath in dictPathToMtime
        }
    )
    mockDocker.fsHashContainerFileSha256.return_value = ""
    return mockDocker


def _fdictBuildMtimes(listPaths, iMtime=100):
    """Return a {path: mtime-string} map for the given paths."""
    return {sPath: str(iMtime) for sPath in listPaths}


# ---------------------------------------------------------------
# WI-1 / WI-9 #1: one round-trip per poll regardless of path count,
# and NO container write. The write is the property that matters
# most here: it was the dashboard's only mutation on a timer, and
# it is what kept this route outside the commit-guard boundary.
# ---------------------------------------------------------------


def testTheStatBatchIsOneTypedReadForAnyNumberOfPaths():
    listPaths = [f"/ws/parent/file{iIndex}.dat" for iIndex in range(600)]
    mockDocker = _fmockDockerWithMtimes(_fdictBuildMtimes(listPaths))
    dictResult = _fdictStatPaths(mockDocker, "cid", listPaths)
    assert mockDocker.fdictStatPathMtimes.call_count == 1
    assert len(dictResult) == 600


@pytest.mark.falsification
def testTheStatBatchWritesNothingIntoTheContainer():
    """The poll must reach no write primitive at all.

    Six hundred paths used to be delivered by pushing a newline list
    into /tmp and running `xargs -a` over it, because a shell argv
    will not hold them. That write ran every five seconds for as long
    as a workflow was open, and a container mutation on a timer cannot
    be carried by a commit-guard carrier without holding the mutation
    drain on that same timer -- which is what refuses a researcher's
    Run Step at random.

    Kills: restoring the pathfile push in ``_ftStatAndFingerprint``.
    """
    listPaths = [f"/ws/parent/file{iIndex}.dat" for iIndex in range(600)]
    mockDocker = _fmockDockerWithMtimes(_fdictBuildMtimes(listPaths))
    _fdictStatPaths(mockDocker, "cid", listPaths)
    mockDocker.fnWriteFileViaTar.assert_not_called()
    mockDocker.fnWriteFile.assert_not_called()
    mockDocker.ftResultExecuteCommand.assert_not_called()


# ---------------------------------------------------------------
# WI-4 / WI-9 #3: a container that vanishes mid-poll -> {}
# ---------------------------------------------------------------


def testTheStatBatchSwallowsNotFound():
    mockDocker = MagicMock()
    mockDocker.fdictStatPathMtimes.side_effect = docker.errors.NotFound(
        "container gone",
    )
    dictResult = _fdictStatPaths(mockDocker, "cid", ["/ws/a.dat"])
    assert dictResult == {}


def testTheStatBatchSwallowsApiError():
    mockDocker = MagicMock()
    mockDocker.fdictStatPathMtimes.side_effect = docker.errors.APIError(
        "409 conflict",
    )
    dictResult = _fdictStatPaths(mockDocker, "cid", ["/ws/a.dat"])
    assert dictResult == {}


@pytest.mark.falsification
def testTheStatBatchPropagatesNonSubstrateErrors():
    """A failure that is NOT the container substrate escapes the poll net.

    Pins the boundary of the vanished-mid-poll catch: only a substrate
    error (the Docker SDK's ``APIError`` family today) may degrade to
    "no answer this tick". A coding error such as a ``ValueError``
    raised by the connection must propagate -- swallowing it would
    report a healthy-looking empty poll over a real bug. The catch
    migrated from ``except (APIError, NotFound)`` to the gateway's
    ``fbErrorMeansContainerUnreachable`` predicate, and this is the
    guard that keeps the migrated shape from decaying into a blanket
    ``except Exception: pass``.

    Kills: replacing the ``fbErrorMeansContainerUnreachable`` check in
    ``_ftStatAndFingerprint`` with a blanket pass (``if False: raise``).
    """
    mockDocker = MagicMock()
    mockDocker.fdictStatPathMtimes.side_effect = ValueError(
        "a real bug, not a container failure",
    )
    with pytest.raises(ValueError):
        _fdictStatPaths(mockDocker, "cid", ["/ws/a.dat"])


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
    mockDocker = _fmockDockerWithMtimes(dictPathToMtime)
    dictFirst = _fdictGetModTimes(mockDocker, "cid", [sWorkflow])
    assert dictFirst[sWorkflow] == "500"
    # In-place edit: child mtime moves, parent mtime stays put.
    dictPathToMtime[sWorkflow] = "750"
    dictSecond = _fdictGetModTimes(mockDocker, "cid", [sWorkflow])
    assert dictSecond[sWorkflow] == "750"


def testGetModTimesIsOneRoundTripPerCall():
    """Every poll issues exactly one stat read for the polled paths.

    Guards against a future "optimization" reintroducing a
    parent-stat-then-child-stat split, which is what created the
    in-place-edit blind spot in the first place.
    """
    listPaths = [f"/ws/parent/file{iIndex}.dat" for iIndex in range(4)]
    mockDocker = _fmockDockerWithMtimes(_fdictBuildMtimes(listPaths))
    _fdictGetModTimes(mockDocker, "cid", listPaths)
    assert mockDocker.fdictStatPathMtimes.call_count == 1


def testGetModTimesEmptyPathlistDoesNoWork():
    """An empty pathlist short-circuits without touching the container."""
    mockDocker = MagicMock()
    dictResult = _fdictGetModTimes(mockDocker, "cid", [])
    assert dictResult == {}
    mockDocker.fdictStatPathMtimes.assert_not_called()
    mockDocker.ftResultExecuteCommand.assert_not_called()


# ---------------------------------------------------------------
# Lifecycle completeness: fsetSweepAllContainerCaches fans across every
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

    setEvicted = fsetSweepAllContainerCaches(dictCtx, listRunning)

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
        fsetSweepAllContainerCaches({"docker": None}, ["live-cid"])
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
        fsetSweepAllContainerCaches({"docker": None}, ["alive"])
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
    fsetSweepAllContainerCaches(dictCtx, ["a", "b"])
    mockConnection.fnEvictAbsentContainers.assert_called_once_with(
        {"a", "b"},
    )
