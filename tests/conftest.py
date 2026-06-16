"""Session-wide pytest fixtures for the vaibify test suite.

The single fixture here guarantees that no test ever writes to the
researcher's real ``~/.vaibify/vaibify.log``: any file handlers already
attached to the ``vaibify`` logger are detached, and the CLI's logging
configurator is redirected to a session-scoped temporary directory so
tests that invoke the CLI still exercise the real handler-attachment
code path.
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
