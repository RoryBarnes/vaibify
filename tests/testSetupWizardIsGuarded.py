"""The setup wizard carries the dashboard's request guards.

Source: ``vaibify/install/setupServer.py``, ``vaibify/cli/main.py`` and
``vaibify/cli/serverLaunch.py``.

``vaibify setup`` serves a page that writes ``vaibify.yml`` -- the base
image, repositories and packages the next build trusts. It used to carry
no session credential, no Host check and no security headers, and on
Linux it was also bound to the Docker bridge gateway, so any container on
the daemon could post a configuration to it. These tests drive the real
application with a real credential exchange, and the real launch command.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.cli import serverLaunch
from vaibify.cli.main import main
from vaibify.gui import browserSession
from vaibify.install.setupServer import fappCreateSetupWizard

I_PORT = 8051
DICT_VALID_SAVE = {"sProjectName": "guardedProject", "sPackageManager": "pip"}


def _fappWizard(tmp_path):
    return fappCreateSetupWizard(
        sOutputDirectory=str(tmp_path), iExpectedPort=I_PORT)


def _fclientOnLoopback(app, sCredential=None):
    dictHeaders = {"Host": f"127.0.0.1:{I_PORT}"}
    if sCredential is not None:
        dictHeaders["X-Session-Token"] = sCredential
    return TestClient(app, headers=dictHeaders)


@pytest.mark.falsification
def testAnUnauthenticatedSaveIsRefusedAndWritesNothing(tmp_path):
    """No credential, no configuration file.

    Kills: dropping the wizard's registration of the dashboard's request
    middleware, which restores the unauthenticated writer.
    """
    app = _fappWizard(tmp_path)
    responseHttp = _fclientOnLoopback(app).post(
        "/api/setup/save", json=DICT_VALID_SAVE)
    assert responseHttp.status_code == 401
    assert not (tmp_path / "vaibify.yml").exists()


def testEveryOtherSetupRouteRefusesAnUnauthenticatedCaller(tmp_path):
    clientHttp = _fclientOnLoopback(_fappWizard(tmp_path))
    for sMethod, sPath in (
        ("get", "/api/setup/templates"), ("get", "/api/setup/config"),
        ("get", "/api/setup/defaults"), ("post", "/api/setup/validate"),
        ("post", "/api/setup/build"),
    ):
        responseHttp = getattr(clientHttp, sMethod)(sPath)
        assert responseHttp.status_code == 401, (sMethod, sPath)


def testAForgedCredentialIsRefused(tmp_path):
    clientHttp = _fclientOnLoopback(_fappWizard(tmp_path), "not-a-credential")
    responseHttp = clientHttp.post("/api/setup/save", json=DICT_VALID_SAVE)
    assert responseHttp.status_code == 401
    assert not (tmp_path / "vaibify.yml").exists()


@pytest.mark.falsification
def testAForeignHostHeaderIsRefusedEvenWithACredential(tmp_path):
    """A DNS-rebinding page names its own host; the credential is not enough.

    Kills: handing the wizard no expected port, which disables the Host
    check.
    """
    app = _fappWizard(tmp_path)
    sCredential = fsBootstrapCredential(app)
    clientHttp = TestClient(app, headers={
        "Host": "attacker.example:8051", "X-Session-Token": sCredential,
    })
    responseHttp = clientHttp.post("/api/setup/save", json=DICT_VALID_SAVE)
    assert responseHttp.status_code == 400
    assert not (tmp_path / "vaibify.yml").exists()


def testACredentialedLoopbackSaveWritesTheConfiguration(tmp_path):
    app = _fappWizard(tmp_path)
    clientHttp = _fclientOnLoopback(app, fsBootstrapCredential(app))
    responseHttp = clientHttp.post("/api/setup/save", json=DICT_VALID_SAVE)
    assert responseHttp.status_code == 200, responseHttp.text
    with open(tmp_path / "vaibify.yml") as fileConfig:
        assert yaml.safe_load(fileConfig)["projectName"] == "guardedProject"


def testThePageCarriesTheSecurityHeaders(tmp_path):
    responseHttp = _fclientOnLoopback(_fappWizard(tmp_path)).get("/")
    assert "default-src 'self'" in responseHttp.headers[
        "Content-Security-Policy"]
    assert responseHttp.headers["X-Frame-Options"] == "DENY"


def testTheCapabilityExchangeIsTheOnlyUnauthenticatedApiRoute(tmp_path):
    app = _fappWizard(tmp_path)
    clientHttp = _fclientOnLoopback(app)
    responseBad = clientHttp.post(
        "/api/bootstrap", json={"sCapability": "forged"})
    assert responseBad.status_code == 401
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions)
    responseGood = clientHttp.post(
        "/api/bootstrap", json={"sCapability": sCapability})
    assert responseGood.status_code == 200
    sCredential = responseGood.json()["sCredential"]
    responseUse = _fclientOnLoopback(app, sCredential).get(
        "/api/setup/templates")
    assert responseUse.status_code == 200


def testAMalformedExchangeBodyIsRefusedNotCrashed(tmp_path):
    clientHttp = _fclientOnLoopback(_fappWizard(tmp_path))
    responseHttp = clientHttp.post(
        "/api/bootstrap", content=b"not json",
        headers={"Content-Type": "application/json"})
    assert responseHttp.status_code == 401


# ---------------------------------------------------------------------
# Loopback-only binding
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAServerWithNoContainerAgentsNeverBindsTheDockerBridge():
    """On Linux the bridge gateway is reachable from every container.

    Kills: ignoring ``bServeContainerAgents`` when resolving the addresses.
    """
    with patch.object(serverLaunch.platform, "system", return_value="Linux"), \
            patch.object(
                serverLaunch, "fsResolveDockerBridgeGateway",
                return_value="172.17.0.1") as mockGateway:
        listWizard = serverLaunch.flistResolveBindAddresses(
            bServeContainerAgents=False)
        mockGateway.assert_not_called()
        listHub = serverLaunch.flistResolveBindAddresses()
    assert listWizard == ["127.0.0.1"]
    assert listHub == ["127.0.0.1", "172.17.0.1"]


def testFnRunServerBindsOnlyLoopbackForTheWizard():
    listBoundAddresses = []

    def flistRecordBind(listAddresses, iPort):
        listBoundAddresses.extend(listAddresses)
        return []

    with patch.object(serverLaunch.platform, "system", return_value="Linux"), \
            patch.object(
                serverLaunch, "fsResolveDockerBridgeGateway",
                return_value="172.17.0.1"), \
            patch.object(
                serverLaunch, "flistBindServerSockets", flistRecordBind), \
            patch.object(serverLaunch, "ServerLoggingExitSignals"):
        serverLaunch.fnRunServer(
            MagicMock(), I_PORT, bServeContainerAgents=False)
    assert listBoundAddresses == ["127.0.0.1"]


# ---------------------------------------------------------------------
# The launch command
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testVaibifySetupBindsTheHostCheckAndLaunchesWithACapability():
    """The command passes its real port, a credentialled URL, loopback only.

    Kills: ``vaibify setup`` building the wizard without ``iExpectedPort``.
    """
    dictCaptured = {}
    fappReal = fappCreateSetupWizard

    def fappCapture(*tArguments, **dictKeywords):
        dictCaptured["dictKeywords"] = dictKeywords
        appWizard = fappReal(*tArguments, **dictKeywords)
        dictCaptured["app"] = appWizard
        return appWizard

    def fnCaptureUrl(sUrl):
        dictCaptured["sUrl"] = sUrl

    with patch("vaibify.install.setupServer.fappCreateSetupWizard",
               fappCapture), \
            patch("vaibify.cli.serverLaunch.fnRunServer") as mockRun, \
            patch("vaibify.cli.main._fnOpenBrowserUnlessSuppressed",
                  fnCaptureUrl), \
            patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VAIBIFY_SUPPRESS_BROWSER", None)
        resultCli = CliRunner().invoke(main, ["setup"])
    assert resultCli.exit_code == 0, resultCli.output
    assert dictCaptured["dictKeywords"] == {"iExpectedPort": I_PORT}
    assert mockRun.call_args.kwargs == {"bServeContainerAgents": False}
    sBase, sFragment = dictCaptured["sUrl"].split("#bootstrap=")
    assert sBase == f"http://127.0.0.1:{I_PORT}/"
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictCaptured["app"].state.dictBrowserSessions, sFragment)
    assert sCredential, "the launch capability must be redeemable once"
