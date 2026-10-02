"""A conversion refused for its archive source leaves the project as it was.

The route released the caller's own open session and scaffolded a
workflow before it discovered that the clone's envelope pins no image
vaibify can obtain and answered 409. The researcher lost the project
view they were converting from, and a workflow file appeared in a
project that had not been converted.
"""

import os

import pytest

from tests.testConvertToContainerRoute import (  # noqa: F401 - fixtures
    S_HOST_NAME, S_NEW_NAME, _fdictBody, _sConvertUrl,
    fixtureIsolateHostState, tclient,
)
from tests.testPromoteToHostProjectRoute import (
    _fbFlockIsStillHeld, _fdictOwnerHeaders, _fsInstallOwningBrowserSession,
)
from vaibify.config import registryManager


@pytest.mark.falsification
def testARefusedArchiveConversionKeepsTheSessionAndScaffoldsNothing(
    tclient, tmp_path,
):
    """Kills: releasing the session and scaffolding before the 409."""
    client, app = tclient
    sCredential = _fsInstallOwningBrowserSession(app, S_HOST_NAME)
    dictBody = dict(_fdictBody(), sEnvironmentSource="archive")
    response = client.post(
        _sConvertUrl(S_HOST_NAME), json=dictBody,
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert response.status_code == 409, response.text
    assert "does not pin an image" in response.text
    assert app.state.dictContainerOwners != {}
    assert _fbFlockIsStillHeld(S_HOST_NAME)
    assert not os.path.exists(tmp_path / S_HOST_NAME / ".vaibify" / "projects")
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None
    assert registryManager.fdictGetProject(S_NEW_NAME) is None
