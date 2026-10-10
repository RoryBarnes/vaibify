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
from vaibify.gui import cliShellContainment, hostControlChannel


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
        cliShellContainment._S_CLI_SHELL_DIRECTORY,
    ]
    listLeaked = [sPath for sPath in listConstants
                  if not _fbOutsideRealHome(sPath)]
    assert not listLeaked, f"these constants still reach the real home: {listLeaked}"


def _flistRealHomeLockFiles():
    sRealLocks = os.path.join(_sRealVaibifyHome(), "locks")
    return sorted(os.listdir(sRealLocks)) if os.path.isdir(sRealLocks) else []


def test_the_shadow_rerun_lock_lands_in_the_redirect_not_the_real_home():
    """The shadow-lane flock builds its path from the redirected constant.

    The writer used to expand ``~/.vaibify/locks`` itself, so every
    reproduction and shadow rerun the suite exercised left a lock file
    in the researcher's real home. Reading the directory through
    ``containerLock`` puts it under the one redirect.
    """
    from vaibify.reproducibility.shadowRerun import _fcontextHoldShadowLaneLock
    listRealBefore = _flistRealHomeLockFiles()
    with _fcontextHoldShadowLaneLock("resource-under-test"):
        listRedirected = os.listdir(containerLock._S_LOCK_DIRECTORY)
    assert any(sName.startswith("shadow-") for sName in listRedirected), (
        f"no shadow lock inside the redirect: {listRedirected}"
    )
    assert _flistRealHomeLockFiles() == listRealBefore, (
        "the shadow lock reached the real ~/.vaibify/locks"
    )


def test_a_keep_alive_started_by_the_suite_launches_no_caffeinate(monkeypatch):
    """A keep-alive asserted inside the suite never reaches the host.

    Regression for the ``caffeinate -s`` processes a running suite left
    behind, one per hub watchdog tick. The platform is forced to
    support keep-alives so the guard is exercised on Linux CI as well,
    and a launch fails the test outright rather than leaking a process.
    """
    def fnRefuseRealLaunch(*args, **kwargs):
        raise AssertionError(f"the suite launched a host process: {args}")

    monkeypatch.setattr(
        keepAliveManager, "fbPlatformSupportsKeepAlive", lambda: True,
    )
    monkeypatch.setattr(
        keepAliveManager.subprocess, "Popen", fnRefuseRealLaunch,
    )
    keepAliveManager.fnStartKeepAlive("isolationProbe")
    assert not keepAliveManager.fbKeepAliveIsLive("isolationProbe"), (
        "a declined spawn must record no pid for a later stop to kill"
    )
