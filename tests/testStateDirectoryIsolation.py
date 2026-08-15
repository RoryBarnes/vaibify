"""The session safety net keeps every ~/.vaibify writer off the real home.

Regression for the leak where a running suite booted a hub and wrote
``hub-port.json`` into the researcher's real ~/.vaibify, repointing the
port survival contract at a now-dead port. The autouse
``fnIsolateVaibifyStateDirectories`` fixture in ``conftest`` redirects
the state-directory constants; these tests prove the actual writers
land inside the redirect, and that every redirected constant points
outside the real home.
"""

import os

from vaibify.config import (
    containerLock,
    hubPortRegistry,
    keepAliveManager,
    operationJournal,
    sessionRegistry,
)
from vaibify.gui import hostControlChannel


def _sRealVaibifyHome():
    return os.path.expanduser("~/.vaibify")


def _fbOutsideRealHome(sPath):
    sReal = _sRealVaibifyHome()
    return sPath != sReal and not sPath.startswith(sReal + os.sep)


def test_hub_port_persist_lands_in_the_redirect_not_the_real_home():
    """The exact writer that leaked now writes only inside the redirect."""
    hubPortRegistry.fnPersistHubPort(54321)
    sWritten = hubPortRegistry.fsHubPortPath()
    assert os.path.isfile(sWritten), "the hub port was persisted somewhere"
    assert _fbOutsideRealHome(sWritten), (
        f"hub-port.json leaked into the real home: {sWritten}"
    )
    assert hubPortRegistry.fiReadPersistedHubPort() == 54321


def test_every_redirected_state_constant_points_outside_the_real_home():
    """No redirected state-directory constant resolves under the home.

    registryManager and preferencesStore are intentionally absent: the
    root fixture leaves them to the hub-booting lanes (see conftest), so
    they are not redirected here and are not asserted here.
    """
    listConstants = [
        containerLock._S_LOCK_DIRECTORY,
        hubPortRegistry._S_VAIBIFY_DIRECTORY,
        sessionRegistry._S_SESSION_DIRECTORY,
        keepAliveManager._S_PID_DIRECTORY,
        operationJournal._S_JOURNAL_DIRECTORY,
        hostControlChannel._S_CONTROL_DIRECTORY,
    ]
    listLeaked = [sPath for sPath in listConstants
                  if not _fbOutsideRealHome(sPath)]
    assert not listLeaked, f"these constants still reach the real home: {listLeaked}"
