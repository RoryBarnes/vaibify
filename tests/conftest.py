"""Session-wide pytest fixtures for the vaibify test suite.

The fixtures here guarantee that no test ever touches the researcher's
real host state: any file handlers already attached to the ``vaibify``
logger are detached and the CLI's logging configurator is redirected
to a session-scoped temporary directory (so tests that invoke the CLI
still exercise the real handler-attachment code path), and the OS
keyring is replaced with an in-memory fake IN THIS PROCESS and with
the null backend in every child process (so no test can read,
overwrite, or delete the researcher's real stored credentials).
"""

import atexit
import contextlib
import fnmatch
import importlib
import logging
import os
import pwd
import shutil
import tempfile
import warnings
from typing import NamedTuple

import pytest


# Point every CHILD process at the null keyring backend, at import, so
# it is in place before collection can spawn anything.
#
# ``fixtureHermeticKeyring`` below patches ``_fmoduleLoadKeyring`` with
# ``monkeypatch``, which reaches this process and no other. Over a
# hundred test files spawn subprocesses -- CLI invocations, hubs,
# reconcile runs -- and each child imports the real ``keyring`` and
# talks to the researcher's real keychain. On macOS that is an
# approval dialog per read (four in one interrupted run,
# researcher-reported 2026-09-04), and a child reaching
# ``_fnDeleteKeyringEntry`` would remove a working credential with the
# suite reporting nothing -- the same hazard the in-process fake
# exists to prevent, one process boundary away.
#
# Set unconditionally rather than with ``setdefault``: a developer
# whose environment already names a real backend is exactly the case
# this must override. ``keyring`` honours the variable when it selects
# a backend, and the null backend answers every read with ``None``
# while accepting writes and deletes as no-ops, so a child neither
# prompts, reads, nor destroys. No test needs a real stored
# credential; the live lanes carry their own fake tokens.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"


def _fnKeepFirstTimeSetupOffTheResearchersShellConfiguration():
    """Record setup as done, in a scratch marker, before collection.

    ``vaibify.cli.main`` runs first-time setup when it is imported, and
    setup APPENDS to the researcher's real shell configuration file. A
    marker older than the current setup version runs it again, so a
    suite started on a machine that has not yet upgraded would edit
    the researcher's rc file during collection -- from whichever
    checkout the suite ran in, including a disposable worktree whose
    completion-script path vanishes with it. The marker constants are
    read at call time, so pointing them at a current-version marker in a
    scratch directory makes every in-process import a no-op. A CHILD
    process still resolves the real marker, and only a test that gives
    the child its own HOME avoids that.
    """
    from vaibify.install import shellSetup
    sMarkerDirectory = tempfile.mkdtemp(prefix="vaibifySetupMarker")
    atexit.register(shutil.rmtree, sMarkerDirectory, ignore_errors=True)
    shellSetup._MARKER_DIR = sMarkerDirectory
    shellSetup._MARKER_PATH = os.path.join(sMarkerDirectory, ".setup_done")
    shellSetup._fnWriteMarkerFile()


_fnKeepFirstTimeSetupOffTheResearchersShellConfiguration()


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


@pytest.fixture(scope="session", autouse=True)
def fnKeepTheSuiteFromLaunchingCaffeinate():
    """Never launch a real ``caffeinate`` from the test session.

    A hub booted by a test runs the real sleep-prevention watchdog
    against the researcher's real Docker daemon, while the per-test
    redirect below hands every test an empty caffeinate registry. Each
    watchdog tick therefore found no keep-alive and spawned another
    ``caffeinate -s`` into its own session, where it outlived the
    suite and accumulated on the host, pinning it awake. The spawn
    declines exactly as it
    does on a host without caffeinate (pid 0), never with a fake pid
    that a later stop would SIGTERM as if it were ours. Session scope,
    because the module-scoped browser hub ticks between tests too.
    """
    from vaibify.config import keepAliveManager
    fiOriginalSpawnCaffeinate = keepAliveManager._fiSpawnCaffeinate
    keepAliveManager._fiSpawnCaffeinate = lambda *aArguments: 0
    yield
    keepAliveManager._fiSpawnCaffeinate = fiOriginalSpawnCaffeinate


@pytest.fixture(scope="session", autouse=True)
def fnKeepSecretFilesOutOfTheResearchersHome():
    """Resolve the ephemeral secret root into a private directory.

    Every hub a test boots runs the ``ephemeralSecretFiles`` reaper at
    startup, and a test hub's Docker double reports what it was told to,
    not what the researcher's daemon mounts. Against the real
    ``~/.vaibify/tmp`` that deletes the host file behind a live
    container's secret mount, after which a VM-backed daemon can refuse
    every copy out of that container (2026-10-30). So the root's home
    resolves to a private directory beneath the home-visible scratch
    root, which the daemon can still see when a live test mounts a
    secret from it.

    A test that set its own ``HOME`` (or patched ``expanduser``) is
    honored, because the resolver's own tests assert exactly that.
    Session scope, because the module-scoped browser hubs run their
    first reaper pass before any function fixture. Yields the private
    home, so a test can plant a file where the redirected sweep looks.
    """
    from unittest.mock import patch
    from tests.homeVisibleTempRoot import fpathHomeVisibleTempRoot
    from vaibify.config import ephemeralStore
    pathScratchRoot = fpathHomeVisibleTempRoot()
    pathScratchRoot.mkdir(parents=True, exist_ok=True)
    sPrivateHome = tempfile.mkdtemp(
        prefix="secretRoot", dir=str(pathScratchRoot))
    sHomeAtSessionStart = os.path.expanduser("~")

    def fsResolveIsolatedHome():
        if os.path.expanduser("~") != sHomeAtSessionStart:
            return os.path.expanduser("~")
        return sPrivateHome

    try:
        with patch.object(
            ephemeralStore, "_fsResolveHomeDirectory", fsResolveIsolatedHome,
        ):
            yield sPrivateHome
    finally:
        shutil.rmtree(sPrivateHome, ignore_errors=True)


# Paths under the researcher's real ~/.vaibify that the sentinel below
# does not watch, as fnmatch patterns relative to it, each with its
# reason. A pattern also covers everything beneath it.
DICT_UNWATCHED_STATE_PATHS = {
    "browserLaneTmp": (
        "the suite's own home-visible scratch root, used on purpose when "
        "the checkout is not under HOME (tests/homeVisibleTempRoot.py)"
    ),
    "vaibify.log*": (
        "a running hub appends to its log every minute (observed over 30 "
        "minutes of an idle hub, 2026-11-02); the suite's own log writer is "
        "redirected for the session by fnRedirectVaibifyLogFileForTests"
    ),
}


def _fbIsUnwatchedStatePath(sRelative):
    return any(
        fnmatch.fnmatch(sRelative, sPattern)
        or sRelative.startswith(sPattern + os.sep)
        for sPattern in DICT_UNWATCHED_STATE_PATHS
    )


def fdictSnapshotStateDirectory(sStateRoot):
    """Return ``{sRelativePath: (st_size, st_mtime_ns, st_ino)}`` for watched files.

    Observation only: every file is ``lstat``-ed, none is opened, and
    symbolic links are not followed.
    """
    dictSnapshot = {}
    for sDirectory, listDirectories, listNames in os.walk(sStateRoot):
        listDirectories[:] = [
            sName for sName in listDirectories if not _fbIsUnwatchedStatePath(
                os.path.relpath(os.path.join(sDirectory, sName), sStateRoot))
        ]
        for sName in listNames:
            sPath = os.path.join(sDirectory, sName)
            sRelative = os.path.relpath(sPath, sStateRoot)
            if _fbIsUnwatchedStatePath(sRelative):
                continue
            try:
                statFile = os.lstat(sPath)
            except OSError:
                continue
            dictSnapshot[sRelative] = (
                statFile.st_size, statFile.st_mtime_ns, statFile.st_ino,
            )
    return dictSnapshot


def flistDescribeStateChanges(dictBefore, dictAfter):
    """Return one line per file created, deleted or modified between snapshots."""
    setBefore, setAfter = set(dictBefore), set(dictAfter)
    return (
        [f"created {sPath}" for sPath in sorted(setAfter - setBefore)]
        + [f"deleted {sPath}" for sPath in sorted(setBefore - setAfter)]
        + [f"modified {sPath}" for sPath in sorted(setBefore & setAfter)
           if dictBefore[sPath] != dictAfter[sPath]]
    )


def _fbRequireUntouchedHome():
    return (
        os.environ.get("CI") == "true"
        or os.environ.get("VAIBIFY_REQUIRE_UNTOUCHED_HOME") == "1"
    )


@pytest.fixture(scope="session", autouse=True)
def fnWatchTheResearchersRealStateDirectory():
    """Report any file the session created, deleted or changed in the real ~/.vaibify.

    The redirects above are the control; this is the alarm for one
    they missed. The home comes from the password database, never from
    HOME, which tests move. A live hub on the researcher's machine
    writes there too, so locally a change is one pytest warning; under
    ``CI=true`` or ``VAIBIFY_REQUIRE_UNTOUCHED_HOME=1`` it fails the
    session. Under xdist each worker watches and reports on its own.
    """
    sStateRoot = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".vaibify")
    dictBefore = fdictSnapshotStateDirectory(sStateRoot)
    yield
    listChanges = flistDescribeStateChanges(
        dictBefore, fdictSnapshotStateDirectory(sStateRoot),
    )
    if not listChanges:
        return
    sMessage = (
        f"the test session changed {len(listChanges)} file(s) in the real "
        f"{sStateRoot}:\n  " + "\n  ".join(listChanges[:50])
    )
    if _fbRequireUntouchedHome():
        pytest.fail(sMessage, pytrace=False)
    warnings.warn(sMessage)


@pytest.fixture(scope="session", autouse=True)
def fnSweepContainersLeftByAKilledLiveLane(request):
    """Reclaim labelled throwaway containers at both ends of a session.

    The live lanes tear their containers down in fixtures. A lane that
    is KILLED -- a CI cancel, a ``^C``, a harness timeout -- never
    reaches that teardown, so its container outlives the run. Ten of
    them accumulated on one researcher's daemon, with names that read
    like research environments (2026-09-21).

    Swept at the START as well as the end, because the start is the
    only one a killed run will ever reach again. Scoped to the label
    ``tests/liveContainerLabels`` writes and to nothing else: never a
    name pattern, never an age. And skipped entirely unless this
    selection actually contains live-daemon tests -- a unit run must
    not touch the researcher's daemon at all, which is the same rule
    the per-test Docker stubs above follow.
    """
    from tests.liveContainerLabels import flistSweepLiveLaneContainers
    if not _fbSelectionRunsLiveDockerTests(request):
        yield
        return
    flistSweepLiveLaneContainers()
    yield
    flistSweepLiveLaneContainers()


def _fbSelectionRunsLiveDockerTests(request):
    """Return True when this pytest selection includes a live-daemon test."""
    for itemTest in getattr(request.session, "items", []) or []:
        for sMarker in ("docker", "docker_live", "dockerProbeUnderTest"):
            if itemTest.get_closest_marker(sMarker) is not None:
                return True
    return False


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


class StateStoreRedirect(NamedTuple):
    """One host store the isolation fixture moves into each test's home.

    ``sAttribute`` names a module constant holding the store's path, or
    a resolver the store calls with no arguments each time it is used.
    ``sSubpath`` is where the store lands under the test's home ("" is
    the home itself), components separated by "/". A resolver whose
    real version creates its directory says so with ``bCreateOnResolve``.
    """

    sModule: str
    sAttribute: str
    sSubpath: str
    bCreateOnResolve: bool = False


# Every store under ~/.vaibify that fnIsolateVaibifyStateDirectories
# moves into each test's private home, declared once.
# tests/testArchitecturalInvariants.py scans vaibify/ for every path
# resolved under ~/.vaibify and requires each to be listed here or
# exempted there with a reason, and tests/testStateStoresStayInTheTestHome.py
# drives these stores with HOME moved to a surrogate.
T_REDIRECTED_STATE_STORES = (
    StateStoreRedirect("vaibify.config.containerLock", "_S_LOCK_DIRECTORY", "locks"),
    StateStoreRedirect(
        "vaibify.config.containerLock", "_S_LIFECYCLE_LOCK_DIRECTORY",
        "lifecycleLocks",
    ),
    StateStoreRedirect(
        "vaibify.config.pendingRenames", "_S_PENDING_RENAMES_DIRECTORY",
        "pendingRenames",
    ),
    StateStoreRedirect(
        "vaibify.config.pendingConversions", "_S_CONVERSIONS_DIRECTORY",
        "pendingConversions",
    ),
    StateStoreRedirect("vaibify.gui.stateWriteLock", "S_STATE_LOCK_DIRECTORY", "stateLocks"),
    StateStoreRedirect("vaibify.config.hubPortRegistry", "_S_VAIBIFY_DIRECTORY", ""),
    StateStoreRedirect("vaibify.config.sessionRegistry", "_S_SESSION_DIRECTORY", "sessions"),
    StateStoreRedirect("vaibify.config.keepAliveManager", "_S_PID_DIRECTORY", "caffeinate"),
    StateStoreRedirect("vaibify.config.operationJournal", "_S_JOURNAL_DIRECTORY", "journal"),
    StateStoreRedirect("vaibify.gui.hostControlChannel", "_S_CONTROL_DIRECTORY", "control"),
    StateStoreRedirect("vaibify.gui.cliShellContainment", "_S_CLI_SHELL_DIRECTORY", "cliShells"),
    StateStoreRedirect("vaibify.cli.commandBuild", "_S_BUILD_STAGING_DIRECTORY", "build"),
    StateStoreRedirect("vaibify.cli.commandBuild", "_S_BUILD_HASH_DIRECTORY", "cache"),
    StateStoreRedirect(
        "vaibify.reproducibility.reproductionSource",
        "_S_REPRODUCTIONS_DIRECTORY", "reproductions",
    ),
    StateStoreRedirect(
        "vaibify.reproducibility.imageAcquisition",
        "_S_DEPOSIT_LOADS_DIRECTORY", "depositLoads",
    ),
    StateStoreRedirect(
        "vaibify.reproducibility.downloadPartial", "_S_DOWNLOAD_ROOT",
        "imageArchive",
    ),
    StateStoreRedirect(
        "vaibify.config.containerConfig", "_S_REPOSITORY_LISTS_DIRECTORY",
        "repositoryLists",
    ),
    StateStoreRedirect("vaibify.config.hostResidue", "S_BUILD_CONTEXT_ROOT", "build"),
    StateStoreRedirect("vaibify.config.hostResidue", "S_BUILD_HASH_ROOT", "cache"),
    StateStoreRedirect("vaibify.install.runtimeSetupRunner", "_S_LOCK_DIRECTORY", "locks"),
    StateStoreRedirect(
        "vaibify.host.hostScratch", "_S_HOST_DIAGNOSTICS_ROOT",
        "tmp/host-diagnostics",
    ),
    # Resolved per call, not as constants. A live test that obtained an
    # image once left an origin record in the real home; the council
    # credential document and snapshot scope would be WRITTEN by an
    # admission; a Prompt Record capture would CREATE a digest key.
    StateStoreRedirect(
        "vaibify.config.imageOrigins", "fsOriginsDirectory", "imageOrigins",
        bCreateOnResolve=True,
    ),
    StateStoreRedirect(
        "vaibify.config.dashboardRecords", "fsDashboardStateRoot",
        "dashboardState",
    ),
    StateStoreRedirect(
        "vaibify.gui.agentCouncilCredentialGate",
        "fsResolveCredentialEvidencePath",
        "agentCouncils/credentialEvidence.json",
    ),
    StateStoreRedirect(
        "vaibify.gui.agentCouncilSnapshotScope",
        "fsResolveSnapshotScopeDirectory", "agentCouncils/snapshotScope",
    ),
    StateStoreRedirect(
        "vaibify.gui.promptRecordManager", "fsResolveDigestKeyPath",
        "promptRecordDigestKey",
    ),
    # The token migration journals and the remote bindings. Every hub a
    # test booted wrote a migration journal into the real home when it
    # opened a project, until these were listed (2026-11-02).
    StateStoreRedirect(
        "vaibify.config.zenodoMigrationJournal", "fsMigrationsDirectory",
        "migrations",
    ),
    StateStoreRedirect(
        "vaibify.config.remoteBindings", "fsBindingsDirectory", "bindings",
    ),
    StateStoreRedirect("vaibify.docker.projectEgress", "_fsEgressRoot", "egress"),
    StateStoreRedirect("vaibify.docker.x11Authorizations", "fsX11Root", "x11"),
    # A hub's scheduled re-verify stamps its first pass a short delay
    # after start, which a browser-lane hub lives long enough to reach.
    StateStoreRedirect(
        "vaibify.reproducibility.scheduledReverify", "fsReverifyStatePath",
        "reverifyState.json",
    ),
    # A connect checks the supervised interval and writes its anchor.
    StateStoreRedirect("vaibify.gui.supervisionAnchor", "_fsAnchorDirectory", "supervision"),
)


def fsRedirectedStorePath(sHome, tStoreRedirect):
    """Return where one declared store lands under the test home ``sHome``."""
    listParts = [sPart for sPart in tStoreRedirect.sSubpath.split("/") if sPart]
    return os.path.join(sHome, *listParts)


def _ffnResolveIntoTheTestHome(sPath, bCreateOnResolve):
    """Return a resolver that answers ``sPath``, creating it when asked to."""

    def fsResolveIntoTheTestHome():
        if bCreateOnResolve:
            os.makedirs(sPath, exist_ok=True)
        return sPath

    return fsResolveIntoTheTestHome


def _fnRedirectStateStore(monkeypatch, sHome, tStoreRedirect):
    """Point one declared store's constant or resolver into ``sHome``."""
    moduleStore = importlib.import_module(tStoreRedirect.sModule)
    sPath = fsRedirectedStorePath(sHome, tStoreRedirect)
    if callable(getattr(moduleStore, tStoreRedirect.sAttribute)):
        monkeypatch.setattr(
            moduleStore, tStoreRedirect.sAttribute,
            _ffnResolveIntoTheTestHome(sPath, tStoreRedirect.bCreateOnResolve),
        )
        return
    monkeypatch.setattr(moduleStore, tStoreRedirect.sAttribute, sPath)


@contextlib.contextmanager
def fcontextRedirectEveryStateStore(sHome):
    """Hold every declared store redirect open for the life of the context.

    The autouse fixture below redirects per TEST. A hub kept open across
    a whole module (the browser lane's) runs its startup hooks, its
    first reaper pass, the gaps between tests and its shutdown outside
    any test, so it needs the redirects for its own lifetime: without
    them its keep-alive reaper signalled the researcher's real
    caffeinate, and its recoveries read the real rename and conversion
    journals against a registry that did not know their projects.
    """
    monkeypatchStores = pytest.MonkeyPatch()
    try:
        for tStoreRedirect in T_REDIRECTED_STATE_STORES:
            _fnRedirectStateStore(monkeypatchStores, sHome, tStoreRedirect)
        yield
    finally:
        monkeypatchStores.undo()


@pytest.fixture(autouse=True)
def fnIsolateVaibifyStateDirectories(monkeypatch, tmp_path_factory):
    """Keep every test out of the researcher's real ~/.vaibify state.

    Most state directories are module-level constants computed from
    ``os.path.expanduser`` at import, and the rest are resolvers asked
    at call time, so a test that boots a hub or runs the CLI without
    its own redirect writes the operation journal, locks, hub-port.json,
    session slots, caffeinate pids, build staging, the host-control
    socket, token migration journals and remote bindings into the REAL
    ~/.vaibify. That leak once clobbered a live hub's port registration
    — a running suite wrote hub-port.json into the researcher's home and
    pointed the survival contract at a dead port — and later left over
    a thousand migration journals there. The stores are declared once,
    in ``T_REDIRECTED_STATE_STORES``.

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
    # A dedicated dir, never a test's own ``tmp_path``: some tests rmdir
    # their whole tmp_path to model a missing directory, and a home
    # created inside it would make that rmdir fail on a non-empty tree.
    sHome = str(tmp_path_factory.mktemp("vaibifyHome"))
    for tStoreRedirect in T_REDIRECTED_STATE_STORES:
        _fnRedirectStateStore(monkeypatch, sHome, tStoreRedirect)
    # registryManager and preferencesStore are deliberately NOT
    # redirected here. Each precomputes full-path constants that the
    # long-lived hub reads at REQUEST time, and the lanes that boot a hub
    # already redirect them at their own scope (the module-scoped browser
    # lane, and tLiveHub for the headless lane). A function-scoped
    # redirect here would clobber those per test — the browser hub would
    # read an empty registry mid-test and render no tiles. They were
    # never the leak this fixture exists for (hub-port and session slots
    # were); leaving them to the per-lane fixtures keeps both correct.
    # The two session-lifetime preferences ARE neutralised here, by
    # patching their readers rather than the store's path. The session
    # cap and the sliding-idle window are resolved on every evaluator
    # pass, so without this the researcher's own ~/.vaibify preference
    # would silently change what a few hundred lifecycle assertions
    # mean — and a suite that behaves differently on the machine that
    # set the preference is the exact failure this fixture exists to
    # prevent. Returning "" reads as UNSET, so the default (or an env
    # override, which still wins) applies. A test of the preference
    # tier patches these back.
    # The Quit admission gate is one per process. A test whose Quit
    # succeeds leaves it closed, exactly as a hub that is exiting would,
    # so each test starts with a fresh, open gate.
    from vaibify.config import mutationAdmission
    monkeypatch.setattr(
        mutationAdmission, "_dictAdmissionGate",
        {"sGateId": "", "fExpiresMonotonic": None},
    )
    from vaibify.config import preferencesStore
    monkeypatch.setattr(
        preferencesStore, "fsSessionCapPreference", lambda: "",
    )
    monkeypatch.setattr(
        preferencesStore, "fsSlidingIdlePreference", lambda: "",
    )
    # The ephemeral secret root is redirected once per session, by
    # fnKeepSecretFilesOutOfTheResearchersHome above, not here: the
    # browser hubs sweep it before any function fixture runs, and that
    # fixture's rule honors a test that set its own HOME.
    yield sHome


@pytest.fixture(autouse=True)
def fnKeepPendingConversionsFromObtainingForReal(
    monkeypatch, fnIsolateVaibifyStateDirectories,
):
    """A route test that starts a containerization must not reach Docker.

    The convert route hands the wizard's answers to the lifecycle, which
    starts a worker thread that opens a Docker client and downloads the
    author's image. Under test that thread would run against the real
    daemon and the real archive. The worker's starting seam is stubbed to
    do nothing, so the record is written and the view reads
    "interrupted"; a test OF the worker restores
    ``conversionLifecycle.fnStartConversionWorker`` on the seam. The
    record directory is one of ``T_REDIRECTED_STATE_STORES``, under the
    isolated home, rather than a second temporary directory per test:
    every ``tmp_path_factory.mktemp`` lists the whole numbered basetemp
    to pick its suffix, so one extra call per test grows the serial unit
    lane by minutes and pushed it past its CI timeout.
    """
    from vaibify.gui import conversionLifecycle
    del fnIsolateVaibifyStateDirectories
    monkeypatch.setattr(
        conversionLifecycle, "_fnStartWorker", lambda sHostName: None,
    )


@pytest.fixture(autouse=True)
def fnKeepTheRangedTransportOnLoopback(monkeypatch):
    """The deposit download owns its sockets; no unit test may open one
    to a host beyond this machine. A test that stubs the record fetch
    but forgets the file fetch would otherwise contact the real
    archive, retry it six times with backoff, and read as a hang. The
    guard refuses the connection by name; tests of the download serve
    their bytes from a loopback server and are untouched by it.
    """
    from urllib.parse import urlsplit
    from vaibify.reproducibility import rangedHttpTransport
    fsocketReal = rangedHttpTransport._fsocketOpenToUrl

    def fsocketOpenOnLoopbackOnly(sUrl):
        sHost = urlsplit(sUrl).hostname or ""
        if sHost not in ("127.0.0.1", "localhost", "::1"):
            raise rangedHttpTransport.TransportFailure(
                f"the test suite refuses to open a connection to {sHost}",
                rangedHttpTransport.S_FAILURE_REFUSED_HOST, False,
            )
        return fsocketReal(sUrl)

    monkeypatch.setattr(
        rangedHttpTransport, "_fsocketOpenToUrl", fsocketOpenOnLoopbackOnly,
    )


@pytest.fixture(autouse=True)
def fnAnswerReaderAccessWithoutTheNetwork(monkeypatch):
    """The verify asks GitHub and Zenodo what a READER is served; no unit
    test may. Both observers answer "public" here, the boringly-valid
    default ``syncStatusFixtures.fdictBuildCachedVerify`` also uses, so a
    test about hashing reads exactly as it did before the question
    existed. The Zenodo observation holds a record with no files: a test
    that compares files patches ``zenodoClient.fdictHashRecordFiles``
    (``tests/zenodoObservationFixtures.py``). The observers' own tests
    import the real functions at module load and drive them against
    loopback servers; a test wanting another verdict patches the module
    attribute itself.
    """
    from vaibify.reproducibility import githubMirror, zenodoClient

    def fdictAnswerPublic(sOwner, sRepo):
        return {"sState": githubMirror.S_ACCESS_PUBLIC,
                "sReason": f"(test) {sOwner}/{sRepo} is public"}

    def fdictObserveOpenRecord(sRecordId, sService="sandbox"):
        return {"sRecordId": str(sRecordId),
                "sState": zenodoClient.S_RECORD_PUBLIC,
                "sReason": f"(test) record {sRecordId} is open",
                "dictRecord": {"metadata": {"access_right": "open"},
                               "files": []}}

    monkeypatch.setattr(
        githubMirror, "fdictProbePublicAccess", fdictAnswerPublic)
    monkeypatch.setattr(
        zenodoClient, "fdictObserveRecord", fdictObserveOpenRecord)
    githubMirror.fnClearAnonymousBackoff()


@pytest.fixture(autouse=True)
def fnKeepBuildPreflightProbesOffTheDaemonAndTheNetwork(monkeypatch):
    """The build preflight asks the daemon's disk and pypi.org; no unit
    test may. Both probes answer "room enough" and "the name exists"
    here, so a test on a full machine or an offline one reads the same
    as CI; the probes' own tests hold the original functions and drive
    them with fakes, and a test wanting a refusal patches the module
    attribute itself (2026-09-21: the real disk probe refused every
    route test on a daemon whose disk was actually full).
    """
    from vaibify.cli import daemonDiskPreflight, pythonPackagePreflight
    monkeypatch.setattr(
        daemonDiskPreflight, "fiDaemonFreeDiskBytes",
        lambda fnRun=None: daemonDiskPreflight.I_DAEMON_FREE_DISK_WARN_BYTES,
    )
    monkeypatch.setattr(
        pythonPackagePreflight, "fbNameExistsOnIndex",
        lambda sName, *aArgs, **kwargs: True,
    )
    from vaibify.cli import repositoryPreflight, systemPackagePreflight
    monkeypatch.setattr(
        systemPackagePreflight, "fbPackageExistsInArchive",
        lambda sName, sSeries, *aArgs, **kwargs: True,
    )
    monkeypatch.setattr(
        repositoryPreflight, "fdictProbeRepositoryBranch",
        lambda sUrl, sBranch, *aArgs, **kwargs: {
            "bBranchExists": True, "sDefaultBranch": "main",
        },
    )


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
    from vaibify import docker as packageDocker
    from vaibify.docker import containerManager, imageBuilder
    monkeypatch.setattr(
        imageBuilder, "fbImageExists", lambda sImageName: False,
    )
    # BOTH names, because the probe has two and production calls the
    # other one: `commandStart._fpreflightImage` does `from
    # vaibify.docker import fbImageExists`, which binds the package
    # re-export rather than the module attribute. Stubbing only the
    # module left every test reaching a pre-flight image check running
    # the REAL docker probe -- invisible on a machine with Docker
    # installed, and a FileNotFoundError on a macOS runner without it.
    monkeypatch.setattr(
        packageDocker, "fbImageExists", lambda sImageName: False,
    )
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        lambda sProjectName: {
            "bExists": False, "bRunning": False, "sStatus": "",
        },
    )
    yield


@pytest.fixture(autouse=True)
def fnKeepCouncilStartupOffTheRealDaemon(request, monkeypatch):
    """A non-live test's app startup must not reach a real daemon.

    ``appFactory``'s council startup reconcile builds its OWN Docker
    client rather than using the connection the test injected, so a
    route test that context-manages a TestClient runs
    ``fdictReconcileLabeledRunnersOnRestart`` against whatever daemon
    the machine has — and that function DESTROYS every container
    carrying the council label, by design, because a restarted hub
    cannot account for survivors.

    On a developer machine with Docker up, that reaches across the
    process and kills the runner a live test is using at that moment.
    Reproduced directly: a labelled runner reported Running True, and
    one reconcile later the daemon answered NotFound. It surfaced as a
    409 Conflict on exec start in exactly one full-suite run, in a
    test that passes alone and passes beside its neighbours — the
    shape that gets written off as flake.

    The live lanes are left alone: they carry the markers, they own
    real containers on purpose, and stubbing them would be the
    skip-reports-success failure this repository has already shipped.
    """
    if any(
        request.node.get_closest_marker(sMarker) is not None
        for sMarker in ("docker", "docker_live", "dockerProbeUnderTest")
    ):
        yield
        return
    from vaibify.gui import appFactory
    monkeypatch.setattr(
        appFactory, "_fdockerCreateCouncilClientOrNone", lambda: None)
    yield


@pytest.fixture(autouse=True)
def fnKeepEgressOffTheRealDaemon(request, monkeypatch):
    """A non-live test's hub must not run egress commands on a real daemon.

    The hub's egress reconcile shells out to ``docker`` itself, so every
    test that boots a hub with a TestClient ran it against the machine's
    daemon; a browser-lane hub doing the same removed a developer's
    running proxy (2026-11-08). Every egress command is refused here
    unless the test is a live one; a test of the module itself replaces
    the runner with its own scripted daemon, which takes precedence.
    """
    if any(
        request.node.get_closest_marker(sMarker) is not None
        for sMarker in ("docker", "docker_live", "dockerProbeUnderTest")
    ):
        yield
        return
    from vaibify.docker import egressProxy, projectEgress

    def fnRefuseEgressDockerCall(listArguments, fTimeoutSeconds=None):
        raise egressProxy.EgressSetupError(
            "a non-live test runs no egress command on a real daemon: "
            + " ".join(listArguments[:2]))

    monkeypatch.setattr(projectEgress, "_fprocessDocker", fnRefuseEgressDockerCall)
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


@pytest.fixture(autouse=True)
def fnPinCouncilCapacityToTheDeclaredFloors(request, monkeypatch):
    """Make the council's machine-scaled bounds machine-INDEPENDENT.

    The snapshot bounds scale with host RAM and daemon memory, which
    means an unpinned assertion about them is an assertion about the
    developer's laptop: the same test would pass on a 16 GB machine and
    fail on an 8 GB runner, and the failure would look like a bug in
    the code under test. Pinning the host reading to "unknown" makes
    every test see the declared floors -- the bounds the design was
    reviewed against -- unless it asks otherwise.

    ``councilCapacity`` opts out, for the tests whose whole subject IS
    the scaling.
    """
    if request.node.get_closest_marker("councilCapacity") is not None:
        yield
        return
    from vaibify.gui import agentCouncilCapacity
    monkeypatch.setattr(
        agentCouncilCapacity, "fiReadHostMemoryBytes", lambda: 0)
    yield


@pytest.fixture(autouse=True)
def fnKeepEveryTestOutOfTheRealCouncilStore(
        request, tmp_path_factory, monkeypatch):
    """No test may touch the researcher's ~/.vaibify/agentCouncils.

    The durable council store root is derived from $HOME, and any hub a
    test builds runs a startup reconcile that classifies every campaign
    still in `planning` as `interrupted`. So a suite that constructs a
    hub kills a live council mid-deliberation — with a reason string
    that reads like the researcher's own hub restarting.

    This happened TWICE on 2026-08-24. The first fix was applied to the
    browser lane's conftest alone, because that was the lane that had
    done it; the route tests did the same thing an hour later. Fixing
    the instance is not fixing the class, so the guard now lives at the
    root conftest where it covers every suite, present and future.

    Autouse and unconditional: a test that genuinely wants the real
    root would be a test that can destroy a researcher's work, and
    there is no such legitimate test.
    """
    if request.node.get_closest_marker("realCouncilStoreRoot") is not None:
        yield
        return
    from vaibify.gui import agentCouncilStore
    sIsolatedRoot = str(
        tmp_path_factory.mktemp("councilStore") / "agentCouncils")
    monkeypatch.setattr(
        agentCouncilStore, "fsResolveDurableStoreRoot",
        lambda: sIsolatedRoot)
    yield


@pytest.fixture
def fixtureReachApproved(monkeypatch):
    """Stand in for the researcher's recorded approval of what a container may reach.

    The run-argument builder refuses a configuration that widens the
    container's reach (open network mode, X11, bind mounts, allowed
    hosts) until this host holds the researcher's approval of exactly
    those values; ``tests/testReachApprovalGuardsTheLaunch.py`` is that
    gate's own kill-confirmed guard. A module whose subject is something
    else -- the arguments open mode adds, the X11 arguments, a resource
    limit -- opts in with ``pytestmark =
    pytest.mark.usefixtures("fixtureReachApproved")`` rather than
    registering a project and recording an approval for every fixture
    configuration it builds.
    """
    from vaibify.docker import containerManager
    monkeypatch.setattr(
        containerManager, "_fnRequireReachApproval", lambda config: None)
