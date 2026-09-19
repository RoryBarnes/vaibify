"""The hub can run `vaibify doctor` and hand the browser its findings.

Every failure toast on the environment hub ends in "Click to run a
diagnosis", and this route is what the click runs. It must return the
same findings the terminal prints, refuse the in-container agent lane
(doctor reads host state), and need no project.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.cli.preflightResult import PreflightResult
from vaibify.gui import pipelineServer


def _fappWithoutDocker():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda *a, **k: None,
    ):
        return pipelineServer.fappCreateApplication(sWorkspaceRoot="/workspace")


def test_the_report_carries_doctors_findings_as_json():
    app = _fappWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    listStub = [
        PreflightResult(
            sName="docker-daemon", sLevel="fail",
            sMessage="Docker daemon not reachable.",
            sRemediation="The Colima virtual machine is not running.",
            sCommand="colima start",
        ),
        PreflightResult(
            sName="installed-checkout", sLevel="info",
            sMessage="this command runs the code checked out at /x",
        ),
    ]
    with patch(
        "vaibify.cli.commandDoctor.flistRunDoctorChecks",
        return_value=listStub,
    ) as mockRun:
        response = clientHttp.get("/api/system/doctor")
    assert response.status_code == 200, response.text
    mockRun.assert_called_once_with(None, False, False)
    listFindings = response.json()["listFindings"]
    assert [d["sName"] for d in listFindings] == [
        "docker-daemon", "installed-checkout",
    ]
    assert listFindings[0]["sCommand"] == "colima start"
    assert listFindings[0]["sRemediation"].startswith("The Colima")


def test_the_real_checks_run_without_a_project():
    """No stub: doctor's environment checks answer on this machine."""
    app = _fappWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    response = clientHttp.get("/api/system/doctor")
    assert response.status_code == 200, response.text
    setNames = {d["sName"] for d in response.json()["listFindings"]}
    assert "installed-checkout" in setNames
    assert "docker-daemon" in setNames or "docker-endpoint" in setNames


def test_the_agent_lane_is_refused():
    app = _fappWithoutDocker()
    sCredential = fsBootstrapCredential(app)
    clientHttp = TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Session": "agent-lane",
    })
    response = clientHttp.get("/api/system/doctor")
    assert response.status_code in (401, 403), response.text
