"""The container sweep must not evict a host project's session.

The periodic sweep (``serverLifespan._fnPeriodicContainerSweepLoop``)
keeps every per-container cache in lockstep with Docker's
running-container list. A HOST project is never a Docker container, so
sweeping on that list alone evicted every host project's workflow and
path ~60 seconds after it was opened — while the dashboard kept
rendering its own copy. Cache-dependent routes then answered "no
project open", and once dispatch consulted the live cache (spec D1
fix), Run was turned away as "Not connected".

Found live twice: 2026-08-12 (the walkthrough's unexplained "defect
3" — the run lane still worked back then only because the pre-fix
socket had CAPTURED the workflow before the sweep took it) and
2026-08-14 (post-D1-fix, when the same eviction made a delayed Run
click fail outright). Root-caused with a delayed-click browser
reproduction: eviction traced to
``fileStatusManager._fsetSweepPlainDicts`` via the periodic sweep, and
the fixed sweep verified end-to-end in the same browser context (real
hub, real Chromium, 75 s wait, run executes).

The registry consulted here is a REAL registry file — the same file
``fbIsHostProject`` reads in production — not a patched predicate, so
a registry-format drift that broke host detection fails this test too.
"""

import json

import pytest

from vaibify.config import registryManager
from vaibify.gui.fileStatusManager import fsetSweepAllContainerCaches


S_HOST_PROJECT = "exampleHostProject"
S_GONE_CONTAINER = "cid-container-that-stopped"
S_RUNNING_CONTAINER = "cid-container-still-running"


@pytest.fixture()
def pathRegistryWithOneHostProject(tmp_path, monkeypatch):
    """Point the registry at a real file naming one host project."""
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({
        "listProjects": [{
            "sName": S_HOST_PROJECT,
            "sMode": "host",
            "sHostPath": str(tmp_path / "repo"),
        }],
    }))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )
    return pathRegistry


def _fdictBuildSweptContext():
    return {
        "workflows": {
            S_HOST_PROJECT: {"sWorkflowName": "host"},
            S_GONE_CONTAINER: {"sWorkflowName": "gone"},
            S_RUNNING_CONTAINER: {"sWorkflowName": "alive"},
        },
        "paths": {
            S_HOST_PROJECT: "/host/repo/.vaibify/projects/p.json",
            S_GONE_CONTAINER: "/workspace/r/.vaibify/projects/p.json",
            S_RUNNING_CONTAINER: "/workspace/s/.vaibify/projects/p.json",
        },
    }


@pytest.mark.falsification
def testTheSweepSparesAHostProjectAndStillEvictsTheGone(
    pathRegistryWithOneHostProject,
):
    """Kills: sweeping host projects on Docker's running list.

    Both halves matter. The host project must survive — its lifetime
    is its registry entry, and Docker's answer says nothing about it.
    The stopped container must still be evicted — an exemption broad
    enough to spare everything would quietly turn the sweep off, and
    hours-stale workflow snapshots for gone containers are the leak
    the sweep exists to close.
    """
    dictCtx = _fdictBuildSweptContext()

    setEvicted = fsetSweepAllContainerCaches(
        dictCtx, [S_RUNNING_CONTAINER],
    )

    assert S_HOST_PROJECT in dictCtx["workflows"], (
        "the container sweep evicted a host project's workflow; every "
        "host session dies ~60 s after it is opened"
    )
    assert S_HOST_PROJECT in dictCtx["paths"]
    assert S_GONE_CONTAINER not in dictCtx["workflows"], (
        "the stopped container survived; the exemption turned the "
        "sweep off entirely"
    )
    assert S_RUNNING_CONTAINER in dictCtx["workflows"]
    assert setEvicted == {S_GONE_CONTAINER}


def testAnUnregisteredIdGetsNoExemption(pathRegistryWithOneHostProject):
    """A container id absent from the registry is swept as before.

    ``fbIsHostProject`` reads a missing entry as container mode, so
    the exemption cannot be widened by an id that merely LOOKS like a
    name — only a registered host entry earns it.
    """
    dictCtx = {
        "workflows": {"unregistered-name": {"sWorkflowName": "x"}},
        "paths": {"unregistered-name": "/workspace/x/p.json"},
    }

    fsetSweepAllContainerCaches(dictCtx, [])

    assert dictCtx["workflows"] == {}
