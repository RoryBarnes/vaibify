"""No test resolves, or sweeps, the researcher's live secret files.

Every hub runs the ``ephemeralSecretFiles`` reaper at startup, and a
test hub's Docker double answers what it was told rather than what the
researcher's daemon mounts. A browser-lane hub swept the real
``~/.vaibify/tmp`` with a fake that reported nothing mounted, which
deletes the host file behind a live container's secret mount (shown on
2026-10-30). ``tests/conftest.py`` therefore resolves the root's home
into a private directory for the whole session.

The oracle for "the researcher's home" is the password database, never
``HOME`` or ``expanduser``, because those are exactly what the redirect
under test controls.

Every mutation these tests kill leaves a hub sweeping a REAL root, so
each double here protects every path but its own canary: under a
mutation the sweep reaches the real root and deletes nothing.
"""

import os
import pwd
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.config import ephemeralStore, secretManager
from vaibify.gui import remnantReapers, serverLifespan, uploadStaging
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import askpassHelper, overleafSync


F_SWEEP_DEADLINE_SECONDS = 10.0


class _RootCaptured(Exception):
    """Raised by the askpass spy once it has seen the directory."""


class _EveryPathMountedButOne(set):
    """A mount answer that protects every host path except one."""

    def __init__(self, sUnprotectedPath):
        super().__init__()
        self.sUnprotectedPath = sUnprotectedPath

    def __contains__(self, sPath):
        return sPath != self.sUnprotectedPath


def fsResearchersSecretRoot():
    """Return the real home's secret root, from the password database."""
    sHome = pwd.getpwuid(os.getuid()).pw_dir
    return os.path.realpath(os.path.join(sHome, ".vaibify", "tmp"))


def fbPathLiesWithin(sPath, sRoot):
    """Return True when ``sPath`` is ``sRoot`` or lies beneath it."""
    sResolved = os.path.realpath(sPath)
    return os.path.commonpath([sResolved, sRoot]) == sRoot


def fsAskpassDirectory():
    """Return the directory askpass would write into, writing nothing."""
    dictSeen = {}

    def fnCaptureDirectory(*args, **kwargs):
        dictSeen["sDirectory"] = kwargs["dir"]
        raise _RootCaptured()

    with patch.object(askpassHelper.tempfile, "mkstemp", fnCaptureDirectory):
        with pytest.raises(_RootCaptured):
            askpassHelper.fsWriteExecutableScript("", "vc_probe_")
    return dictSeen["sDirectory"]


def fdictResolveEveryEphemeralRoot():
    """Return each resolver's answer, keyed by the caller that asks it."""
    return {
        "ephemeralStore.fsGetEphemeralRoot":
            ephemeralStore.fsGetEphemeralRoot(),
        "secretManager._fsGetTempDirectory":
            secretManager._fsGetTempDirectory(),
        "uploadStaging._fsSpoolDirectory":
            uploadStaging._fsSpoolDirectory(),
        "askpassHelper.fsWriteExecutableScript": fsAskpassDirectory(),
        "overleafSync._fsEphemeralRoot": overleafSync._fsEphemeralRoot(),
    }


@pytest.mark.falsification
def testEveryEphemeralRootResolverAvoidsTheResearchersHome():
    """Kills: the session fixture's rule returning the real home.

    Five callers resolve the root, and one of them (``uploadStaging``)
    bound the resolver at import, which is why the redirect patches the
    home seam beneath it rather than the resolver itself.
    """
    sForbidden = fsResearchersSecretRoot()
    dictRoots = fdictResolveEveryEphemeralRoot()
    listInside = [
        f"{sCaller} -> {sRoot}" for sCaller, sRoot in dictRoots.items()
        if fbPathLiesWithin(sRoot, sForbidden)
    ]
    assert not listInside, (
        "These callers resolved the researcher's live secret root under "
        f"test, where a hub's sweep deletes real files: {listInside}"
    )


def _fappWithTheSecretSweep(connectionDocker):
    """Return a bare hub app running the real secret sweep on its loop."""
    app = FastAPI(lifespan=serverLifespan._fcontextLifespanShared)
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictCtx = {"docker": connectionDocker}
    syncRoutes._fnRegisterEphemeralSecretSweep(app, dictCtx)
    remnantReapers.fnRegisterReaperLoop(app, dictCtx, fInterval=3600.0)
    return app


def _fdictAwaitTheSweepRecord(app):
    """Return the sweep's first recorded outcome, or fail at the deadline."""
    fDeadline = time.monotonic() + F_SWEEP_DEADLINE_SECONDS
    while time.monotonic() < fDeadline:
        dictHealth = getattr(app.state, "dictReaperHealth", {})
        if "ephemeralSecretFiles" in dictHealth:
            return dictHealth["ephemeralSecretFiles"]
        time.sleep(0.02)
    pytest.fail("the hub's first reaper pass never recorded the sweep")


def _fsPlantBackdatedCanary(sPrivateHome):
    """Write a secret-shaped file older than the grace; return its path."""
    sRoot = os.path.join(sPrivateHome, ".vaibify", "tmp")
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    sCanary = os.path.join(
        sRoot, f"{ephemeralStore.S_SECRET_FILE_PREFIX}canary_"
        f"{uuid.uuid4().hex}.tmp")
    with open(sCanary, "w") as fileCanary:
        fileCanary.write("canary")
    fBackdated = time.time() - 2 * ephemeralStore.F_SECRET_FILE_GRACE_SECONDS
    os.utime(sCanary, (fBackdated, fBackdated))
    return sCanary


@pytest.mark.falsification
def testAHubStartedUnderTestSweepsOnlyItsIsolatedRoot(
    fnKeepSecretFilesOutOfTheResearchersHome,
):
    """Kills: the session fixture's rule returning the real home.

    The canary is planted where the redirect points, from the fixture's
    own answer rather than the resolver's, so a redirect that stopped
    working leaves it standing. The double protects every other path,
    so a sweep that reached the real root deletes nothing there.
    """
    sCanary = _fsPlantBackdatedCanary(
        fnKeepSecretFilesOutOfTheResearchersHome)
    connectionDocker = SimpleNamespace(
        fsetListMountSourcesOfAllContainers=(
            lambda: _EveryPathMountedButOne(sCanary)),
    )
    app = _fappWithTheSecretSweep(connectionDocker)
    try:
        with TestClient(app):
            dictRecord = _fdictAwaitTheSweepRecord(app)
        assert dictRecord["sOutcome"] == remnantReapers.S_OUTCOME_RAN
        assert not os.path.exists(sCanary), (
            "the hub's first reaper pass did not sweep the isolated root, "
            "so it swept some other one"
        )
    finally:
        if os.path.exists(sCanary):
            os.unlink(sCanary)


def testATestThatSetsItsOwnHomeKeepsIt(monkeypatch, tmp_path):
    """The redirect honors a test's own HOME, as the resolver tests need."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRoot = ephemeralStore.fsGetEphemeralRoot()
    assert fbPathLiesWithin(sRoot, os.path.realpath(str(tmp_path)))
