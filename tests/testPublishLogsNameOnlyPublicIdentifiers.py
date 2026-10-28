"""Every external publish is logged, and the log names no credential.

Only GitHub pushes wrote to the hub log, so a Zenodo version, an
Overleaf push or a Repos-panel push left no trace on the host
(researcher-reported, 2026-10-27). The lines added for them name the
service, a count or a validated repository name, and on success the
public identifier; a failure's output is redacted before it is logged.
"""

import logging

import pytest

from vaibify.gui.routes import syncRoutes


_S_TOKEN = "ghp_" + "b" * 36
_S_QUERY_SECRET = "zenodoSecretValue123"


def _flistMessages(caplog):
    return [record.getMessage() for record in caplog.records]


@pytest.mark.falsification
def testAFailedPublishLogsNoCredential(caplog):
    """Kills: logging the failure's output without the shared redactor.

    The bounded excerpt alone redacts only a URL's userinfo, so an
    access token in a query string would reach the log.
    """
    dictResult = {
        "bSuccess": False, "sErrorType": "auth",
        "sMessage": (
            "upload to https://zenodo.org/api/deposit?access_token="
            + _S_QUERY_SECRET + " failed; remote https://x-access-token:"
            + _S_TOKEN + "@github.com/owner/repo.git refused"),
    }
    with caplog.at_level(logging.INFO):
        syncRoutes._fnLogPublishOutcome("Zenodo", "cid", dictResult)
    sLogged = "\n".join(_flistMessages(caplog))
    assert "Zenodo publish did not complete: container=cid" in sLogged
    assert _S_QUERY_SECRET not in sLogged
    assert _S_TOKEN not in sLogged


def testASuccessfulPublishLogsItsPublicIdentifier(caplog):
    with caplog.at_level(logging.INFO):
        syncRoutes._fnLogPublishRequested("Zenodo", "cid", "files=3")
        syncRoutes._fnLogPublishOutcome(
            "Zenodo", "cid", {"bSuccess": True},
            sIdentifier="10.5281/zenodo.1")
    assert _flistMessages(caplog) == [
        "Zenodo publish requested: container=cid files=3",
        "Zenodo publish succeeded: container=cid id=10.5281/zenodo.1",
    ]
