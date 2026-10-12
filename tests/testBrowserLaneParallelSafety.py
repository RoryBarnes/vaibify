"""The browser lane's fixtures must stay safe under parallel workers.

The lane runs as ``pytest -n 6 --dist loadfile``: each worker starts
its own hub per module, and all of them share one machine and one
``test-results`` directory. Two workers must therefore never be able to
claim the same port, nor the same artifact directory. Neither collision
shows up in a serial run, so these properties are pinned here, in the
ordinary suite, where they hold whether or not Playwright is installed.
"""

import pathlib
import socket
from types import SimpleNamespace

import pytest

from tests.browser.conftest import (
    fpathArtifactDirectoryForTest,
    fsocketBindLoopbackPort,
)


pytestmark = pytest.mark.exclusive


@pytest.mark.falsification
def testAHubPortStaysHeldUntilItsServerServesIt():
    """No other process can bind the port the lane hands to uvicorn.

    Asking the OS for a free port and releasing it before uvicorn binds
    the number leaves a window in which another worker's hub can be
    given the same port; the loser then probes, and drives, the
    winner's hub.

    Kills: binding a throwaway socket to learn a free port and
    returning a socket that does not hold it.
    """
    with fsocketBindLoopbackPort() as socketHeld:
        iPort = socketHeld.getsockname()[1]
        assert iPort != 0, "the returned socket is not bound to a port"
        with socket.socket() as socketRival:
            with pytest.raises(OSError):
                socketRival.bind(("127.0.0.1", iPort))


@pytest.mark.falsification
def testSameNamedTestsInTwoModulesKeepSeparateArtifacts():
    """A test's trace and video directory is unique across the lane.

    Two modules define a test of the same name. Keyed by name alone,
    the one that passes deletes the directory the other is recording
    into on a sibling worker, or the failure artifacts it left for CI.

    Kills: keying the artifact directory by the test's name alone.
    """
    sSharedName = "test_an_unready_project_is_told_what_is_missing"
    nodeFirst = SimpleNamespace(
        path=pathlib.Path("tests/browser/testVerifyRefusesBeforeItWarns.py"),
        name=sSharedName,
    )
    nodeSecond = SimpleNamespace(
        path=pathlib.Path(
            "tests/browser/testTheRunMenuCanStartAnL3Verify.py"),
        name=sSharedName,
    )
    pathFirst = fpathArtifactDirectoryForTest(nodeFirst)
    pathSecond = fpathArtifactDirectoryForTest(nodeSecond)
    assert pathFirst != pathSecond
    assert pathFirst.parent == pathSecond.parent == pathlib.Path(
        "test-results")
