"""Four routes jail a caller-supplied path, and each jail has its own test.

The audit that found these found that the shared jail
(``fsValidatePathWithinRoot``) is called from about twenty sites, and that
deleting the call at a site changed nothing any existing test could see:
the figure GET and HEAD routes, the log reader, and the add-file push all
took a path from the URL or the body and handed it onward unchecked once
their own call was gone.

Each test sends a path that leaves the root and asserts the 403 the jail
raises. The other half of each pair confirms the same route still serves a
path inside the root, so a route that refuses everything cannot pass.
"""

import pytest

from tests.testCoverageRoutesAFileRoutes import (  # noqa: F401
    fixtureIsolatedRegistryAndHome,
    tclientFiles,
)
from tests.testDraftRoutes import S_CONTAINER_ID


S_OUTSIDE_PATH = "/etc/passwd"
S_INSIDE_FIGURE = "workspace/Plot/figure.png"


@pytest.mark.falsification
def testAFigureReadOutsideTheWorkspaceIsRefused(tclientFiles):
    """The figure GET jails the resolved path itself.

    Kills: figureRoutes.fresponseServeFigure: the call
    `fsValidatePathWithinRoot(sAbsPath, sProjectRoot)` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.get(
        f"/api/figure/{S_CONTAINER_ID}/{S_OUTSIDE_PATH}",
    )
    assert responseHttp.status_code == 403


@pytest.mark.falsification
def testAFigureProbeOutsideTheWorkspaceIsRefused(tclientFiles):
    """The figure HEAD probe is not an existence oracle for outside paths.

    Kills: figureRoutes.fresponseCheckFigure: the call
    `fsValidatePathWithinRoot(sAbsPath, sProjectRoot)` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.head(
        f"/api/figure/{S_CONTAINER_ID}/{S_OUTSIDE_PATH}",
    )
    assert responseHttp.status_code == 403


def testAFigureInsideTheWorkspaceIsStillServed(tclientFiles):
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles["/workspace/Plot/figure.png"] = b"png"
    responseHttp = client.get(
        f"/api/figure/{S_CONTAINER_ID}/{S_INSIDE_FIGURE}",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.content == b"png"


@pytest.mark.falsification
def testAnAddFileOutsideTheProjectRepoIsRefused(tclientFiles):
    """The add-file push refuses a path that climbs out of the repo.

    Kills: syncRoutes.fdictGithubAddFile: the call
    `fsValidatePathWithinRoot(posixpath.normpath(posixpath.join(
    sWorkdir, request.sFilePath)), fsResolveProjectRoot(...))` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/github/{S_CONTAINER_ID}/add-file",
        json={"sFilePath": "../../etc/passwd", "sCommitMessage": "message"},
    )
    assert responseHttp.status_code == 403
    assert responseHttp.json()["detail"] == "Path traversal is not permitted"


@pytest.mark.falsification
def testALogNamedForTheParentDirectoryIsRefused(tclientFiles):
    """A percent-encoded ``..`` log name cannot read the logs' parent.

    Kills: settingsRoutes.fresponseGetLogContent: the call
    `fsValidatePathWithinRoot(sLogPath, sLogsDir)` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.get(f"/api/logs/{S_CONTAINER_ID}/%2e%2e")
    assert responseHttp.status_code == 403
    assert responseHttp.json()["detail"] == "Path traversal is not permitted"
