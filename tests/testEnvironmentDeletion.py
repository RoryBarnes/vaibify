"""What a permanent environment deletion removes, and what it refuses to.

The mechanics, below the HTTP surface (that is
``testDeleteEnvironmentRoute``). Every Docker gateway is substituted,
so what these prove is the ORDER and the BOOKKEEPING -- which is where
the failure modes are. Three of them are asserted by trying to break
the claim rather than confirm it:

- a container the daemon will not answer for must not be read as gone,
  because the volume and image removals below it are only correct once
  the container really is;
- a partial failure must LEAVE THE REGISTRY ENTRY, because an entry
  removed over orphaned bytes is a dashboard that no longer points at
  what is still on the disk;
- the image removal must reach every tag in the project's repository,
  not just ``:latest``, because a build leaves a chain of them.

What this does NOT cover, stated so silence is not read as proof: no
real Docker daemon is touched here, so the behaviour of ``docker rmi
-f`` on a parent image with living children is asserted nowhere in
this file, and no automated lane asserts it anywhere else either.
``flistProjectImageReferences`` and ``fbRemoveImage`` are the seam
where it lives, and it was checked BY HAND once, on 2026-09-13,
against a real daemon: a three-tag chain (``:base`` -> ``:jupyter`` ->
``:latest``, the shape a build leaves) was enumerated, force-removed
in listing order, and the repository and the dangling-image list were
both empty afterwards. One manual run is evidence, not a guarantee --
it will not re-run and it will not notice a regression.
"""

import pytest

from vaibify.gui import environmentDeletion


S_NAME = "sample-environment"


@pytest.fixture
def dictDoubles(monkeypatch):
    """Substitute every gateway; record what the deletion asked of each."""
    dictCalls = {
        "listStopped": [], "listRemovedStopped": [], "listKeepAlive": [],
        "listVolumesDestroyed": [], "listImagesRemoved": [],
        "listUnregistered": [],
    }
    dictState = {
        "bRunning": True, "bExists": True,
        "bPresentAfter": False, "bAnswered": True,
        "setVolumes": {f"{S_NAME}-workspace", f"{S_NAME}-credentials"},
        "listReferences": [
            f"{S_NAME}:latest", f"{S_NAME}:base", f"{S_NAME}:jupyter",
        ],
        "setUnremovableImages": set(),
    }

    def fnStop(sContainerName):
        dictCalls["listStopped"].append(sContainerName)

    def fnRemoveStopped(sContainerName):
        dictCalls["listRemovedStopped"].append(sContainerName)

    monkeypatch.setattr(
        environmentDeletion.containerManager, "fnStopContainer", fnStop)
    monkeypatch.setattr(
        environmentDeletion.containerManager, "fnRemoveStopped",
        fnRemoveStopped)
    monkeypatch.setattr(
        environmentDeletion.containerManager, "fdictGetContainerStatus",
        lambda sName: {
            "bRunning": dictState["bRunning"],
            "bExists": dictState["bExists"], "sStatus": "running",
        })
    monkeypatch.setattr(
        environmentDeletion.containerManager, "fdictProbeContainerPresence",
        lambda sName: {
            "bAnswered": dictState["bAnswered"],
            "bPresent": dictState["bPresentAfter"],
        })
    monkeypatch.setattr(
        environmentDeletion, "fnStopKeepAlive",
        lambda sName: dictCalls["listKeepAlive"].append(sName))
    monkeypatch.setattr(
        environmentDeletion.volumeManager, "fbVolumeExists",
        lambda sVolumeName: sVolumeName in dictState["setVolumes"])
    monkeypatch.setattr(
        environmentDeletion.volumeManager, "fnDestroyVolume",
        lambda sVolumeName: dictCalls[
            "listVolumesDestroyed"].append(sVolumeName))
    monkeypatch.setattr(
        environmentDeletion.imageBuilder, "flistProjectImageReferences",
        lambda sProjectName: list(dictState["listReferences"]))

    def fbRemoveImage(sReference):
        if sReference in dictState["setUnremovableImages"]:
            return False
        dictCalls["listImagesRemoved"].append(sReference)
        return True

    monkeypatch.setattr(
        environmentDeletion.imageBuilder, "fbRemoveImage", fbRemoveImage)
    monkeypatch.setattr(
        environmentDeletion.registryManager, "fnRemoveProject",
        lambda sName: dictCalls["listUnregistered"].append(sName))
    return {"dictCalls": dictCalls, "dictState": dictState}


def _fdictProject():
    return {"sName": S_NAME, "sContainerName": S_NAME, "sMode": "container"}


def testEverythingTheProjectOwnsIsRemovedAndReported(dictDoubles):
    """The container, BOTH volumes, EVERY image tag, and the entry."""
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    dictCalls = dictDoubles["dictCalls"]
    assert dictCalls["listStopped"] == [S_NAME]
    assert dictCalls["listKeepAlive"] == [S_NAME]
    assert dictCalls["listVolumesDestroyed"] == [
        f"{S_NAME}-workspace", f"{S_NAME}-credentials",
    ]
    assert sorted(dictCalls["listImagesRemoved"]) == sorted([
        f"{S_NAME}:latest", f"{S_NAME}:base", f"{S_NAME}:jupyter",
    ])
    assert dictCalls["listUnregistered"] == [S_NAME]
    assert dictReport["listFailures"] == []
    assert dictReport["bContainerRemoved"] is True
    assert dictReport["bRegistryEntryRemoved"] is True


def testTheImageSweepIsNotJustLatest(dictDoubles):
    """A build leaves a chain of tags; deleting one of them is not deleting.

    Falsification: the report must name the base and overlay tags, not
    only ``:latest``. A deletion that removed one tag would leave most
    of the bytes on the daemon under a dashboard that no longer lists
    them.
    """
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert f"{S_NAME}:base" in dictReport["listImagesRemoved"]
    assert f"{S_NAME}:jupyter" in dictReport["listImagesRemoved"]


def testAVolumeThatDoesNotExistIsNotAFailure(dictDoubles):
    """A project that never ran has no volumes; that is nothing to report."""
    dictDoubles["dictState"]["setVolumes"] = set()
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictReport["listVolumesRemoved"] == []
    assert dictReport["listFailures"] == []
    assert dictReport["bRegistryEntryRemoved"] is True


def testAnUnansweringDaemonIsNotReadAsAMissingContainer(dictDoubles):
    """No answer means UNPROVEN, and nothing below it may proceed.

    The whole ordering rests on the container being gone: a volume
    cannot be removed while a container holds it. So a probe that did
    not answer must stop the deletion, not be rounded down to "no such
    container".
    """
    dictDoubles["dictState"]["bAnswered"] = False
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictReport["bContainerRemoved"] is False
    assert dictReport["listVolumesRemoved"] == []
    assert dictReport["listImagesRemoved"] == []
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictDoubles["dictCalls"]["listUnregistered"] == []
    assert any("did not answer" in s for s in dictReport["listFailures"])


def testASurvivingContainerStopsTheDeletion(dictDoubles):
    """The removal call returning is not proof; the probe is."""
    dictDoubles["dictState"]["bPresentAfter"] = True
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictReport["bContainerRemoved"] is False
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictDoubles["dictCalls"]["listVolumesDestroyed"] == []


def testAFailedImageRemovalKeepsTheRegistryEntry(dictDoubles):
    """Partial failure must leave the environment LISTED.

    Falsification of the tempting shortcut -- un-register anyway,
    because the researcher asked for the tile to go. That would leave
    an image on the daemon with nothing in the dashboard pointing at
    it, which is strictly worse than a tile that is still there.
    """
    dictDoubles["dictState"]["setUnremovableImages"] = {f"{S_NAME}:base"}
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictDoubles["dictCalls"]["listUnregistered"] == []
    assert any(f"{S_NAME}:base" in s for s in dictReport["listFailures"])
    # What DID go is still reported, so the dashboard can say so.
    assert dictReport["bContainerRemoved"] is True
    assert f"{S_NAME}:latest" in dictReport["listImagesRemoved"]


def testAStoppedContainerIsRemovedRatherThanStopped(dictDoubles):
    """An existing-but-stopped container takes the docker rm branch."""
    dictDoubles["dictState"]["bRunning"] = False
    environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictDoubles["dictCalls"]["listStopped"] == []
    assert dictDoubles["dictCalls"]["listRemovedStopped"] == [S_NAME]


def testAnAbsentContainerNeedsNoDockerCallAtAll(dictDoubles):
    """Deletion is idempotent: a container already gone is simply gone."""
    dictDoubles["dictState"]["bRunning"] = False
    dictDoubles["dictState"]["bExists"] = False
    dictReport = environmentDeletion.fdictDeleteEnvironment(_fdictProject())
    assert dictDoubles["dictCalls"]["listStopped"] == []
    assert dictDoubles["dictCalls"]["listRemovedStopped"] == []
    assert dictReport["bContainerRemoved"] is True
    assert dictReport["bRegistryEntryRemoved"] is True


def testTheConfirmationPhraseCarriesTheEnvironmentName():
    """The phrase is name-bearing, so a wrong tile cannot be confirmed."""
    assert environmentDeletion.fsConfirmationPhraseFor("alpha") == (
        "permanently delete alpha"
    )
    assert environmentDeletion.fsConfirmationPhraseFor("beta") != (
        environmentDeletion.fsConfirmationPhraseFor("alpha")
    )
