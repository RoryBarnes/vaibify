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
        "vaibify.config.secretManager._fnLoadKeyringModule",
        lambda: keyringFake,
    )
    yield keyringFake


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
