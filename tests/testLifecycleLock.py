"""The per-name lifecycle lock, and every entry point that must take it.

An environment's name keys its container, volumes, image tags, session
lock, journal and registry entry. Before the lifecycle lock, nothing
kept two operations that change what a name owns from interleaving:
CLI ``vaibify start`` created and started a container under no lock,
``vaibify destroy`` removed volumes under none, and a rename would have
had nothing to exclude them with. Every such operation now takes the
name's lifecycle lock, non-blocking, and refuses by naming the
operation in progress.

The holder in every refusal test is ANOTHER real process, because the
lock is a cross-process ``flock`` and an in-process double would prove
only that two calls agree. Each refusal test also asserts the
operation's mutation was never reached, which is the only honest
reading of a refusal.

The completeness test is an AST scan of ``vaibify/``: every call of a
lifecycle primitive sits inside a function that takes the lock, inside
a helper whose every caller takes it, or on an allow-list with its
reason.
"""

import ast
import functools
import os
import pathlib

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.lifecycleLockHolder import (
    fcontextHoldLifecycleLockInAnotherProcess,
)
from vaibify.config import containerLock, operationJournal, registryManager


S_NAME = "sample-environment"
S_HOST_NAME = "promotion sandbox"
S_PROMOTED_NAME = "Promoted Project"
S_REFUSAL = f"'{S_NAME}' is being renamed by another vaibify process"
PATH_PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "vaibify"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Redirect the registry, both lock directories and the journal."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LIFECYCLE_LOCK_DIRECTORY",
        str(tmp_path / "lifecycleLocks"),
    )
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fbDockerContainerExists",
        lambda sName: False,
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory with a vaibify.yml and register it."""
    sDirectory = str(tmp_path / sProjectName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sDirectory, sMode=sMode)
    return sDirectory


@pytest.fixture
def clientHub(tmp_path):
    """A hub app serving one container project and one host sandbox."""
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_NAME, "container")
    _fsRegisterProject(tmp_path, S_HOST_NAME, "host")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(app, {"require": lambda *a: None, "docker": None})
    return TestClient(app)


def _flistRecordCalls(monkeypatch, objectTarget, sAttribute, value=None):
    """Replace a primitive with a recorder returning ``value``."""
    listCalls = []

    def fRecord(*aArgs, **dictKwargs):
        listCalls.append(aArgs)
        return value
    monkeypatch.setattr(objectTarget, sAttribute, fRecord)
    return listCalls


# ---------------------------------------------------------------------
# The lock itself.
# ---------------------------------------------------------------------


def testAHeldLockRefusesAndNamesTheOperationInProgress():
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        with pytest.raises(containerLock.LifecycleLockedError) as error:
            containerLock.ffileTakeLifecycleLock(S_NAME, "started")
    assert str(error.value) == (
        f"{S_REFUSAL}; try again when it finishes."
    )
    fileHandle = containerLock.ffileTakeLifecycleLock(S_NAME, "started")
    containerLock.fnReleaseLifecycleLock(fileHandle)


def testALifecycleLockFileOutlivesEveryHolderAndEveryReaper():
    """Nothing unlinks a lifecycle lock file: not a release, not a reaper.

    Unlinking a file whose flock is held lets another process lock a
    new inode at the same path, so the file must stay.
    """
    sPath = containerLock.fsLifecycleLockPathFor(S_NAME)
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        containerLock.fnReapStaleContainerLocks()
        assert os.path.exists(sPath)
    assert os.path.exists(sPath)
    with containerLock.fcontextHoldLifecycleLock(S_NAME, "started"):
        pass
    containerLock.fnReapStaleContainerLocks()
    assert os.path.exists(sPath)


def testANameAndItsLifecycleSuffixedTwinNeverShareALock():
    """``example`` and ``example.lifecycle`` are both valid names.

    A lifecycle lock written as ``<name>.lifecycle.lock`` in the session
    directory would BE the session lock of ``example.lifecycle``.
    """
    sTwin = "example.lifecycle"
    assert len({
        containerLock.fsLockPathFor("example"),
        containerLock.fsLockPathFor(sTwin),
        containerLock.fsLifecycleLockPathFor("example"),
        containerLock.fsLifecycleLockPathFor(sTwin),
    }) == 4
    with fcontextHoldLifecycleLockInAnotherProcess("example", "renamed"):
        fileSession = containerLock.ffileAcquireContainerLock(sTwin, 8050)
        with containerLock.fcontextHoldLifecycleLock(sTwin, "started"):
            pass
        containerLock.fnReleaseContainerLock(fileSession)
    with fcontextHoldLifecycleLockInAnotherProcess(sTwin, "renamed"):
        with containerLock.fcontextHoldLifecycleLock("example", "started"):
            pass
    assert containerLock.flistReadAllLockHolders() == []


# ---------------------------------------------------------------------
# Every entry point refuses while another process holds the name.
# ---------------------------------------------------------------------


def _fnPrepareCliStart(monkeypatch, tmp_path):
    """Let ``vaibify start`` reach its launch with Docker replaced."""
    from types import SimpleNamespace
    from vaibify.cli import commandStart
    configProject = SimpleNamespace(sProjectName=S_NAME)
    monkeypatch.setattr(
        commandStart, "fconfigResolveProject", lambda sName: configProject)
    monkeypatch.setattr(
        commandStart, "fsResolveProjectConfigPath",
        lambda sName: str(tmp_path / "vaibify.yml"))
    monkeypatch.setattr(commandStart, "flistRunStartPreflight", lambda c: [])
    monkeypatch.setattr(
        commandStart, "fnConfirmImageTrustOrExit", lambda *aArgs: None)
    monkeypatch.setattr(commandStart, "fsDockerDir", lambda: str(tmp_path))
    monkeypatch.setattr(
        commandStart, "fnClearStoppedContainerBeforeLaunch", lambda c: None)
    from vaibify.docker import containerManager
    return _flistRecordCalls(
        monkeypatch, containerManager, "fsStartContainerDetached",
        "0123456789abcdef")


@pytest.mark.falsification
def testCliStartRefusesWhileAnotherProcessHoldsTheName(monkeypatch, tmp_path):
    """Kills: CLI start creating and starting with no lifecycle lock."""
    from vaibify.cli.commandStart import fnStartCommand
    listStarted = _fnPrepareCliStart(monkeypatch, tmp_path)
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        result = CliRunner().invoke(fnStartCommand, ["--detach"])
    assert result.exit_code == 1, result.output
    assert S_REFUSAL in result.output
    assert listStarted == []
    result = CliRunner().invoke(fnStartCommand, ["--detach"])
    assert result.exit_code == 0, result.output
    assert len(listStarted) == 1


def testCliStopRefusesWhileAnotherProcessHoldsTheName(monkeypatch):
    from types import SimpleNamespace
    from vaibify.cli import main
    from vaibify.docker import containerManager
    monkeypatch.setattr(
        main, "fconfigResolveProject",
        lambda sName: SimpleNamespace(sProjectName=S_NAME))
    listStopped = _flistRecordCalls(
        monkeypatch, containerManager, "fnStopContainer")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        result = CliRunner().invoke(main.fnStopCommand, [])
    assert result.exit_code == 1
    assert S_REFUSAL in result.output
    assert listStopped == []


def testCliDestroyRefusesWhileAnotherProcessHoldsTheName(monkeypatch):
    from types import SimpleNamespace
    from vaibify.cli import commandDestroy
    monkeypatch.setattr(commandDestroy, "fnRequireDocker", lambda: None)
    monkeypatch.setattr(
        commandDestroy, "fconfigResolveProject",
        lambda sName: SimpleNamespace(sProjectName=S_NAME))
    listRemoved = _flistRecordCalls(monkeypatch, commandDestroy, "fnRemoveVolume")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        result = CliRunner().invoke(
            commandDestroy.fnDestroyCommand, [], input="y\ny\n")
    assert result.exit_code == 1
    assert S_REFUSAL in result.output
    assert listRemoved == []


@pytest.mark.falsification
def testGuiStartRefusesWhileAnotherProcessHoldsTheName(clientHub, monkeypatch):
    """Kills: the start route's early lifecycle refusal doing nothing."""
    from vaibify.gui import startReservation
    listBegun = []

    async def ftRecordBegin(*aArgs, **dictKwargs):
        listBegun.append(aArgs)
        return 202, {}
    monkeypatch.setattr(startReservation, "ftBeginStart", ftRecordBegin)
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        response = clientHub.post(f"/api/containers/{S_NAME}/start")
    assert response.status_code == 409
    assert S_REFUSAL in response.json()["detail"]["sMessage"]
    assert listBegun == []


def testTheReservedStartItselfHoldsTheName(monkeypatch):
    """The background create-and-start refuses before anything is created."""
    from types import SimpleNamespace
    from vaibify.docker import containerManager
    from vaibify.gui import startReservation
    listCreated = _flistRecordCalls(
        monkeypatch, containerManager, "fsCreateContainerForReservation")
    reservation = SimpleNamespace(recordStartTask=SimpleNamespace(
        sJournalOperationId="unused"), sReservationId="unused")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        with pytest.raises(containerLock.LifecycleLockedError):
            startReservation._fsExecuteReservedStart(
                S_NAME, reservation, None)
    assert listCreated == []


def testGuiStopRefusesWhileAnotherProcessHoldsTheName(clientHub, monkeypatch):
    from vaibify.docker import containerManager
    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        lambda sName: {"bExists": True, "bRunning": True, "sStatus": "up"})
    listStopped = _flistRecordCalls(
        monkeypatch, containerManager, "fnStopContainer")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        response = clientHub.post(f"/api/containers/{S_NAME}/stop")
    assert response.status_code == 409
    assert S_REFUSAL in response.json()["detail"]["sMessage"]
    assert listStopped == []


@pytest.mark.falsification
def testGuiClaimRefusesWhileAnotherProcessHoldsTheName(clientHub):
    """Kills: the lifecycle probe answering "free" without asking."""
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        response = clientHub.post(f"/api/registry/{S_NAME}/claim")
    assert response.status_code == 409
    assert S_REFUSAL in response.json()["detail"]["sMessage"]
    assert containerLock.fdictReadLockHolder(S_NAME) == {}


def testDeletionRefusesWhileAnotherProcessHoldsTheName(clientHub, monkeypatch):
    from vaibify.gui import environmentDeletion
    listTouched = _flistRecordCalls(
        monkeypatch, environmentDeletion, "_fbRemoveContainer")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        response = clientHub.post(
            f"/api/registry/{S_NAME}/delete-environment",
            json={"sConfirmation": f"permanently delete {S_NAME}"})
    assert response.status_code == 409
    assert S_REFUSAL in response.json()["detail"]["sMessage"]
    assert listTouched == []
    assert registryManager.fdictGetProject(S_NAME) is not None


def testPromotionRefusesWhileAnotherProcessHoldsTheNewName(clientHub):
    sUrl = "/api/registry/" + S_HOST_NAME.replace(" ", "%20") + (
        "/promote-to-host-project")
    with fcontextHoldLifecycleLockInAnotherProcess(S_PROMOTED_NAME, "renamed"):
        response = clientHub.post(sUrl, json={"sProjectName": S_PROMOTED_NAME})
    assert response.status_code == 409
    assert "is being renamed by another vaibify process" in (
        response.json()["detail"]["sMessage"])
    assert registryManager.fdictGetProject(S_HOST_NAME) is not None
    assert registryManager.fdictGetProject(S_PROMOTED_NAME) is None


def testBuildRefusesWhileAnotherProcessHoldsTheName(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from vaibify.cli import commandBuild
    listStaged = _flistRecordCalls(
        monkeypatch, commandBuild, "fsStageBuildContext")
    with fcontextHoldLifecycleLockInAnotherProcess(S_NAME, "renamed"):
        with pytest.raises(containerLock.LifecycleLockedError, match=S_REFUSAL):
            commandBuild.fnBuildFromConfig(
                SimpleNamespace(sProjectName=S_NAME), str(tmp_path), False)
    assert listStaged == []


# ---------------------------------------------------------------------
# Completeness: every lifecycle primitive call is under the lock.
# ---------------------------------------------------------------------

# What changes what a name owns: its container, its volumes, its image
# tags, or its registry entry.
SET_LIFECYCLE_PRIMITIVES = {
    "fnStartContainer", "fsStartContainerDetached",
    "fsCreateContainerForReservation", "fnStartCreatedContainer",
    "fsRecreateContainerDetachedFromImage", "fnStopContainer",
    "fnRemoveStopped", "fnRemoveStoppedContainerById",
    "fbStopContainerProvenSettled", "fdictRepairContainerLifecycle",
    "fnDestroyVolume", "fnRemoveVolume", "fbRemoveImage", "fnTagImage",
    "fnRemoveImageTag", "fnBuildImage", "fnAddProject",
    "fnPromoteHostProject", "fnConvertProjectToContainer",
    "fsReturnProjectToHost", "fnRemoveProject",
}
SET_LOCK_TAKERS = {
    "ffileTakeLifecycleLock", "fcontextHoldLifecycleLock",
    "fcontextHoldLifecycleLocks",
}
# Takes and releases at once to ASK; holding nothing, it covers nothing.
SET_NOT_A_HOLD = {"fnRefuseWhileLifecycleLockHeld"}

# Helpers whose every caller in the same file must take the lock.
SET_HELPERS_UNDER_A_CALLER_LOCK = {
    ("cli/commandBuild.py", "_ffnImportBuildOrExit"),
    ("cli/commandDestroy.py", "flistRemoveProjectImages"),
    ("cli/commandStart.py", "_fnStartContainer"),
    ("cli/commandStart.py", "_fnStartContainerDetached"),
    ("cli/commandStart.py", "fnClearStoppedContainerBeforeLaunch"),
    ("gui/conversionLifecycle.py", "_fnWriteInOrder"),
    ("gui/conversionLifecycle.py", "_fnRecoverJournalLocked"),
    ("gui/environmentDeletion.py", "_fbRemoveContainer"),
    ("gui/environmentDeletion.py", "_flistRemoveVolumes"),
    ("gui/environmentDeletion.py", "_flistRemoveImages"),
    ("gui/environmentDeletion.py", "_fbRemoveRegistryEntry"),
    ("gui/registryRoutes.py", "_fnRegisterNewProject"),
    ("gui/startReservation.py", "_fnClearStoppedIncarnation"),
}

DICT_ALLOWED_WITHOUT_THE_LOCK = {
    ("cli/commandInit.py", "fsRegisterProject"):
        "registers a name read from vaibify.yml inside the registry "
        "write, whose lock refuses a duplicate name",
    ("cli/commandRegister.py", "fnRegisterCommand"):
        "registers a name read from vaibify.yml inside the registry "
        "write, whose lock refuses a duplicate name",
    ("gui/registryRoutes.py", "fdictAddProject"):
        "registers a name read from vaibify.yml inside the registry "
        "write, whose lock refuses a duplicate name",
    ("gui/routes/remnantRoutes.py", "_fsRemoveStoppedContainer"):
        "removes an orphaned container by its id, never a registered "
        "environment's container",
    ("gui/conversionLifecycle.py", "_fnDropStagingTagQuietly"):
        "removes only the conversion's own <name>:vaibify-staging tag, "
        "which nothing else reads or writes, under the conversion's "
        "source lock",
}


def _fsNameOf(nodeExpression):
    if isinstance(nodeExpression, ast.Attribute):
        return nodeExpression.attr
    if isinstance(nodeExpression, ast.Name):
        return nodeExpression.id
    return ""


_DICT_FUNCTIONS_BY_MODULE = {}


def _flistFunctionsOf(treeModule):
    """Return every function defined in a module, nested ones included."""
    if id(treeModule) not in _DICT_FUNCTIONS_BY_MODULE:
        _DICT_FUNCTIONS_BY_MODULE[id(treeModule)] = (treeModule, [
            node for node in ast.walk(treeModule)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ])
    return _DICT_FUNCTIONS_BY_MODULE[id(treeModule)][1]


def _fiterOwnNodes(nodeFunction):
    """Yield a function's own nodes, never those of a nested function.

    A route handler is nested inside its ``_fnRegister...`` function, and
    walking into it would credit the registrar with the handler's lock.
    """
    listPending = list(ast.iter_child_nodes(nodeFunction))
    while listPending:
        node = listPending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            continue
        yield node
        listPending.extend(ast.iter_child_nodes(node))


_DICT_NAMES_BY_FUNCTION = {}


def _fsetNamesReferencedIn(nodeFunction):
    """Return the names a function's own body references, memoized."""
    if id(nodeFunction) not in _DICT_NAMES_BY_FUNCTION:
        _DICT_NAMES_BY_FUNCTION[id(nodeFunction)] = (nodeFunction, {
            _fsNameOf(node) for node in _fiterOwnNodes(nodeFunction)
            if isinstance(node, (ast.Attribute, ast.Name))
        })
    return _DICT_NAMES_BY_FUNCTION[id(nodeFunction)][1]


def _fsetTakerNames(dictTrees):
    """Return every function name that takes the lock, transitively."""
    setTakers = set(SET_LOCK_TAKERS)
    while True:
        setFound = {
            nodeFunction.name
            for treeModule in dictTrees.values()
            for nodeFunction in _flistFunctionsOf(treeModule)
            if nodeFunction.name not in SET_NOT_A_HOLD
            and _fsetNamesReferencedIn(nodeFunction) & setTakers
        }
        if setFound <= setTakers:
            return setTakers
        setTakers |= setFound


def _flistPrimitiveReferences(dictTrees):
    """Return (file, enclosing function, primitive) for every reference.

    A reference, not only a call: a primitive handed to
    ``asyncio.to_thread`` is a call the scan would otherwise miss.
    """
    listSites = []
    for sFile, treeModule in dictTrees.items():
        for nodeFunction in _flistFunctionsOf(treeModule):
            # A primitive calling another is the gateway composing
            # itself (a stop that removes); its callers take the lock.
            if nodeFunction.name in SET_LIFECYCLE_PRIMITIVES:
                continue
            for sName in _fsetNamesReferencedIn(nodeFunction):
                if sName in SET_LIFECYCLE_PRIMITIVES:
                    listSites.append((sFile, nodeFunction, sName))
    return listSites


@functools.lru_cache(maxsize=1)
def _fdictParsePackage():
    return {
        str(pathFile.relative_to(PATH_PACKAGE)): ast.parse(
            pathFile.read_text(encoding="utf-8"))
        for pathFile in PATH_PACKAGE.rglob("*.py")
    }


def _fbHelperCallersAllTakeTheLock(sFile, sHelper, dictTrees, setTakers):
    listCallers = [
        nodeFunction for nodeFunction in _flistFunctionsOf(dictTrees[sFile])
        if nodeFunction.name != sHelper
        and sHelper in _fsetNamesReferencedIn(nodeFunction)
    ]
    return bool(listCallers) and all(
        _fsetNamesReferencedIn(nodeCaller) & setTakers
        or (sFile, nodeCaller.name) in SET_HELPERS_UNDER_A_CALLER_LOCK
        for nodeCaller in listCallers
    )


def testEveryLifecyclePrimitiveIsCalledUnderTheLock():
    dictTrees = _fdictParsePackage()
    setTakers = _fsetTakerNames(dictTrees)
    listOffenders = []
    for sFile, nodeFunction, sPrimitive in _flistPrimitiveReferences(dictTrees):
        tKey = (sFile, nodeFunction.name)
        if _fsetNamesReferencedIn(nodeFunction) & setTakers:
            continue
        if tKey in DICT_ALLOWED_WITHOUT_THE_LOCK:
            continue
        if tKey in SET_HELPERS_UNDER_A_CALLER_LOCK and \
                _fbHelperCallersAllTakeTheLock(
                    sFile, nodeFunction.name, dictTrees, setTakers):
            continue
        listOffenders.append(f"{sFile}::{nodeFunction.name} -> {sPrimitive}")
    assert listOffenders == [], (
        "these change what a name owns without its lifecycle lock; take "
        "it, or add the site to an allow-list with its reason:\n"
        + "\n".join(listOffenders)
    )


def testEveryAllowListEntryStillNamesARealSite():
    """A stale entry is a hole: it would excuse a future site by name."""
    dictTrees = _fdictParsePackage()
    setSites = {
        (sFile, nodeFunction.name)
        for sFile, nodeFunction, _ in _flistPrimitiveReferences(dictTrees)
    }
    listStale = sorted(
        (set(DICT_ALLOWED_WITHOUT_THE_LOCK)
         | SET_HELPERS_UNDER_A_CALLER_LOCK) - setSites
    )
    assert listStale == []
