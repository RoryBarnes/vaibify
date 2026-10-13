"""A step's committed ``tests/conftest.py`` is a loader vaibify never rewrites.

A published project commits its conftests. While each one carried the
whole marker plugin, stamped with its generation, every vaibify release
that bumped the generation rewrote every committed copy: a reader's
clone showed them as modified tracked files the moment it connected,
and the reproduction baseline counted them.

The committed file is now a loader whose bytes never change. The plugin
it loads lives in ``.vaibify/pytestPlugin/``, a directory that ignores
itself, so vaibify rewrites it freely and git never sees it. These tests
drive real files, a real ``git``, the real refresh paths through a real
``HostConnection`` (connect, poll, run), and real pytest sessions.
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import conftestManager, pipelineRunner, pipelineServer
from vaibify.gui.routes import pipelineRoutes
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection


S_RESOURCE = "conftestLoaderProject"
S_SLUG = "demo"
LIST_STEP_DIRECTORIES = ["StepAlpha", "StepBeta"]
S_AUTHOR_GENERATION = "7"
S_BUMPED_GENERATION = "10"
S_PASSING_TESTS = (
    "def test_one():\n    assert True\n\n"
    "def test_two():\n    assert True\n")
S_ONE_FAILING_TEST = (
    "def test_one():\n    assert True\n\n"
    "def test_two():\n    assert False\n")
S_BROKEN_TEST_FILE = "def test_broken(:\n    pass\n"
T_VOLATILE_MARKER_KEYS = ("fTimestamp", "sRunAtUtc", "sRunId")


@pytest.fixture(autouse=True)
def fixtureIsolateHostExecution(tmp_path, monkeypatch):
    """Keep the host exec journal, locks and scratch inside tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )
    conftestManager.fnClearRefreshCaches()
    yield
    conftestManager.fnClearRefreshCaches()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _fnWriteText(sPath, sContent):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileOut:
        fileOut.write(sContent)


def _fsReadText(sPath):
    with open(sPath, encoding="utf-8") as fileIn:
        return fileIn.read()


def _fsRunGit(sRepo, *tArguments):
    processGit = subprocess.run(
        ["git", "-c", "user.email=lane@example.invalid",
         "-c", "user.name=Loader Lane", "-c", "commit.gpgsign=false"]
        + list(tArguments),
        cwd=sRepo, capture_output=True, text=True, check=True,
    )
    return processGit.stdout


def _flistGitStatus(sRepo):
    return [
        sLine for sLine in _fsRunGit(
            sRepo, "status", "--porcelain", "--untracked-files=all",
        ).splitlines() if sLine.strip()
    ]


def _fsStampedFullConftest(sProjectRepoPath, sGeneration):
    """Return a whole-plugin conftest of an older generation, as committed."""
    sSource = conftestManager.fsBuildConftestSource(sProjectRepoPath)
    return sSource.replace(
        conftestManager.S_CONFTEST_VERSION_PREFIX
        + conftestManager.S_CONFTEST_VERSION,
        conftestManager.S_CONFTEST_VERSION_PREFIX + sGeneration, 1,
    )


def _fsConftestPath(sRepo, sStep):
    return os.path.join(sRepo, sStep, "tests", "conftest.py")


def _fnLayDownProject(sRepo, sConftestSource, sTestSource=S_PASSING_TESTS):
    """Write a two-step project whose steps commit the given conftest."""
    _fnWriteText(
        os.path.join(sRepo, ".vaibify", "projects", S_SLUG + ".json"),
        json.dumps({"listSteps": [
            {"sName": sStep, "sDirectory": sStep,
             "saOutputDataFiles": ["out.dat"], "saPlotFiles": []}
            for sStep in LIST_STEP_DIRECTORIES
        ]}),
    )
    _fnWriteText(os.path.join(sRepo, ".vaibify", ".gitignore"), "state.json\n")
    for sStep in LIST_STEP_DIRECTORIES:
        _fnWriteText(os.path.join(sRepo, sStep, "out.dat"), sStep + "\n")
        _fnWriteText(_fsConftestPath(sRepo, sStep), sConftestSource)
        _fnWriteText(
            os.path.join(sRepo, sStep, "tests", "test_integrity_step.py"),
            sTestSource,
        )


def _fsPublishAuthorProject(sRoot):
    """Return an author repository committing generation-7 conftests."""
    sRepo = os.path.join(sRoot, "author")
    _fnLayDownProject(
        sRepo,
        _fsStampedFullConftest("/workspace/author", S_AUTHOR_GENERATION),
    )
    _fsRunGit(sRepo, "init", "-q")
    _fsRunGit(sRepo, "add", "-A")
    _fsRunGit(sRepo, "commit", "-q", "-m", "Publish the project")
    return sRepo


def _fsCloneForReader(sRoot, sAuthorRepo):
    sReader = os.path.join(sRoot, "reader")
    subprocess.run(
        ["git", "clone", "-q", sAuthorRepo, sReader],
        check=True, capture_output=True,
    )
    return sReader


def _fconnectionOver(sProjectRoot):
    return HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot)


def _fdictWorkflow(sRepo):
    return {
        "sProjectRepoPath": sRepo,
        "listSteps": [
            {"sName": sStep, "sDirectory": sStep}
            for sStep in LIST_STEP_DIRECTORIES
        ],
    }


# ---------------------------------------------------------------------------
# The three refresh paths, driven the way the dashboard drives them
# ---------------------------------------------------------------------------

def _fnConnect(connection, sRepo):
    asyncio.run(pipelineServer._fnRefreshConftestsAndMigrateMarkers(
        {"docker": connection}, S_RESOURCE, _fdictWorkflow(sRepo),
        os.path.join(sRepo, ".vaibify", "projects", S_SLUG + ".json"),
    ))


def _fnPoll(connection, sRepo):
    asyncio.run(pipelineRoutes._fnRefreshConftestsAndMigrateMarkers(
        connection, S_RESOURCE, LIST_STEP_DIRECTORIES, sRepo, S_SLUG,
    ))


def _flistRunPreflight(connection, sRepo):
    return pipelineRunner._flistCollectPreflightWarnings(
        connection, S_RESOURCE, _fdictWorkflow(sRepo),
    )


def _fnConnectPollAndRun(connection, sRepo):
    """Drive every refresh path once, each one probing afresh."""
    _fnConnect(connection, sRepo)
    conftestManager.fnClearRefreshCaches()
    _fnPoll(connection, sRepo)
    conftestManager.fnClearRefreshCaches()
    assert _flistRunPreflight(connection, sRepo) == []


def _fdictFileStamps(listPaths):
    return {
        sPath: (os.stat(sPath).st_mtime_ns, os.stat(sPath).st_ino)
        for sPath in listPaths
    }


def _fnAgeFiles(listPaths):
    """Give each file an old, distinctive mtime so any rewrite shows."""
    for sPath in listPaths:
        os.utime(sPath, ns=(10**18, 10**18))


# ---------------------------------------------------------------------------
# The committed loader is stable across a generation bump
# ---------------------------------------------------------------------------

def test_the_loader_bytes_are_pinned():
    """The loader is a constant across vaibify versions, so pin its bytes.

    Changing a single byte would dirty every published project that
    committed it. A deliberate change needs a new loader stamp AND a
    migration story; it is never a silent edit.
    """
    sLoader = conftestManager.S_CONFTEST_LOADER_SOURCE
    assert sLoader.startswith("# vaibify-conftest-loader: 1\n")
    assert "vaibify-conftest-version" not in sLoader
    assert hashlib.sha256(sLoader.encode("utf-8")).hexdigest() == (
        "3434714c0f4ca9e514049adbeb53461f7bc60b3c1e844b11d964c2d22cdea066"
    )


@pytest.mark.falsification
def test_a_migrated_clone_stays_clean_through_connect_poll_and_run(
    tmp_path, monkeypatch,
):
    """After the author migrates once, a reader's clone is never dirtied.

    The author's connect turns their generation-7 conftests into the
    loader; they commit it. A reader then clones under a vaibify whose
    generation has moved on, and connects, polls and runs. Every one of
    those paths used to rewrite the committed conftests; now only the
    git-ignored plugin follows the generation, and the committed loader
    is never even touched (its mtime is checked, because a rewrite with
    identical bytes leaves git clean and would hide the defect).

    Kills: counting a committed loader as stale in ``_flistStalePaths``,
    so every refresh rewrites the file the author committed.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sAuthor = _fsPublishAuthorProject(sRoot)
    _fnConnect(_fconnectionOver(sAuthor), sAuthor)
    for sStep in LIST_STEP_DIRECTORIES:
        assert _fsReadText(_fsConftestPath(sAuthor, sStep)) == (
            conftestManager.S_CONFTEST_LOADER_SOURCE
        )
    _fsRunGit(sAuthor, "add", "-A")
    _fsRunGit(sAuthor, "commit", "-q", "-m", "Adopt the conftest loader")
    assert "pytestPlugin" not in _fsRunGit(sAuthor, "ls-files")
    sReader = _fsCloneForReader(sRoot, sAuthor)
    listLoaders = [_fsConftestPath(sReader, s) for s in LIST_STEP_DIRECTORIES]
    _fnAgeFiles(listLoaders)
    dictBefore = _fdictFileStamps(listLoaders)
    monkeypatch.setattr(
        conftestManager, "S_CONFTEST_VERSION", S_BUMPED_GENERATION,
    )
    _fnConnectPollAndRun(_fconnectionOver(sReader), sReader)
    assert _flistGitStatus(sReader) == []
    assert _fdictFileStamps(listLoaders) == dictBefore
    sPlugin = conftestManager.flistMarkerPluginPaths(sReader)[1]
    assert _fsReadText(sPlugin).startswith(
        conftestManager.S_CONFTEST_VERSION_PREFIX + S_BUMPED_GENERATION + "\n"
    )


@pytest.mark.falsification
def test_an_unmigrated_published_project_is_migrated_exactly_once(
    tmp_path, monkeypatch,
):
    """A clone of an old project is rewritten once, to the loader, then never.

    The one final rewrite: each committed whole-plugin conftest becomes
    the loader, so the reader sees exactly those files modified and
    nothing untracked. A later generation must not touch them again.

    Kills: migrating a stale conftest to the whole plugin source instead
    of the loader, which makes the next generation bump rewrite it again.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sReader = _fsCloneForReader(sRoot, _fsPublishAuthorProject(sRoot))
    connection = _fconnectionOver(sReader)
    _fnConnect(connection, sReader)
    listExpected = sorted(
        " M " + s + "/tests/conftest.py" for s in LIST_STEP_DIRECTORIES
    )
    assert sorted(_flistGitStatus(sReader)) == listExpected
    listLoaders = [_fsConftestPath(sReader, s) for s in LIST_STEP_DIRECTORIES]
    for sPath in listLoaders:
        assert _fsReadText(sPath) == conftestManager.S_CONFTEST_LOADER_SOURCE
    _fnAgeFiles(listLoaders)
    dictBefore = _fdictFileStamps(listLoaders)
    monkeypatch.setattr(
        conftestManager, "S_CONFTEST_VERSION", S_BUMPED_GENERATION,
    )
    conftestManager.fnClearRefreshCaches()
    _fnConnectPollAndRun(connection, sReader)
    assert _fdictFileStamps(listLoaders) == dictBefore
    assert sorted(_flistGitStatus(sReader)) == listExpected


@pytest.mark.falsification
def test_the_plugin_directory_is_invisible_to_git(tmp_path):
    """The plugin and its ignore file never appear in status or a commit.

    Kills: dropping the ``*`` rule from the plugin directory's own
    ``.gitignore``, which leaves the plugin untracked in every clone.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sAuthor = _fsPublishAuthorProject(sRoot)
    _fsRunGit(sAuthor, "rm", "-q", "-r", "--cached", ".")
    _fnLayDownProject(sAuthor, conftestManager.S_CONFTEST_LOADER_SOURCE)
    _fsRunGit(sAuthor, "add", "-A")
    _fsRunGit(sAuthor, "commit", "-q", "-m", "Commit the loader")
    _fnConnectPollAndRun(_fconnectionOver(sAuthor), sAuthor)
    listPluginPaths = conftestManager.flistMarkerPluginPaths(sAuthor)
    for sPath in listPluginPaths:
        assert os.path.isfile(sPath), sPath
    assert _flistGitStatus(sAuthor) == []
    _fsRunGit(sAuthor, "add", "-A")
    assert _fsRunGit(sAuthor, "diff", "--cached", "--name-only") == ""
    sIgnored = _fsRunGit(
        sAuthor, "ls-files", "--others", "--ignored", "--exclude-standard",
    )
    assert sorted(sIgnored.split()) == sorted(
        os.path.relpath(sPath, sAuthor) for sPath in listPluginPaths
    )


# ---------------------------------------------------------------------------
# The loader in a real pytest session
# ---------------------------------------------------------------------------

def _ftRunPytest(sRepo, sStep, listArguments):
    dictEnvironment = dict(os.environ)
    dictEnvironment["VAIBIFY_ACTIVE_WORKFLOW_SLUG"] = S_SLUG
    dictEnvironment.pop("VAIBIFY_TEST_CATEGORY", None)
    processRun = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
        + listArguments,
        cwd=os.path.join(sRepo, sStep), env=dictEnvironment,
        capture_output=True, text=True,
    )
    return processRun.returncode, processRun.stdout + processRun.stderr


def _fsMarkerPath(sRepo, sStep):
    return os.path.join(
        sRepo, ".vaibify", "test_markers", S_SLUG, sStep + ".json")


def _fnReplaceVolatile(objValue, dictRunIds):
    """Return objValue with run ids and clock readings made comparable."""
    if isinstance(objValue, dict):
        return {
            dictRunIds.setdefault(sKey, "run" + str(len(dictRunIds)))
            if re.fullmatch(r"[0-9a-f]{32}", sKey) else sKey:
            ("volatile" if sKey in T_VOLATILE_MARKER_KEYS
             else _fnReplaceVolatile(objItem, dictRunIds))
            for sKey, objItem in objValue.items()
        }
    if isinstance(objValue, list):
        return [
            _fnReplaceVolatile(objItem, dictRunIds) for objItem in objValue
        ]
    return objValue


def _fdictComparableMarker(sRepo, sStep):
    with open(_fsMarkerPath(sRepo, sStep), encoding="utf-8") as fileMarker:
        return _fnReplaceVolatile(json.load(fileMarker), {})


def _fnRunMarkerSessions(sRepo):
    """Three sessions: a narrowed run, a full run, and a collection error."""
    sStep = LIST_STEP_DIRECTORIES[0]
    _fnWriteText(
        os.path.join(sRepo, sStep, "tests", "test_quantitative_step.py"),
        S_ONE_FAILING_TEST,
    )
    _ftRunPytest(sRepo, sStep, ["-k", "one"])
    _ftRunPytest(sRepo, sStep, [])
    _fnWriteText(
        os.path.join(sRepo, sStep, "tests", "test_qualitative_step.py"),
        S_BROKEN_TEST_FILE,
    )
    _ftRunPytest(sRepo, sStep, [])


@pytest.mark.falsification
def test_the_loader_and_plugin_write_the_markers_the_whole_conftest_did(
    tmp_path,
):
    """Same sessions, same markers: the loader changes WHERE, never WHAT.

    One project carries today's whole-plugin conftest; its twin carries
    the loader and the plugin the refresh wrote. A narrowed run, a full
    run with a failure, and a collection error leave markers that agree
    in everything but run ids and clock readings.

    Kills: exposing only the session hook from the plugin, so the
    per-test outcomes the report hook records are lost.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sWhole = os.path.join(sRoot, "whole")
    sLoaded = os.path.join(sRoot, "loaded")
    _fnLayDownProject(sWhole, conftestManager.fsBuildConftestSource(sWhole))
    _fnLayDownProject(
        sLoaded, _fsStampedFullConftest(sLoaded, S_AUTHOR_GENERATION),
    )
    _fnConnect(_fconnectionOver(sLoaded), sLoaded)
    sStep = LIST_STEP_DIRECTORIES[0]
    assert _fsReadText(_fsConftestPath(sLoaded, sStep)) == (
        conftestManager.S_CONFTEST_LOADER_SOURCE
    )
    _fnRunMarkerSessions(sWhole)
    _fnRunMarkerSessions(sLoaded)
    dictWhole = _fdictComparableMarker(sWhole, sStep)
    assert dictWhole["dictTestFiles"]["test_quantitative_step.py"][
        "dictOutcomes"], "the reference marker recorded no outcomes"
    assert _fdictComparableMarker(sLoaded, sStep) == dictWhole


@pytest.mark.falsification
def test_without_the_plugin_tests_run_on_their_own_merits_silently(tmp_path):
    """A stranger's clone has the loader and no plugin: pytest just works.

    Passing tests exit 0, a failing test exits 1, no marker is written,
    and nothing about vaibify is printed.

    Kills: dropping the loader's absent-plugin check, so a stranger's
    every session warns that a plugin they never had did not load.
    """
    sRepo = os.path.join(os.path.realpath(str(tmp_path)), "stranger")
    _fnLayDownProject(sRepo, conftestManager.S_CONFTEST_LOADER_SOURCE)
    sStep = LIST_STEP_DIRECTORIES[0]
    iExit, sOutput = _ftRunPytest(sRepo, sStep, [])
    assert iExit == 0, sOutput
    assert "2 passed" in sOutput
    assert "vaibify" not in sOutput.lower(), sOutput
    _fnWriteText(
        os.path.join(sRepo, sStep, "tests", "test_integrity_step.py"),
        S_ONE_FAILING_TEST,
    )
    iExit, sOutput = _ftRunPytest(sRepo, sStep, [])
    assert iExit == 1, sOutput
    assert "1 failed" in sOutput
    assert not os.path.exists(_fsMarkerPath(sRepo, sStep))


@pytest.mark.falsification
def test_the_loader_refuses_a_plugin_reached_through_a_symlink_out(tmp_path):
    """A plugin directory linked outside the repository is never executed.

    The decoy plugin writes a sentinel when it runs, so the assertion is
    about execution, not about a marker.

    Kills: dropping the loader's resolved-path containment check, so a
    symlinked ``.vaibify/pytestPlugin`` runs whatever it points at.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sRepo = os.path.join(sRoot, "repo")
    _fnLayDownProject(sRepo, conftestManager.S_CONFTEST_LOADER_SOURCE)
    sOutside = os.path.join(sRoot, "outside")
    sSentinel = os.path.join(sRoot, "sentinel")
    _fnWriteText(
        os.path.join(sOutside, "markerPlugin.py"),
        "open(" + repr(sSentinel) + ", 'w').close()\n",
    )
    os.symlink(sOutside, os.path.join(sRepo, ".vaibify", "pytestPlugin"))
    iExit, sOutput = _ftRunPytest(sRepo, LIST_STEP_DIRECTORIES[0], [])
    assert iExit == 0, sOutput
    assert not os.path.exists(sSentinel), "the loader ran a plugin outside"


# ---------------------------------------------------------------------------
# Every place that writes or runs tests has the plugin
# ---------------------------------------------------------------------------

def test_the_plugin_write_refuses_a_symlinked_directory_out(tmp_path):
    """The run reports a plugin it could not write; nothing lands outside."""
    sRoot = os.path.realpath(str(tmp_path))
    sRepo = os.path.join(sRoot, "repo")
    _fnLayDownProject(sRepo, conftestManager.S_CONFTEST_LOADER_SOURCE)
    sOutside = os.path.join(sRoot, "outside")
    os.makedirs(sOutside)
    os.symlink(sOutside, os.path.join(sRepo, ".vaibify", "pytestPlugin"))
    listWarnings = _flistRunPreflight(_fconnectionOver(sRepo), sRepo)
    assert listWarnings, "a plugin that could not be written went unreported"
    assert os.listdir(sOutside) == []


def test_the_container_plugin_write_lands_through_the_confined_program(
    tmp_path,
):
    """The container leg writes both files through the real confined program,
    and the same program refuses a plugin directory linked out of the repo.
    """
    from tests.confinedWriteHarness import ExecProgramDaemon
    from tests.testConfinedContainerWrite import _fconnectionOverDaemon
    sRoot = os.path.realpath(str(tmp_path))
    sRepo = os.path.join(sRoot, "repo")
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    connectionDocker = _fconnectionOverDaemon(ExecProgramDaemon(), "cid")
    assert conftestManager.fbWriteMarkerPlugin(connectionDocker, "cid", sRepo)
    sIgnore, sPlugin = conftestManager.flistMarkerPluginPaths(sRepo)
    assert _fsReadText(sPlugin) == conftestManager.fsBuildConftestSource(sRepo)
    assert _fsReadText(sIgnore).splitlines()[-1] == "*"
    sLinkedRepo = os.path.join(sRoot, "linked")
    sOutside = os.path.join(sRoot, "outside")
    os.makedirs(os.path.join(sLinkedRepo, ".vaibify"))
    os.makedirs(sOutside)
    os.symlink(sOutside, os.path.join(sLinkedRepo, ".vaibify", "pytestPlugin"))
    assert not conftestManager.fbWriteMarkerPlugin(
        connectionDocker, "cid", sLinkedRepo,
    )
    assert os.listdir(sOutside) == []


async def _fnDiscardEvent(dictEvent):
    del dictEvent


@pytest.mark.falsification
def test_a_run_writes_the_plugin_where_its_steps_execute(tmp_path):
    """The Level 3 shadow runs in a copy the workflow does not name.

    On that lane ``sProjectRepoPath`` still names the LIVE repository
    while every step runs under the shadow's root, so the plugin must be
    written where the runner resolves its steps. Driven through the
    runner's own entry point with the two roots distinct, and the copy
    carrying no plugin, as a clone or a cleaned tree would not.

    Kills: dropping the run root from the runner's call to its preflight
    collector, so the refresh writes the live repository and the copy's
    tests run without a marker plugin.
    """
    sRoot = os.path.realpath(str(tmp_path))
    sLive = os.path.join(sRoot, "live", "repo")
    sShadow = os.path.join(sRoot, "shadow", "repo")
    for sRepo in (sLive, sShadow):
        _fnLayDownProject(sRepo, conftestManager.S_CONFTEST_LOADER_SOURCE)
    sWorkflowPath = os.path.join(
        sShadow, ".vaibify", "projects", S_SLUG + ".json")
    dictWorkflow = _fdictWorkflow(sLive)
    for dictStep in dictWorkflow["listSteps"]:
        dictStep.update({
            "sStepId": dictStep["sDirectory"].lower(), "bRunEnabled": True,
            "saDataCommands": [],
        })
    iExitCode = asyncio.run(pipelineRunner.fiRunAllSteps(
        _fconnectionOver(sRoot), S_RESOURCE, dictWorkflow, sWorkflowPath,
        os.path.dirname(sWorkflowPath), _fnDiscardEvent,
    ))
    assert iExitCode == 0
    sPlugin = conftestManager.flistMarkerPluginPaths(sShadow)[1]
    assert os.path.isfile(sPlugin), "the shadow's tests ran with no plugin"


@pytest.mark.falsification
def test_the_test_generator_installs_the_loader_and_its_plugin(tmp_path):
    """A new step's tests directory gets the loader, never the whole plugin.

    Kills: writing the whole plugin source into a new step's
    ``tests/conftest.py``, which a later generation would rewrite.
    """
    sRepo = os.path.join(os.path.realpath(str(tmp_path)), "repo")
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    os.makedirs(os.path.join(sRepo, "NewStep", "tests"))
    conftestManager.fnWriteConftestMarker(
        _fconnectionOver(sRepo), S_RESOURCE, "NewStep", sRepo,
    )
    assert _fsReadText(_fsConftestPath(sRepo, "NewStep")) == (
        conftestManager.S_CONFTEST_LOADER_SOURCE
    )
    sPlugin = conftestManager.flistMarkerPluginPaths(sRepo)[1]
    assert _fsReadText(sPlugin) == conftestManager.fsBuildConftestSource(sRepo)


@pytest.mark.falsification
def test_the_marker_poll_counts_a_loader_as_an_installed_conftest(tmp_path):
    """The poll's discovery script must not call every migrated step bare.

    Kills: removing the loader clause from the discovery script's
    installed-conftest test, so every migrated step is reported missing.
    """
    from vaibify.gui.syncDispatcher import _fsBuildTestMarkerScript
    sRepo = os.path.realpath(str(tmp_path))
    _fnWriteText(
        _fsConftestPath(sRepo, "step1"),
        conftestManager.S_CONFTEST_LOADER_SOURCE,
    )
    sScript = _fsBuildTestMarkerScript(json.dumps(["step1"]), sRepo, S_SLUG)
    dictResult = json.loads(subprocess.check_output(
        [sys.executable, "-c", sScript], text=True,
    ))
    assert dictResult["missingConftest"] == []
