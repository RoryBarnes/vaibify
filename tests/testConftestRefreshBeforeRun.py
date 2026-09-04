"""The conftest a run executes must be current, and must never fail a suite.

Two independent defects met on a researcher's host on 2026-09-04 and
produced one symptom: every step, and every test tier within it,
reported ``exit 1`` while pytest printed ``1 passed`` immediately above.

The generated conftest writes vaibify's test marker in
``pytest_sessionfinish``. Generation 5 stamped the CONTAINER's project
root into the file as a literal, so on a host project the marker write
raised -- and an exception in that hook fails a session whose tests all
passed. Bookkeeping must not be able to overturn a scientific result.

The refresh that would have replaced the stale file is cached per hub
PROCESS and runs at connect, so the pull that reinstated generation 5
was never noticed: reopening the project re-probed nothing.
"""

import json
import subprocess
import sys

import pytest

from vaibify.gui import conftestManager, pipelineRunner
from tests.testConftestManagerVersioning import _FakeDocker


_S_CONTAINER_ID = "test-cid"
_S_PROJECT_REPO = "/workspace/myrepo"
_S_STEP_DIRECTORY = "StepOne"
_S_CONFTEST_PATH = (
    _S_PROJECT_REPO + "/" + _S_STEP_DIRECTORY + "/tests/conftest.py"
)
_S_STALE_VERSION = "5"


class _RefusingDocker(_FakeDocker):
    """A fake whose batched conftest write fails, as a read-only root would."""

    def _tHandleBatchWrite(self, sCommand):
        return (1, "cannot create directory: Read-only file system")


@pytest.fixture(autouse=True)
def _fnClearRefreshCachesBetweenTests():
    """Reset the per-process refresh caches so each test starts clean."""
    conftestManager.fnClearRefreshCaches()
    yield
    conftestManager.fnClearRefreshCaches()


def _fdictBuildWorkflow():
    """Return a one-step workflow carrying its project repo path."""
    return {
        "sProjectRepoPath": _S_PROJECT_REPO,
        "listSteps": [{"sDirectory": _S_STEP_DIRECTORY}],
    }


def _fnInstallConftestAtVersion(fakeDocker, sVersion):
    """Seed the fake's filesystem with a conftest stamped at sVersion."""
    sSource = conftestManager.fsBuildConftestSource(_S_PROJECT_REPO)
    sStamped = sSource.replace(
        conftestManager.S_CONFTEST_VERSION_PREFIX
        + conftestManager.S_CONFTEST_VERSION,
        conftestManager.S_CONFTEST_VERSION_PREFIX + sVersion,
        1,
    )
    fakeDocker.dictFiles[_S_CONFTEST_PATH] = sStamped.encode("utf-8")


def _fsInstalledVersion(fakeDocker):
    """Return the version stamp currently on the fake's conftest."""
    return conftestManager.fsReadInstalledConftestVersion(
        fakeDocker, _S_CONTAINER_ID, _S_CONFTEST_PATH,
    )


def _fnWriteProbeSuite(pathTests, sStampedRepo):
    """Write a generated conftest and one passing test into pathTests."""
    pathTests.mkdir(parents=True)
    (pathTests / "conftest.py").write_text(
        conftestManager.fsBuildConftestSource(sStampedRepo),
        encoding="utf-8",
    )
    (pathTests / "test_integrity_probe.py").write_text(
        "def test_passes():\n    assert True\n", encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# The marker write may never fail a session
# ---------------------------------------------------------------------------

@pytest.mark.falsification
def test_an_unwritable_marker_directory_does_not_fail_a_passing_suite(
    tmp_path,
):
    """A marker that cannot be written costs its status, not the verdict.

    Driven through a REAL pytest subprocess, because the defect lives
    in a hook only a real session invokes: asserting on the generated
    source would pass against the unguarded version too.

    The marker root is blocked by stamping the project repo onto a
    regular FILE, so ``mkdir`` raises ``NotADirectoryError`` for every
    user. A ``chmod`` would be bypassed by a root CI runner, and the
    test would then pass while exercising nothing.

    Kills: replacing the try/except around
    ``_fnWriteSessionMarker`` in the generated conftest's
    ``pytest_sessionfinish`` with a bare call, so a marker the
    hook cannot write ends the session non-zero again.
    """
    pathBlocker = tmp_path / "blocker"
    pathBlocker.write_text("not a directory\n", encoding="utf-8")
    _fnWriteProbeSuite(tmp_path / "step" / "tests", str(pathBlocker))
    processResult = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_integrity_probe.py"],
        cwd=str(tmp_path / "step"), capture_output=True, text=True,
    )
    assert "1 passed" in processResult.stdout, processResult.stdout
    assert processResult.returncode == 0, (
        "an unwritable marker directory turned a passing suite into "
        f"exit {processResult.returncode}\n{processResult.stdout}"
    )
    assert "could not write the test-result marker" in processResult.stdout


def test_the_marker_write_still_happens_when_the_directory_is_writable(
    tmp_path,
):
    """The guard must not have turned the marker write into a no-op.

    The paired half of the test above: swallowing every exception
    would also 'pass' there if the write were deleted outright.
    """
    pathRepo = tmp_path / "repo"
    (pathRepo / ".vaibify").mkdir(parents=True)
    _fnWriteProbeSuite(pathRepo / "step" / "tests", str(pathRepo))
    processResult = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_integrity_probe.py"],
        cwd=str(pathRepo / "step"), capture_output=True, text=True,
    )
    assert processResult.returncode == 0, processResult.stdout
    listMarkers = sorted(
        (pathRepo / ".vaibify" / "test_markers").rglob("*.json")
    )
    assert listMarkers, "no marker was written to a writable directory"
    dictMarker = json.loads(listMarkers[0].read_text(encoding="utf-8"))
    assert dictMarker["iExitStatus"] == 0


# ---------------------------------------------------------------------------
# A run re-probes; the connect sweep's cache must not answer for it
# ---------------------------------------------------------------------------

def test_the_connect_sweep_cache_hides_a_conftest_replaced_afterwards():
    """Pins the behaviour the run-time refresh exists to work around.

    Not a defect to fix here -- the cache is what keeps switch time
    flat on a large workflow. It is recorded so a later reader knows
    the run-time re-probe is load-bearing rather than redundant.
    """
    fakeDocker = _FakeDocker()
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID,
        [_S_STEP_DIRECTORY], _S_PROJECT_REPO,
    )
    assert _fsInstalledVersion(fakeDocker) == (
        conftestManager.S_CONFTEST_VERSION
    )
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID,
        [_S_STEP_DIRECTORY], _S_PROJECT_REPO,
    )
    assert _fsInstalledVersion(fakeDocker) == _S_STALE_VERSION


@pytest.mark.falsification
def test_the_run_refresh_replaces_a_conftest_the_cache_calls_current():
    """A run re-probes even after a cached connect sweep said 'done'.

    This is the researcher's sequence: open the project (the sweep
    runs and caches), pull the repo (generation 5 comes back), press
    Run All Steps.

    Kills: giving ``flistRefreshConftestsForRun`` the same
    ``_SET_REFRESHED_KEYS`` short-circuit ``fnEnsureConftestsCurrent``
    uses, so a cached connect sweep answers for the run too.
    """
    fakeDocker = _FakeDocker()
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID,
        [_S_STEP_DIRECTORY], _S_PROJECT_REPO,
    )
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    listWarnings = conftestManager.flistRefreshConftestsForRun(
        fakeDocker, _S_CONTAINER_ID,
        [_S_STEP_DIRECTORY], _S_PROJECT_REPO,
    )
    assert listWarnings == []
    assert _fsInstalledVersion(fakeDocker) == (
        conftestManager.S_CONFTEST_VERSION
    )


def test_a_refresh_that_cannot_write_warns_instead_of_staying_silent():
    """A swallowed log line is why this cost an afternoon to find."""
    fakeDocker = _RefusingDocker()
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    listWarnings = conftestManager.flistRefreshConftestsForRun(
        fakeDocker, _S_CONTAINER_ID,
        [_S_STEP_DIRECTORY], _S_PROJECT_REPO,
    )
    assert len(listWarnings) == 1
    assert _S_CONFTEST_PATH in listWarnings[0]
    assert "passed" in listWarnings[0]


# ---------------------------------------------------------------------------
# The run's preflight is where the refresh is wired
# ---------------------------------------------------------------------------

def test_the_run_preflight_refreshes_the_conftests():
    """The wire from the runner to the refresh carries a real value.

    A parameter accepted and dropped leaves every call site reading
    correctly, so this drives the runner's own collector rather than
    the conftest helper it calls.
    """
    fakeDocker = _FakeDocker()
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    listWarnings = pipelineRunner._flistCollectPreflightWarnings(
        fakeDocker, _S_CONTAINER_ID, _fdictBuildWorkflow(),
    )
    assert listWarnings == []
    assert _fsInstalledVersion(fakeDocker) == (
        conftestManager.S_CONFTEST_VERSION
    )


def test_the_run_preflight_reports_a_refresh_it_could_not_perform():
    """A stale conftest the hub could not replace reaches the screen."""
    fakeDocker = _RefusingDocker()
    _fnInstallConftestAtVersion(fakeDocker, _S_STALE_VERSION)
    listWarnings = pipelineRunner._flistCollectPreflightWarnings(
        fakeDocker, _S_CONTAINER_ID, _fdictBuildWorkflow(),
    )
    assert any("conftest" in sWarning for sWarning in listWarnings), (
        listWarnings
    )
