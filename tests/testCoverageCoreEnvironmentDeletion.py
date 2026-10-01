"""Failure paths of environmentDeletion.fdictDeleteEnvironment.

Every Docker gateway is a recorder (the daemon is the external
boundary); the deletion's own bookkeeping runs for real. The project
name and the container name are deliberately distinct so a gateway
handed the wrong one fails the assertion rather than passing by
coincidence.
"""

import pytest

from vaibify.gui import environmentDeletion


S_PROJECT_NAME = "projectAlpha"
S_CONTAINER_NAME = "containerAlphaRuntime"


@pytest.fixture
def dictDoubles(monkeypatch):
    """Substitute every Docker and registry gateway with a recorder."""
    dictCalls = {
        "listStatusAsked": [], "listPresenceAsked": [],
        "listVolumesDestroyed": [], "listImageProjects": [],
        "listImagesRemoved": [], "listUnregistered": [],
        "listResidue": [],
    }
    dictState = {
        "exceptionStatus": None, "exceptionVolume": None,
        "exceptionImages": None, "exceptionRegistryRead": None,
        "exceptionUnregister": None,
    }

    def fdictStatus(sName):
        dictCalls["listStatusAsked"].append(sName)
        if dictState["exceptionStatus"]:
            raise dictState["exceptionStatus"]
        return {"bRunning": False, "bExists": False}

    def fdictPresence(sName):
        dictCalls["listPresenceAsked"].append(sName)
        return {"bAnswered": True, "bPresent": False}

    def fnDestroyVolume(sVolumeName):
        if dictState["exceptionVolume"] and "workspace" in sVolumeName:
            raise dictState["exceptionVolume"]
        dictCalls["listVolumesDestroyed"].append(sVolumeName)

    def flistReferences(sName):
        dictCalls["listImageProjects"].append(sName)
        if dictState["exceptionImages"]:
            raise dictState["exceptionImages"]
        return [f"{sName}:latest"]

    def fnRemoveProject(sName):
        if dictState["exceptionUnregister"]:
            raise dictState["exceptionUnregister"]
        dictCalls["listUnregistered"].append(sName)

    def flistAllProjects():
        if dictState["exceptionRegistryRead"]:
            raise dictState["exceptionRegistryRead"]
        return [{"sName": S_PROJECT_NAME}, {"sName": "projectBeta"}]

    def flistRemoveResidue(sName, listRegisteredNames):
        dictCalls["listResidue"].append((sName, list(listRegisteredNames)))
        return ["residueRemoved"]

    containerManager = environmentDeletion.containerManager
    monkeypatch.setattr(containerManager, "fdictGetContainerStatus", fdictStatus)
    monkeypatch.setattr(containerManager, "fdictProbeContainerPresence", fdictPresence)
    monkeypatch.setattr(environmentDeletion, "fnStopKeepAlive", lambda sName: None)
    monkeypatch.setattr(
        environmentDeletion.volumeManager, "fbVolumeExists", lambda sName: True)
    monkeypatch.setattr(
        environmentDeletion.volumeManager, "fnDestroyVolume", fnDestroyVolume)
    monkeypatch.setattr(
        environmentDeletion.imageBuilder, "flistProjectImageReferences",
        flistReferences)
    monkeypatch.setattr(
        environmentDeletion.imageBuilder, "fbRemoveImage",
        lambda sReference: dictCalls["listImagesRemoved"].append(sReference)
        or True)
    monkeypatch.setattr(
        environmentDeletion.registryManager, "fnRemoveProject", fnRemoveProject)
    monkeypatch.setattr(
        environmentDeletion.registryManager, "flistGetAllProjects",
        flistAllProjects)
    from vaibify.config import hostResidue
    monkeypatch.setattr(
        hostResidue, "flistRemoveResidueForProject", flistRemoveResidue)
    return {"dictCalls": dictCalls, "dictState": dictState}


def fdictProject():
    """Return a registry entry whose project and container names differ."""
    return {
        "sName": S_PROJECT_NAME, "sContainerName": S_CONTAINER_NAME,
        "sMode": "container",
    }


def testCleanDeletionUsesContainerNameForContainerAndImages(dictDoubles):
    """Container and images go by container name; volumes by project name."""
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    dictCalls = dictDoubles["dictCalls"]
    assert dictCalls["listStatusAsked"] == [S_CONTAINER_NAME]
    assert dictCalls["listImageProjects"] == [S_CONTAINER_NAME]
    assert all(
        sVolume.startswith(S_PROJECT_NAME)
        for sVolume in dictCalls["listVolumesDestroyed"]
    )
    assert dictCalls["listResidue"] == [
        (S_PROJECT_NAME, [S_PROJECT_NAME, "projectBeta"]),
    ]
    assert dictReport["listResidueRemoved"] == ["residueRemoved"]
    assert dictReport["bRegistryEntryRemoved"] is True
    assert dictCalls["listUnregistered"] == [S_PROJECT_NAME]


def testStatusProbeFailureStopsBeforeAnythingIsRemoved(dictDoubles):
    """A daemon error while stopping keeps volumes, images and the entry."""
    dictDoubles["dictState"]["exceptionStatus"] = RuntimeError("daemon gone")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    dictCalls = dictDoubles["dictCalls"]
    assert dictReport["bContainerRemoved"] is False
    assert dictReport["listFailures"] == [
        "could not remove the container: daemon gone",
    ]
    assert dictCalls["listPresenceAsked"] == []
    assert dictCalls["listVolumesDestroyed"] == []
    assert dictCalls["listImageProjects"] == []
    assert dictCalls["listResidue"] == []
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictCalls["listUnregistered"] == []


def testOneVolumeFailureIsReportedAndKeepsTheRegistryEntry(dictDoubles):
    """The other volume still goes; the entry stays so a retry is possible."""
    dictDoubles["dictState"]["exceptionVolume"] = OSError("volume in use")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert len(dictReport["listVolumesRemoved"]) == 1
    assert "credentials" in dictReport["listVolumesRemoved"][0]
    assert len(dictReport["listFailures"]) == 1
    assert "could not remove the volume" in dictReport["listFailures"][0]
    assert "volume in use" in dictReport["listFailures"][0]
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictDoubles["dictCalls"]["listUnregistered"] == []


def testImageListingFailureIsReportedAndRemovesNoImage(dictDoubles):
    """An unreadable image list is a failure, not an empty repository."""
    dictDoubles["dictState"]["exceptionImages"] = RuntimeError("images failed")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert dictReport["listImagesRemoved"] == []
    assert dictReport["listFailures"] == [
        "could not list the project's images: images failed",
    ]
    assert dictReport["bRegistryEntryRemoved"] is False


def testUnreadableRegistryLeavesHostResidueAlone(dictDoubles):
    """No cross-check list means no residue sweep, and no failure either."""
    dictDoubles["dictState"]["exceptionRegistryRead"] = ValueError("corrupt")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert dictReport["listResidueRemoved"] == []
    assert dictDoubles["dictCalls"]["listResidue"] == []
    assert dictReport["listFailures"] == []
    assert dictReport["bRegistryEntryRemoved"] is True


def testAlreadyUnregisteredProjectCountsAsRemoved(dictDoubles):
    """A KeyError from the registry means the goal is already met."""
    dictDoubles["dictState"]["exceptionUnregister"] = KeyError(S_PROJECT_NAME)
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert dictReport["bRegistryEntryRemoved"] is True
    assert dictReport["listFailures"] == []


def testRegistryWriteFailureIsReportedAsNotRemoved(dictDoubles):
    """Any other registry error is surfaced and the entry is not claimed gone."""
    dictDoubles["dictState"]["exceptionUnregister"] = OSError("read-only disk")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert dictReport["bRegistryEntryRemoved"] is False
    assert dictReport["listFailures"] == [
        "could not remove the registry entry: read-only disk",
    ]
