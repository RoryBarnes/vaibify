"""Every declared host store lands in the test's private home, wherever HOME points.

Source: ``tests/conftest.py`` (``T_REDIRECTED_STATE_STORES`` and
``fnIsolateVaibifyStateDirectories``) and the stores it names.

The isolation fixture exists because a store left out of it writes
into the researcher's real ``~/.vaibify``; over a thousand Zenodo
migration journals reached it that way. Every case here first moves
HOME to a fresh surrogate directory. On POSIX ``os.path.expanduser``
reads HOME, so a store that is NOT redirected resolves into the
surrogate rather than the real home: these checks can fail without
writing anywhere but a temporary directory.
"""

import ast
import os
import pathlib
import uuid

import pytest

from tests.conftest import T_REDIRECTED_STATE_STORES, fsRedirectedStorePath


def _fsStoreIdentity(tStoreRedirect):
    return f"{tStoreRedirect.sModule.rsplit('.', 1)[1]}.{tStoreRedirect.sAttribute}"


@pytest.fixture
def sSurrogateHome(tmp_path, monkeypatch):
    """A fresh HOME that an unredirected store would resolve into."""
    sHome = str(tmp_path / "surrogateHome")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    return sHome


def _fnAssertSurrogateUntouched(sSurrogateHome):
    sLeaked = os.path.join(sSurrogateHome, ".vaibify")
    listLeaked = [
        os.path.join(sDirectory, sName)
        for sDirectory, _, listNames in os.walk(sLeaked) for sName in listNames
    ]
    assert not os.path.exists(sLeaked), (
        f"a store resolved through HOME instead of the test home: {listLeaked}"
    )


@pytest.mark.parametrize(
    "tStoreRedirect", T_REDIRECTED_STATE_STORES, ids=_fsStoreIdentity,
)
def testEveryDeclaredStoreResolvesIntoTheTestHome(
    tStoreRedirect, sSurrogateHome, fnIsolateVaibifyStateDirectories,
):
    import importlib
    moduleStore = importlib.import_module(tStoreRedirect.sModule)
    objectLocation = getattr(moduleStore, tStoreRedirect.sAttribute)
    sResolved = objectLocation() if callable(objectLocation) else objectLocation
    assert sResolved == fsRedirectedStorePath(
        fnIsolateVaibifyStateDirectories, tStoreRedirect,
    )
    assert not sResolved.startswith(sSurrogateHome)


def _fsWriteMigrationJournal(sIdentity):
    from vaibify.config import zenodoMigrationJournal
    zenodoMigrationJournal.fnRecordSlotState(
        sIdentity, "zenodo_token", zenodoMigrationJournal.S_STATE_ABSENT,
    )
    return f"zenodo-{sIdentity}.json"


def _fsWriteBinding(sIdentity):
    from vaibify.config import remoteBindings
    remoteBindings.fnConfirmBinding(
        sIdentity, "projects/study.json::study", "study", "overleaf", "",
        {"sProjectId": "overleafProjectSynthetic"}, "researcher",
    )
    return f"{sIdentity}.json"


def _fsWriteEgressScript(sIdentity):
    from vaibify.docker import egressProxy, projectEgress
    projectEgress._fsWriteProxyScript(sIdentity)
    return os.path.join(sIdentity, egressProxy.S_PROXY_SCRIPT_BASENAME)


def _fsWriteX11Directory(sIdentity):
    from vaibify.docker import x11Authorizations
    sContainerName = "container" + sIdentity[:12]
    x11Authorizations.fsAuthorizationDirectory(sContainerName)
    return sContainerName


def _fsWriteOriginRecord(sIdentity):
    from vaibify.config import imageOrigins
    sProjectName = "project" + sIdentity[:12]
    imageOrigins.fnWriteOriginRecord(sProjectName, {"sVersionDoi": ""})
    return os.path.basename(imageOrigins.fsOriginRecordPathFor(sProjectName))


# Each store's real write API, keyed by the store's identity in the tuple.
DICT_STORE_WRITERS = {
    "zenodoMigrationJournal.fsMigrationsDirectory": _fsWriteMigrationJournal,
    "remoteBindings.fsBindingsDirectory": _fsWriteBinding,
    "projectEgress._fsEgressRoot": _fsWriteEgressScript,
    "x11Authorizations.fsX11Root": _fsWriteX11Directory,
    "imageOrigins.fsOriginsDirectory": _fsWriteOriginRecord,
}


def testEveryWriterNamesADeclaredStore():
    setDeclared = {_fsStoreIdentity(tStore) for tStore in T_REDIRECTED_STATE_STORES}
    assert set(DICT_STORE_WRITERS) <= setDeclared


@pytest.mark.falsification
@pytest.mark.parametrize("sStoreIdentity", sorted(DICT_STORE_WRITERS))
def testAStoreWriteLandsInTheTestHome(
    sStoreIdentity, sSurrogateHome, fnIsolateVaibifyStateDirectories,
):
    """A store's real write lands under the test home and nowhere under HOME.

    Kills: deleting the migrations entry from T_REDIRECTED_STATE_STORES,
    which sends the journal back through ``os.path.expanduser``.
    """
    sRelative = DICT_STORE_WRITERS[sStoreIdentity](uuid.uuid4().hex)
    _fnAssertSurrogateUntouched(sSurrogateHome)
    listWritten = [
        sDirectory for sDirectory, listDirectories, listNames
        in os.walk(fnIsolateVaibifyStateDirectories)
        if os.path.exists(os.path.join(sDirectory, sRelative))
    ]
    assert listWritten, f"{sRelative} was not written under the test home"


def testTheSentinelSeesACreatedADeletedAndAModifiedFile(tmp_path):
    from tests.conftest import fdictSnapshotStateDirectory, flistDescribeStateChanges
    (tmp_path / "kept.json").write_text("{}")
    (tmp_path / "gone.json").write_text("{}")
    (tmp_path / "browserLaneTmp").mkdir()
    dictBefore = fdictSnapshotStateDirectory(str(tmp_path))
    (tmp_path / "gone.json").unlink()
    (tmp_path / "kept.json").write_text('{"changed": true}')
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "zenodo-new.json").write_text("{}")
    (tmp_path / "browserLaneTmp" / "scratch.txt").write_text("unwatched")
    assert flistDescribeStateChanges(
        dictBefore, fdictSnapshotStateDirectory(str(tmp_path)),
    ) == [
        "created " + os.path.join("migrations", "zenodo-new.json"),
        "deleted gone.json",
        "modified kept.json",
    ]


# Stores whose lock path is a constant of its own beside the file's.
T_FILES_WITH_A_SEPARATE_LOCK_CONSTANT = ("_S_REGISTRY_PATH", "_S_PREFERENCES_PATH")


def _fbRedirectsAFileWithASeparateLock(nodeFunction):
    return any(
        isinstance(nodeCall, ast.Call) and len(nodeCall.args) >= 2
        and isinstance(nodeCall.args[1], ast.Constant)
        and nodeCall.args[1].value in T_FILES_WITH_A_SEPARATE_LOCK_CONSTANT
        for nodeCall in ast.walk(nodeFunction)
    )


def _fbNamesTheLockPath(nodeFunction):
    return any(
        isinstance(nodeConstant, ast.Constant)
        and nodeConstant.value == "_S_LOCK_PATH"
        for nodeConstant in ast.walk(nodeFunction)
    )


def flistFunctionsRedirectingAFileButNotItsLock(sSource):
    return [
        nodeFunction.name for nodeFunction in ast.walk(ast.parse(sSource))
        if isinstance(nodeFunction, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _fbRedirectsAFileWithASeparateLock(nodeFunction)
        and not _fbNamesTheLockPath(nodeFunction)
    ]


def testEveryRegistryRedirectAlsoRedirectsItsLock():
    """A test that moves a store's file moves the store's lock with it.

    ``registryManager`` and ``preferencesStore`` keep the lock path in a
    constant of its own, so a redirect of the file alone still opens and
    flocks the lock in the real ``~/.vaibify``: the test then waits on,
    and delays, the researcher's own hub, and on a CI runner it creates
    the file the session sentinel fails on.
    """
    pathTests = pathlib.Path(__file__).resolve().parent
    listUnlocked = [
        f"{pathSource.relative_to(pathTests)}::{sFunction}"
        for pathSource in sorted(pathTests.rglob("*.py"))
        for sFunction in flistFunctionsRedirectingAFileButNotItsLock(
            pathSource.read_text(encoding="utf-8"))
    ]
    assert listUnlocked == [], (
        "these redirect a store's file but not its _S_LOCK_PATH: "
        f"{listUnlocked}"
    )


def testTheLockScanFlagsAFileRedirectWithoutItsLock():
    sSource = (
        "def fixtureIsolate(monkeypatch, sDirectory):\n"
        "    monkeypatch.setattr(registryManager, '_S_REGISTRY_PATH', sDirectory)\n"
        "def fixtureComplete(monkeypatch, sDirectory):\n"
        "    monkeypatch.setattr(registryManager, '_S_REGISTRY_PATH', sDirectory)\n"
        "    monkeypatch.setattr(registryManager, '_S_LOCK_PATH', sDirectory)\n"
    )
    assert flistFunctionsRedirectingAFileButNotItsLock(sSource) == [
        "fixtureIsolate",
    ]
