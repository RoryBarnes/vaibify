"""The lane's hub never resolves the researcher's live secret root.

The browser hub runs the ``ephemeralSecretFiles`` reaper at startup,
and once swept the real ``~/.vaibify/tmp`` with a fake daemon that
reported nothing mounted, which deletes the file behind a live
container's secret mount (2026-10-30). The suite-wide redirect in
``tests/conftest.py`` is the fix; this asks the hub's own loop where
the root is. It is read-only: it plants and deletes nothing, because a
mutated run of it reaches the real root.
"""

import os
import pwd

import pytest

from vaibify.config import ephemeralStore


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def testTheHubResolvesTheSecretRootOutsideTheResearchersHome(serverHub):
    """Kills: the session fixture's rule returning the real home.

    The researcher's home comes from the password database, never from
    ``HOME``, which a scratch-home run of this lane sets on purpose.
    """
    sForbidden = os.path.realpath(os.path.join(
        pwd.getpwuid(os.getuid()).pw_dir, ".vaibify", "tmp"))
    sRoot = os.path.realpath(
        serverHub.fgenericCallOnTheHubLoop(ephemeralStore.fsGetEphemeralRoot))
    assert os.path.commonpath([sRoot, sForbidden]) != sForbidden, (
        f"the lane's hub resolved the researcher's secret root {sRoot}, "
        "which its startup sweep then reaches"
    )
