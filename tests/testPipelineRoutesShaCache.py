"""Persistence contract for the per-container output-sha cache.

The cache used to live only in ``dictCtx``. Restarting the GUI process
emptied it and forced every poll to rehash every declared output —
many GB of work for a real sweep. The cache now persists to the
container at ``<sProjectRepoPath>/.vaibify/container_mtime_cache.json``
and is reloaded on first access for a new dictCtx.
"""

from vaibify.gui.routes import pipelineRoutes


_S_CONTAINER_ID = "ctr-sha-cache"
_S_REPO = "/workspace/myrepo"


class _RecordingFakeDocker:
    """Capture file writes and seed reads for round-trip checks."""

    def __init__(self):
        self.dictFiles = {}
        self.listWrites = []

    def fbaFetchFile(self, sContainerId, sPath):
        sKey = (sContainerId, sPath)
        if sKey not in self.dictFiles:
            raise FileNotFoundError(sPath)
        return self.dictFiles[sKey]

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[(sContainerId, sPath)] = baContent
        self.listWrites.append(sPath)


def _fdictCtxWithWorkflow(connectionDocker):
    """Return a fresh dictCtx wired for a workflow with a project repo."""
    return {
        "workflows": {
            _S_CONTAINER_ID: {"sProjectRepoPath": _S_REPO},
        },
        "docker": connectionDocker,
    }


def test_first_access_hydrates_from_container():
    """A brand-new dictCtx loads the cache from the container store."""
    connectionFake = _RecordingFakeDocker()
    sCachePath = _S_REPO + "/.vaibify/container_mtime_cache.json"
    connectionFake.dictFiles[(_S_CONTAINER_ID, sCachePath)] = (
        b'{"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}'
    )
    dictCtx = _fdictCtxWithWorkflow(connectionFake)
    dictCache = pipelineRoutes._fdictManifestShaCache(
        dictCtx, _S_CONTAINER_ID,
    )
    assert dictCache.get("out/a.dat", {}).get("sSha256") == "aa"


def test_subsequent_access_reuses_in_memory_layer():
    """Once hydrated the cache lives in dictCtx for the rest of the process."""
    connectionFake = _RecordingFakeDocker()
    dictCtx = _fdictCtxWithWorkflow(connectionFake)
    dictCacheOne = pipelineRoutes._fdictManifestShaCache(
        dictCtx, _S_CONTAINER_ID,
    )
    dictCacheOne["out/b.dat"] = {"iMtime": 1800, "sSha256": "bb"}
    dictCacheTwo = pipelineRoutes._fdictManifestShaCache(
        dictCtx, _S_CONTAINER_ID,
    )
    assert dictCacheTwo is dictCacheOne


def test_cache_survives_dict_ctx_recreation():
    """Tearing down dictCtx and rebuilding must not lose cached SHAs.

    Models a server restart: the new dictCtx is empty, the first sha
    cache request reloads the persisted state from the container.
    """
    connectionFake = _RecordingFakeDocker()
    dictCtxOriginal = _fdictCtxWithWorkflow(connectionFake)
    dictCacheOriginal = pipelineRoutes._fdictManifestShaCache(
        dictCtxOriginal, _S_CONTAINER_ID,
    )
    dictCacheOriginal["out/c.dat"] = {"iMtime": 1900, "sSha256": "cc"}
    # Persist the way _fnPersistShaCacheToContainer would after an update.
    pipelineRoutes._fnPersistShaCacheToContainer(
        dictCtxOriginal, _S_CONTAINER_ID, _S_REPO, dictCacheOriginal,
    )
    # Restart: a brand new dictCtx replaces the old one.
    dictCtxRebuilt = _fdictCtxWithWorkflow(connectionFake)
    dictCacheRebuilt = pipelineRoutes._fdictManifestShaCache(
        dictCtxRebuilt, _S_CONTAINER_ID,
    )
    assert dictCacheRebuilt.get("out/c.dat", {}).get("sSha256") == "cc"


def test_no_project_repo_skips_persistence_silently():
    """Workflows without a project repo path do not attempt to persist."""
    connectionFake = _RecordingFakeDocker()
    dictCtx = {
        "workflows": {_S_CONTAINER_ID: {"sProjectRepoPath": ""}},
        "docker": connectionFake,
    }
    pipelineRoutes._fnPersistShaCacheToContainer(
        dictCtx, _S_CONTAINER_ID, "", {"x": {"iMtime": 1, "sSha256": "y"}},
    )
    assert connectionFake.listWrites == []


def test_persist_only_runs_on_update():
    """An identical sha+mtime should not trigger a write."""
    connectionFake = _RecordingFakeDocker()
    dictCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}

    class _FakeFilesNoChange:
        def fdictHashFiles(self, listPaths):
            return {
                sPath: {
                    "sSha256": "aa", "sSymlinkSegment": None,
                    "bEscapesRoot": False,
                }
                for sPath in listPaths
            }

    dictMtimesRel = {"out/a.dat": "1700"}
    bChanged = pipelineRoutes._fnUpdateShaCache(
        dictCache, _FakeFilesNoChange(),
        ["out/a.dat"], dictMtimesRel,
    )
    assert bChanged is False


def test_persist_runs_when_sha_changes():
    """A fresh sha or mtime advances the cache and signals persistence."""
    connectionFake = _RecordingFakeDocker()
    dictCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}

    class _FakeFilesChanged:
        def fdictHashFiles(self, listPaths):
            return {
                sPath: {
                    "sSha256": "bb", "sSymlinkSegment": None,
                    "bEscapesRoot": False,
                }
                for sPath in listPaths
            }

    dictMtimesRel = {"out/a.dat": "1800"}
    bChanged = pipelineRoutes._fnUpdateShaCache(
        dictCache, _FakeFilesChanged(),
        ["out/a.dat"], dictMtimesRel,
    )
    assert bChanged is True
    assert dictCache["out/a.dat"] == {"iMtime": 1800, "sSha256": "bb"}
