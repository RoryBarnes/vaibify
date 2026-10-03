"""The Zenodo dataset-download action is withdrawn, completely.

The route advertised to in-container agents as `download-zenodo-dataset`
called a dispatcher function that never existed, so every call answered
500, and two tests that patched the missing name into being kept the
suite green. Withdrawing it means the action, the route, its request
model and every shipped mention are gone together; this file pins the
absence so none of them returns alone.
"""

import pathlib

import pytest

from vaibify.gui import actionCatalog, routeScope
from vaibify.gui.appFactory import fappCreateHubApplication

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
S_WITHDRAWN_ACTION_NAME = "download-zenodo-dataset"
S_WITHDRAWN_ROUTE_PATH = "/api/zenodo/{sContainerId}/download"
LIST_SHIPPED_ROOTS = ["vaibify", "docs"]
SET_SHIPPED_SUFFIXES = {".py", ".js", ".sh", ".md", ".json", ".html"}


@pytest.mark.falsification
def testTheWithdrawnActionIsAbsentFromTheAgentCatalog():
    """Kills: restoring the catalog entry advertised to in-container agents."""
    listNames = [dictEntry["sName"]
                 for dictEntry in actionCatalog.LIST_AGENT_ACTIONS]
    assert S_WITHDRAWN_ACTION_NAME not in listNames
    assert not any(
        dictEntry["sPath"] == S_WITHDRAWN_ROUTE_PATH
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS)


@pytest.mark.falsification
def testTheWithdrawnRouteIsNotRegisteredOnTheHubApplication():
    """Kills: restoring the route registration in the sync route module."""
    setRegisteredPaths = {
        sPath for sPath in (
            getattr(route, "path", None)
            for route in fappCreateHubApplication().routes)
        if sPath}
    assert S_WITHDRAWN_ROUTE_PATH not in setRegisteredPaths
    assert "/api/zenodo/{sContainerId}/archive" in setRegisteredPaths, (
        "the sibling Zenodo routes must still be registered; an empty "
        "route table would make the absence above meaningless")


def testNoCarrierRatchetStillRecordsTheWithdrawnRoute():
    for sMethod in ("POST", "GET"):
        assert (sMethod, S_WITHDRAWN_ROUTE_PATH) not in (
            routeScope.SET_ROUTES_AWAITING_CARRIER_MODE)


def testNoShippedFileStillMentionsTheWithdrawnAction():
    listMentions = []
    for sRoot in LIST_SHIPPED_ROOTS:
        for pathFile in (PATH_REPOSITORY / sRoot).rglob("*"):
            if (pathFile.suffix not in SET_SHIPPED_SUFFIXES
                    or "vendor" in pathFile.parts or not pathFile.is_file()):
                continue
            sText = pathFile.read_text(errors="replace")
            if (S_WITHDRAWN_ACTION_NAME in sText
                    or "ftResultDownloadDataset" in sText):
                listMentions.append(str(pathFile.relative_to(PATH_REPOSITORY)))
    assert listMentions == []
