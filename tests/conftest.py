"""Session-wide pytest fixtures for the vaibify test suite.

The fixtures here guarantee that no test ever touches the researcher's
real host state: any file handlers already attached to the ``vaibify``
logger are detached and the CLI's logging configurator is redirected
to a session-scoped temporary directory (so tests that invoke the CLI
still exercise the real handler-attachment code path), and the OS
keyring is replaced with an in-memory fake (so no test can read,
overwrite, or delete the researcher's real stored credentials).
"""

import logging
import os

import pytest


def _fnRemoveFileHandlersFromVaibifyLogger():
    """Detach and close every file handler on the ``vaibify`` logger."""
    loggerVaibify = logging.getLogger("vaibify")
    for handlerAttached in list(loggerVaibify.handlers):
        if isinstance(handlerAttached, logging.FileHandler):
            loggerVaibify.removeHandler(handlerAttached)
            handlerAttached.close()


@pytest.fixture(scope="session", autouse=True)
def fnRedirectVaibifyLogFileForTests(tmp_path_factory):
    """Keep the entire test session out of ~/.vaibify/vaibify.log."""
    import vaibify.cli.main as cliMain
    sLogDir = str(tmp_path_factory.mktemp("vaibifyLog"))
    fnOriginalConfigure = cliMain._fnConfigureErrorLogging

    def fnConfigureRedirected(sLogDirOverride=None):
        fnOriginalConfigure(sLogDirOverride=sLogDirOverride or sLogDir)

    cliMain._fnConfigureErrorLogging = fnConfigureRedirected
    _fnRemoveFileHandlersFromVaibifyLogger()
    yield
    cliMain._fnConfigureErrorLogging = fnOriginalConfigure
    _fnRemoveFileHandlersFromVaibifyLogger()


class _FakeInMemoryKeyring:
    """Dict-backed stand-in for the OS keyring, one instance per test."""

    def __init__(self):
        self.dictStore = {}

    def get_password(self, sService, sName):
        return self.dictStore.get((sService, sName))

    def set_password(self, sService, sName, sValue):
        self.dictStore[(sService, sName)] = sValue

    def delete_password(self, sService, sName):
        from keyring.errors import PasswordDeleteError
        if (sService, sName) not in self.dictStore:
            raise PasswordDeleteError("no such entry")
        del self.dictStore[(sService, sName)]


@pytest.fixture(autouse=True)
def fixtureHermeticKeyring(monkeypatch):
    """Isolate every test from the researcher's real OS keyring.

    A test that reaches ``secretManager`` un-mocked must land in this
    in-memory fake, never in the host keychain: a real read makes test
    outcomes depend on the researcher's machine state, and a real
    write or delete can destroy a working credential (the same hazard
    class as the fixture above, for secrets instead of logs). Tests
    that need a stored credential request this fixture and seed
    ``keyringFake.dictStore`` directly.
    """
    keyringFake = _FakeInMemoryKeyring()
    monkeypatch.setattr(
        "vaibify.config.secretManager._fmoduleLoadKeyring",
        lambda: keyringFake,
    )
    yield keyringFake


@pytest.fixture(autouse=True)
def fnIsolateVaibifyStateDirectories(monkeypatch, tmp_path_factory):
    """Keep every test out of the researcher's real ~/.vaibify state.

    Each state directory is a module-level constant computed from
    ``os.path.expanduser`` at import, so a test that boots a hub or
    runs the CLI without its own redirect writes the operation journal,
    locks, hub-port.json, session slots, the registry, preferences,
    caffeinate pids, build staging, and the host-control socket into the
    REAL ~/.vaibify. That leak once clobbered a live hub's port
    registration — a running suite wrote hub-port.json into the
    researcher's home and pointed the survival contract at a dead port.

    Function-scoped so each test gets a FRESH home: the session-slot
    and cardinality machinery accumulates on-disk state, so a shared
    home would let one test's records leak into the next (a shared
    session scope was tried and produced exactly that cross-test
    pollution). A test that patches its own directory still overrides
    these, so per-test isolation is unchanged; what changes is that an
    UNpatched writer can no longer reach the real home. Same host-state
    hazard class as the log and keyring fixtures above, generalised from
    what used to be a journal-only redirect. The module-scoped browser
    lane boots its hub before this runs, so it carries its OWN redirect
    of the boot-time writers (registry, preferences, hub-port, sessions)
    in tests/browser/conftest.py.
    """
    from vaibify.cli import commandBuild
    from vaibify.config import (
        containerLock, hubPortRegistry, keepAliveManager,
        operationJournal, sessionRegistry,
    )
    from vaibify.gui import hostControlChannel
    # A dedicated dir, never a test's own ``tmp_path``: some tests rmdir
    # their whole tmp_path to model a missing directory, and a home
    # created inside it would make that rmdir fail on a non-empty tree.
    sHome = str(tmp_path_factory.mktemp("vaibifyHome"))

    def fnRedirectDirectory(moduleTarget, sAttribute, sSubdirectory=""):
        monkeypatch.setattr(
            moduleTarget, sAttribute,
            os.path.join(sHome, sSubdirectory) if sSubdirectory else sHome,
        )

    fnRedirectDirectory(containerLock, "_S_LOCK_DIRECTORY", "locks")
    fnRedirectDirectory(hubPortRegistry, "_S_VAIBIFY_DIRECTORY")
    fnRedirectDirectory(sessionRegistry, "_S_SESSION_DIRECTORY", "sessions")
    fnRedirectDirectory(keepAliveManager, "_S_PID_DIRECTORY", "caffeinate")
    fnRedirectDirectory(operationJournal, "_S_JOURNAL_DIRECTORY", "journal")
    fnRedirectDirectory(hostControlChannel, "_S_CONTROL_DIRECTORY", "control")
    fnRedirectDirectory(commandBuild, "_S_BUILD_STAGING_DIRECTORY", "build")
    fnRedirectDirectory(commandBuild, "_S_BUILD_HASH_DIRECTORY", "cache")
    # registryManager and preferencesStore are deliberately NOT
    # redirected here. Each precomputes full-path constants that the
    # long-lived hub reads at REQUEST time, and the lanes that boot a hub
    # already redirect them at their own scope (the module-scoped browser
    # lane, and tLiveHub for the headless lane). A function-scoped
    # redirect here would clobber those per test — the browser hub would
    # read an empty registry mid-test and render no tiles. They were
    # never the leak this fixture exists for (hub-port and session slots
    # were); leaving them to the per-lane fixtures keeps both correct.
    # ephemeralStore is deliberately NOT redirected here: it computes
    # its root from os.path.expanduser("~") at call time (no import-time
    # constant to patch), and its own tests exercise that real behaviour
    # — a blanket patch here broke them. It carries its own isolation.
    yield sHome


@pytest.fixture(autouse=True)
def fnStubTheDockerBinaryStatusProbes(request, monkeypatch):
    """Keep the ordinary suite off the host's ``docker`` executable.

    Same host-state hazard class as the log, keyring and journal
    fixtures above, and it hid for weeks because the hazard is
    INVERTED: those leak test state onto the researcher's machine,
    while this one silently borrows the machine's state into the test.

    ``/api/registry`` enriches each project with its image and container
    status, and both probes shell out to ``docker``. Tests about
    reservations, ownership and route wiring reach that listing
    incidentally -- they are not about Docker at all -- so on a machine
    WITH Docker installed they pass, and on one without they raise
    ``FileNotFoundError: 'docker'``. Every macOS CI runner is the
    second kind and every developer machine here is the first, so the
    suite was green locally and red on six macOS legs at once, for
    every Python version. A test whose verdict depends on what is
    installed on the machine running it is not testing the code.

    Answering "no image, no container" is the honest default: it is what
    a fresh machine reports, and the tests that care about a real
    daemon carry the ``docker`` or ``docker_live`` marker and are left
    alone here -- stubbing those would make a live-daemon lane pass
    without a daemon, which is the skip-reports-success failure this
    repository has already shipped once.
    """
    if any(
        request.node.get_closest_marker(sMarker) is not None
        for sMarker in ("docker", "docker_live", "dockerProbeUnderTest")
    ):
        yield
        return
    from vaibify.docker import containerManager, imageBuilder
    monkeypatch.setattr(
        imageBuilder, "fbImageExists", lambda sImageName: False,
    )
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        lambda sProjectName: {
            "bExists": False, "bRunning": False, "sStatus": "",
        },
    )
    yield


@pytest.fixture(autouse=True)
def fnClearPushDedupeCache():
    """Reset the syncRoutes push idempotency cache between tests.

    The cache is keyed by ``(container, pre-push HEAD sha, file-list
    digest)`` with a TTL. Several existing push tests share the same
    key space; a clean cache per test keeps a prior test's cached
    result from leaking into the next test's mock expectations.
    """
    from vaibify.gui.routes import syncRoutes
    syncRoutes._DICT_RECENT_PUSH_RESULTS.clear()
    yield
    syncRoutes._DICT_RECENT_PUSH_RESULTS.clear()
