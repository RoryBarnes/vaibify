"""Host determinism travels as environment DATA, container as shell text.

The host-exec primitive can pass real environment entries, so the host
lane's determinism guarantees (SOURCE_DATE_EPOCH, the matplotlib salt
directory, the active-workflow slug) ride an overlay dict merged over
the hub's inherited environment — while the container lane keeps its
shell-text prefix byte-identical. The mode is decided by a REAL
registry file (the same one ``fbIsHostProject`` reads in production),
and the end-to-end test drives a REAL git repository, a REAL
``HostConnection``, and a REAL child process — never a stub keyed the
same way as the code under test.
"""

import asyncio
import json
import os
import subprocess

import pytest
from unittest.mock import MagicMock

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui.determinismEnvironment import (
    S_DETERMINISM_APPLIED_KEY,
    S_ENV_OVERLAY_KEY,
    S_ENV_PREFIX_KEY,
    _fnInjectDeterminismEnvPrefix,
)
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection

S_HOST_PROJECT = "hostDeterminismProject"
I_EPOCH = 1717171717


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def fixtureHostRegistry(tmp_path, monkeypatch):
    """Point the registry at a real file naming one host project."""
    sProjectRoot = str(tmp_path / "repo")
    os.makedirs(sProjectRoot, exist_ok=True)
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({
        "listProjects": [{
            "sName": S_HOST_PROJECT,
            "sMode": "host",
            "sDirectory": sProjectRoot,
        }],
    }))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )
    return sProjectRoot


def _fnBuildCallback():
    """Return an async no-op status callback."""
    async def fnCallback(dictEvent):
        pass
    return fnCallback


def _fdictBuildWorkflow(sProjectRepoPath):
    """Return a minimal workflow document for the injection seam."""
    return {
        "sProjectRepoPath": sProjectRepoPath,
        "sPath": ".vaibify/projects/hostDeterminism.json",
    }


@pytest.mark.falsification
def testAHostRunsDeterminismTravelsAsEnvironmentData(
    fixtureHostRegistry, tmp_path,
):
    """The host lane stashes an overlay, not shell text, and pins the salt.

    Kills: the host branch of the injection never firing (host runs
    silently fall back to vaibify-authored shell text prepended to the
    researcher's command, with the salt directory back on a
    world-shared ``/tmp``).
    """
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.return_value = (0, str(I_EPOCH))
    dictVariables = {}
    asyncio.run(_fnInjectDeterminismEnvPrefix(
        mockConnection, S_HOST_PROJECT,
        _fdictBuildWorkflow(fixtureHostRegistry), dictVariables,
    ))
    dictOverlay = dictVariables[S_ENV_OVERLAY_KEY]
    assert dictOverlay["SOURCE_DATE_EPOCH"] == str(I_EPOCH)
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is True
    assert dictVariables[S_ENV_PREFIX_KEY] == ""
    tWriteArgs = mockConnection.fnWriteFile.call_args.args
    assert tWriteArgs[0] == S_HOST_PROJECT
    assert tWriteArgs[1].endswith("matplotlibrc")
    assert tWriteArgs[1].startswith(str(tmp_path / "host-diagnostics"))
    assert dictOverlay["MPLCONFIGDIR"] == os.path.dirname(tWriteArgs[1])
    assert b"svg.hashsalt: " + str(I_EPOCH).encode() in tWriteArgs[2]


@pytest.mark.falsification
def testAContainerRunKeepsItsShellTextPrefix(fixtureHostRegistry):
    """The container lane is byte-identical: shell text, no overlay.

    Kills: routing the container lane through the overlay path — the
    Docker leg takes no environment argument, so its determinism
    guarantees exist ONLY as the exported shell prefix, and an overlay
    there is silently dropped text.
    """
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.return_value = (0, str(I_EPOCH))
    dictVariables = {}
    asyncio.run(_fnInjectDeterminismEnvPrefix(
        mockConnection, "cid-not-in-registry",
        _fdictBuildWorkflow("/repo"), dictVariables,
    ))
    assert (
        f"export SOURCE_DATE_EPOCH={I_EPOCH} && "
        in dictVariables[S_ENV_PREFIX_KEY]
    )
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is True
    assert S_ENV_OVERLAY_KEY not in dictVariables
    mockConnection.fnWriteFile.assert_not_called()


@pytest.mark.falsification
def testTheRunnerHandsTheOverlayToTheExecPrimitive(fixtureHostRegistry):
    """The stashed overlay reaches the exec primitive as an argument.

    Kills: the runner never passing the overlay onward — the injection
    then computes guarantees nobody delivers, and the run records
    determinism as applied while the step's process saw none of it.
    """
    from vaibify.gui.pipelineRunner import _ftRunCommandList
    dictSeen = {}
    mockConnection = MagicMock()

    def fnStreamingSideEffect(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None, dictEnvironmentOverlay=None,
    ):
        dictSeen["dictOverlay"] = dictEnvironmentOverlay
        return ExecResult(iExitCode=0, sStdout="", sStderr="")

    mockConnection.ftRunInContainerStreamedWithChunks.side_effect = (
        fnStreamingSideEffect
    )
    dictVariables = {S_ENV_OVERLAY_KEY: {"SOURCE_DATE_EPOCH": "7"}}
    iExitCode, _fCpu = asyncio.run(_ftRunCommandList(
        mockConnection, S_HOST_PROJECT, ["run thing"], "wd",
        dictVariables, _fnBuildCallback(),
    ))
    assert iExitCode == 0
    assert dictSeen["dictOverlay"] == {"SOURCE_DATE_EPOCH": "7"}


@pytest.mark.falsification
def testARealHostStepSeesSourceDateEpoch(fixtureHostRegistry, tmp_path):
    """End to end: a real host step's process observes the derived epoch.

    A real git repository supplies the epoch (the independent oracle
    is ``git log`` read directly by the test), a real
    ``HostConnection`` runs the injection's git query, the salt file
    really lands in the guarded scratch subtree, and a real child
    process prints what its environment actually contains.

    Kills: skipping the salt-file write (the overlay would still name
    a config directory or omit it, but the file the salt lives in
    never exists, so matplotlib draws unsalted ids while the run
    records determinism as applied).
    """
    sProjectRoot = fixtureHostRegistry
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "lane@example.invalid"],
        ["git", "config", "user.name", "Determinism Lane"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q",
         "--allow-empty", "-m", "seed"],
    ):
        subprocess.run(
            listCommand, cwd=sProjectRoot, check=True,
            capture_output=True,
        )
    sExpectedEpoch = subprocess.run(
        ["git", "log", "-1", "--format=%ct"], cwd=sProjectRoot,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    dictVariables = {}
    asyncio.run(_fnInjectDeterminismEnvPrefix(
        connection, S_HOST_PROJECT,
        _fdictBuildWorkflow(sProjectRoot), dictVariables,
    ))
    dictOverlay = dictVariables[S_ENV_OVERLAY_KEY]
    assert dictOverlay["SOURCE_DATE_EPOCH"] == sExpectedEpoch
    sConfigPath = os.path.join(
        dictOverlay["MPLCONFIGDIR"], "matplotlibrc",
    )
    with open(sConfigPath) as fileConfig:
        assert f"svg.hashsalt: {sExpectedEpoch}" in fileConfig.read()
    from vaibify.gui.pipelineRunner import _ftRunCommandList
    iExitCode, _fCpu = asyncio.run(_ftRunCommandList(
        connection, S_HOST_PROJECT,
        ['printenv SOURCE_DATE_EPOCH > observed.txt'],
        sProjectRoot, dictVariables, _fnBuildCallback(),
    ))
    assert iExitCode == 0
    with open(os.path.join(sProjectRoot, "observed.txt")) as fileObserved:
        assert fileObserved.read().strip() == sExpectedEpoch
