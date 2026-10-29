"""With a Quit's gate closed, every catalogued mutation is refused.

Generated from the catalogs rather than listed by hand, so an action
added tomorrow is covered the day it is catalogued:
``LIST_AGENT_ACTIONS`` and ``LIST_RESEARCHER_ACTIONS`` together name
every state-mutating action a researcher or an agent can invoke, the
pipeline-socket (``WS``) actions included.

An HTTP action is sent to the real app that serves it (the hub or the
workflow viewer) with a real browser credential, and must be answered
503 ``shutting-down``. A socket action must be one the pipeline loop
launches through the carrier, and is driven through the real message
loop on a socket that was already open when the gate closed.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import mutationAdmission
from vaibify.gui import actionCatalog, pipelineServer, researcherActions
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testHubQuit import _GateClosingSocket, _ftRunOneFrame


_SET_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE"})


def _flistCataloguedActions():
    return list(actionCatalog.LIST_AGENT_ACTIONS) + list(
        researcherActions.LIST_RESEARCHER_ACTIONS)


def _fsFillPathTemplate(sPathTemplate):
    """Return a concrete path for a template, numbers for i-prefixed slots."""
    import re

    def fsFill(matchSlot):
        return "0" if matchSlot.group(1).startswith("i") else "gateCheck"

    return re.sub(r"\{([^}]+)\}", fsFill, sPathTemplate)


def _fdictClientsByRoute():
    """Map (method, path template) to a credentialled client of its app."""
    with patch.object(pipelineServer, "_fconnectionCreateDocker",
                      return_value=MagicMock()):
        listApps = [
            pipelineServer.fappCreateHubApplication(iExpectedPort=0),
            pipelineServer.fappCreateApplication(iExpectedPort=0),
        ]
    dictClients = {}
    for app in listApps:
        clientApp = TestClient(
            app, headers={"X-Session-Token": fsBootstrapCredential(app)})
        for route in app.routes:
            for sMethod in getattr(route, "methods", None) or ():
                dictClients.setdefault(
                    (sMethod, getattr(route, "path", "")), clientApp)
    return dictClients


def testEveryCataloguedHttpMutationIsRefusedWhileTheGateIsClosed():
    dictClients = _fdictClientsByRoute()
    mutationAdmission.fsCloseAdmissions()
    listUnserved, listAdmitted = [], []
    for dictAction in _flistCataloguedActions():
        sMethod = dictAction["sMethod"]
        if sMethod not in _SET_MUTATING_METHODS:
            continue
        tRoute = (sMethod, dictAction["sPath"])
        clientApp = dictClients.get(tRoute)
        if clientApp is None:
            listUnserved.append(tRoute)
            continue
        response = clientApp.request(
            sMethod, _fsFillPathTemplate(dictAction["sPath"]), json={})
        bRefused = response.status_code == 503 and (
            response.json().get("detail", {}).get("sRefusal")
            == "shutting-down")
        if not bRefused:
            listAdmitted.append((tRoute, response.status_code))
    assert listUnserved == [], (
        f"catalogued actions no app serves: {listUnserved}")
    assert listAdmitted == [], (
        f"mutations admitted while a Quit held the gate: {listAdmitted}")


def _flistSocketActions():
    return sorted({
        dictAction["sPath"] for dictAction in _flistCataloguedActions()
        if dictAction["sMethod"] == "WS"
    })


def testEverySocketActionIsOneTheLoopLaunchesThroughTheCarrier():
    assert set(_flistSocketActions()) <= pipelineServer._SET_RUN_DISPATCH_ACTIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("sAction", _flistSocketActions())
async def testEverySocketActionIsRefusedOnceTheGateCloses(sAction):
    socketFake = _GateClosingSocket([{"sAction": sAction}])
    listDispatched = await _ftRunOneFrame(socketFake)
    assert listDispatched == []
    assert socketFake.listSent[-1]["sType"] == "runRefused"
