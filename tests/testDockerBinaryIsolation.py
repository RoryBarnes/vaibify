"""The ordinary suite must not depend on a ``docker`` executable.

The sibling isolation fixtures in ``conftest.py`` stop a test writing
into the researcher's log, keyring or journal. This one guards the
INVERSE hazard, which is why it hid: those leak test state outward,
while this leaks the machine's state inward.

``/api/registry`` enriches every project with its image and container
status, and both probes shell out to ``docker``. Tests about
reservations, ownership and route wiring reach that listing
incidentally -- they are not about Docker at all. On a machine with
Docker installed they pass; on one without they raise
``FileNotFoundError: 'docker'``. Every macOS CI runner is the second
kind and every developer machine here is the first, so the suite ran
green locally while six macOS legs failed at once, on every Python
version, and the local green was read as evidence for weeks.

A test whose verdict depends on what happens to be installed on the
machine running it is not testing the code.
"""

__all__ = [
    "testAnOrdinaryTestNeverReachesTheDockerExecutable",
    "testADockerMarkedTestStillSeesTheRealProbe",
]

import pytest

from vaibify.docker import containerManager, imageBuilder


def testAnOrdinaryTestNeverReachesTheDockerExecutable():
    """An unmarked test sees stubbed status probes, not the binary.

    Asserted on the ANSWER and on the identity of the callable: a probe
    that merely returned False could be the real function finding no
    image on a machine that does have Docker, which would pass here and
    still fail on a runner without one.
    """
    assert imageBuilder.fbImageExists("anything:latest") is False
    assert imageBuilder.fbImageExists.__name__ == "<lambda>", (
        "the ordinary suite is reaching the real image probe, which "
        "shells out to the docker executable"
    )
    dictStatus = containerManager.fdictGetContainerStatus("anything")
    assert dictStatus == {
        "bExists": False, "bRunning": False, "sStatus": "",
    }
    assert containerManager.fdictGetContainerStatus.__name__ == "<lambda>"


@pytest.mark.docker
def testADockerMarkedTestStillSeesTheRealProbe():
    """A test that asks for a daemon is exempt from the stub.

    Without this direction the fixture would make the live-Docker lanes
    pass with no daemon present -- the skip-reports-success failure this
    repository has already shipped once, in a `docker info || exit 0`
    guard that turned an unreachable daemon green.
    """
    assert imageBuilder.fbImageExists.__name__ == "fbImageExists"
    assert (
        containerManager.fdictGetContainerStatus.__name__
        == "fdictGetContainerStatus"
    )
