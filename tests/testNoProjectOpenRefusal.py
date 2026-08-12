"""What the server says when this session has no project open.

The old sentence was "Not connected to container", and it was false in
both halves at once. The caller IS connected — ``/api/connect``
answered 200 and minted the lease being presented — and a host project
has no container to be connected to, so the message sent a researcher
looking for a Docker fault they did not have.

Reached rarely from the dashboard and routinely from the in-container
agent: the no-workflow view does not offer the controls that need a
project open (measured in the browser lane, where every request that
view makes answers 200, including Stop All Running Tasks), while an
agent acting on a session with none open gets this message and nothing
else.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from testPipelineServerRoutes import (  # noqa: E402
    S_CONTAINER_ID, clientHttp,  # noqa: F401
)

from vaibify.gui.pipelineServer import (  # noqa: E402
    S_REFUSAL_NO_PROJECT_OPEN,
)


def _fdictConnectWithoutAProject(clientHttp):
    """Connect in no-workflow mode and present the lease it mints."""
    responseConnect = clientHttp.post(f"/api/connect/{S_CONTAINER_ID}")
    assert responseConnect.status_code == 200, responseConnect.text
    clientHttp.headers["X-Vaibify-Lease"] = (
        responseConnect.json()["sLeaseId"]
    )
    return responseConnect.json()


@pytest.mark.falsification
def testTheRefusalDoesNotClaimTheCallerIsDisconnected(clientHttp):
    """The session connected successfully; saying otherwise is false.

    Kills: restoring the flat "Not connected to container" sentence,
    which asserts the opposite of what just happened and names a
    container a host project does not have.
    """
    _fdictConnectWithoutAProject(clientHttp)
    response = clientHttp.get(f"/api/steps/{S_CONTAINER_ID}")
    assert response.status_code == 404
    dictDetail = response.json()["detail"]
    assert dictDetail["sRefusal"] == S_REFUSAL_NO_PROJECT_OPEN
    # The resource id is DATA and this fixture's happens to contain
    # the word; what is under test is the sentence around it.
    sWording = dictDetail["sMessage"].replace(S_CONTAINER_ID, "")
    assert "container" not in sWording.lower(), sWording
    assert "not connected" not in sWording.lower(), sWording
    assert "project" in sWording.lower(), sWording


def testTheRefusalNamesTheResourceItIsAbout(clientHttp):
    """A researcher with two projects open needs to know which one."""
    _fdictConnectWithoutAProject(clientHttp)
    response = clientHttp.get(f"/api/settings/{S_CONTAINER_ID}")
    assert response.status_code == 404
    assert S_CONTAINER_ID in response.json()["detail"]["sMessage"]


@pytest.mark.falsification
def testBothRequireHelpersRefuseIdentically():
    """One state must not have two explanations.

    The two caches are written together by the connect handler, so a
    caller that finds one empty finds the other empty.

    Driven at the helpers rather than over HTTP, and that is a finding
    rather than a convenience: no route can reach the path helper's
    refusal, because every caller checks the workflow first and is
    refused there. So the path helper is a guard against a caller that
    does not exist yet — which is exactly the kind that drifts
    unnoticed.

    Kills: giving the path helper its own wording again.
    """
    from fastapi import HTTPException

    from vaibify.gui.pipelineServer import (
        fdictRequireWorkflow, fsRequireWorkflowPath,
    )
    with pytest.raises(HTTPException) as excWorkflow:
        fdictRequireWorkflow({}, S_CONTAINER_ID)
    with pytest.raises(HTTPException) as excPath:
        fsRequireWorkflowPath({}, S_CONTAINER_ID)
    assert excPath.value.status_code == excWorkflow.value.status_code
    assert excPath.value.detail == excWorkflow.value.detail


def testAnOpenProjectIsStillServedNormally(clientHttp):
    """The other direction: the refusal must not fire on a live project.

    A guard that refuses everything reads the same as a guard that
    works, in a test that only ever drives the refusing case.
    """
    from testPipelineServerRoutes import S_WORKFLOW_PATH
    responseConnect = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseConnect.status_code == 200
    clientHttp.headers["X-Vaibify-Lease"] = (
        responseConnect.json()["sLeaseId"]
    )
    response = clientHttp.get(f"/api/steps/{S_CONTAINER_ID}")
    assert response.status_code == 200, response.text
