"""The acquisition chain, in parity with the chain ``reproduce.sh`` runs.

Two lanes obtain the pinned image: the shell the project publishes and
the Python vaibify runs on a stranger's behalf. They must choose the
same image from the same envelope, refuse the same tampered deposit
before any load, and request the same platform. The shell lane is
driven exactly as its own tests drive it -- stubs on PATH, real jq,
real sha256sum -- and the Python lane through a fake image store whose
answers are the SDK's shapes, fed the same tarball bytes over a real
loopback HTTP server.

The three platform facts are asserted apart: an obtained platform that
differs from the required one refuses whatever the emulation flag
says, and a daemon of another architecture refuses without the flag
and is recorded with it.
"""

import gzip
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import threading
from functools import partial

import pytest

from tests.testReproduceScriptGenerator import (
    _S_CURL_STUB,
    _fnWriteStub,
)
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import imageDeposit
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
    S_OBTAINED_ARCHIVE,
    S_OBTAINED_LOCAL,
    S_OBTAINED_REGISTRY,
    fdictAcquirePinnedImage,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    fsRenderReproduceScript,
)


S_PINNED_REFERENCE = "registry.example/project@sha256:" + "a" * 64
S_LOADED_IMAGE_ID = "sha256:" + "e" * 64
S_ARCHITECTURE = "amd64"
S_REQUIRED_PLATFORM = "linux/amd64"
S_TARBALL_NAME = "environment-image.tar.gz"
S_DOI = "10.5281/zenodo.7000001"

_skipWithoutTooling = pytest.mark.skipif(
    any(shutil.which(sTool) is None for sTool in ("bash", "jq")),
    reason="bash and jq are required to drive the rendered script",
)


# ---------------------------------------------------------------------
# A fake image store answering in the SDK's shapes
# ---------------------------------------------------------------------


class _NotFound(Exception):
    """Stands in for ``docker.errors.NotFound``."""


class _FakeImage:
    def __init__(self, sId, sArchitecture):
        self.id = sId
        self.attrs = {"Os": "linux", "Architecture": sArchitecture}


class _FakeImages:
    def __init__(self, store):
        self._store = store

    def pull(self, sRepository, platform=None, **kwargs):
        self._store.listPulls.append((sRepository, platform))
        if not self._store.bRegistryServes:
            raise _NotFound("manifest unknown")
        imagePulled = _FakeImage("sha256:" + "1" * 64, self._store.sImageArchitecture)
        self._store.dictHeld[sRepository] = imagePulled
        return imagePulled

    def load(self, fileStream):
        baData = fileStream.read()
        self._store.listLoaded.append(baData)
        if not self._store.bLoadable:
            raise RuntimeError("daemon refused the tarball")
        imageLoaded = _FakeImage(S_LOADED_IMAGE_ID, self._store.sImageArchitecture)
        self._store.dictHeld[S_LOADED_IMAGE_ID] = imageLoaded
        return [imageLoaded]

    def get(self, sReference):
        try:
            return self._store.dictHeld[sReference]
        except KeyError as error:
            raise _NotFound(sReference) from error


class FakeImageStore:
    """What the acquisition asks of the daemon, and nothing more."""

    def __init__(
        self, bRegistryServes=False, bLoadable=True, bLocalCopy=False,
        sImageArchitecture=S_ARCHITECTURE, sDaemonArchitecture=S_ARCHITECTURE,
    ):
        self.bRegistryServes = bRegistryServes
        self.bLoadable = bLoadable
        self.sImageArchitecture = sImageArchitecture
        self.sDaemonArchitecture = sDaemonArchitecture
        self.dictHeld = {}
        self.listPulls = []
        self.listLoaded = []
        self.images = _FakeImages(self)
        if bLocalCopy:
            self.dictHeld[S_PINNED_REFERENCE] = _FakeImage(
                "sha256:" + "2" * 64, sImageArchitecture,
            )

    def version(self):
        return {"Arch": self.sDaemonArchitecture}


# ---------------------------------------------------------------------
# A loopback "Zenodo": the DOI resolves to a record, files beneath it
# ---------------------------------------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, sFormat, *args):
        return


class LoopbackDeposit:
    """Serve ``<doi>/files/<name>`` from a directory over loopback."""

    def __init__(self, pathRoot):
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(_QuietHandler, directory=str(pathRoot)),
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()

    @property
    def sResolver(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/"


def _fdictEnvelope(sTarballSha256, iTarballBytes, sArchitecture=S_ARCHITECTURE):
    return {
        "dictContainer": {
            "sImageDigest": S_PINNED_REFERENCE,
            "sArchitecture": sArchitecture,
            "dictImageArchive": {
                "sVersionDoi": S_DOI,
                "sConceptDoi": "10.5281/zenodo.7000000",
                "sTarballName": S_TARBALL_NAME,
                "sTarballSha256": sTarballSha256,
                "iTarballBytes": iTarballBytes,
                "sProvenance": "original",
                "sImageDigest": S_PINNED_REFERENCE,
                "sArchitecture": sArchitecture,
            },
        },
    }


@pytest.fixture
def tServedDeposit(tmp_path, monkeypatch):
    """Serve one gzip tarball as the deposit; return (envelope, bytes)."""
    baTarball = gzip.compress(b"not an image, but bytes with a hash")
    pathFiles = tmp_path / "zenodo" / S_DOI / "files"
    pathFiles.mkdir(parents=True)
    (pathFiles / S_TARBALL_NAME).write_bytes(baTarball)
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: str(tmp_path / "scratch" / os.urandom(4).hex()),
    )
    (tmp_path / "scratch").mkdir()
    return (tmp_path / "zenodo", baTarball)


def _fnPointResolverAt(monkeypatch, server):
    monkeypatch.setattr(imageAcquisition, "_S_DOI_RESOLVER", server.sResolver)


def _flistScratchLeftovers(tmp_path):
    return [
        sName for sName in os.listdir(tmp_path / "scratch")
        if os.listdir(tmp_path / "scratch" / sName)
    ]


def _fnMakeScratchDirectories(tmp_path):
    """The scratch resolver above returns a fresh path; create it lazily."""
    fReal = imageDeposit.fsResolveDepositScratchDirectory

    def fsCreate():
        sPath = fReal()
        os.makedirs(sPath, exist_ok=True)
        return sPath
    imageDeposit.fsResolveDepositScratchDirectory = fsCreate


# ---------------------------------------------------------------------
# The chain, link by link
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_registry_serves_first_and_the_deposit_is_never_fetched(
    tServedDeposit, tmp_path, monkeypatch,
):
    """Kills: trying the archived deposit before the registry."""
    pathRoot, baTarball = tServedDeposit
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=True)
    with LoopbackDeposit(pathRoot) as server:
        _fnPointResolverAt(monkeypatch, server)
        dictAcquired = fdictAcquirePinnedImage(
            _fdictEnvelope("sha256:" + hashlib.sha256(baTarball).hexdigest(),
                           len(baTarball)),
            S_REQUIRED_PLATFORM, dockerDisposable=store,
        )
    assert dictAcquired["sObtainedFrom"] == S_OBTAINED_REGISTRY
    assert dictAcquired["sImageReference"] == S_PINNED_REFERENCE
    assert store.listPulls == [(S_PINNED_REFERENCE, S_REQUIRED_PLATFORM)]
    assert store.listLoaded == []
    assert [dictAttempt["sLink"] for dictAttempt in dictAcquired["listAttempts"]] == [
        "registry pull",
    ]


@pytest.mark.falsification
def test_the_pull_requests_the_required_platform(tmp_path, monkeypatch):
    """Kills: pulling without the platform."""
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=True)
    fdictAcquirePinnedImage(
        _fdictEnvelope("sha256:" + "0" * 64, 10), S_REQUIRED_PLATFORM,
        dockerDisposable=store,
    )
    assert store.listPulls[0][1] == S_REQUIRED_PLATFORM


@pytest.mark.falsification
def test_a_verified_deposit_is_loaded_and_the_loaded_id_is_what_runs(
    tServedDeposit, tmp_path, monkeypatch,
):
    """Kills: running the registry reference after the deposit loaded."""
    pathRoot, baTarball = tServedDeposit
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=False)
    listEvents = []
    with LoopbackDeposit(pathRoot) as server:
        _fnPointResolverAt(monkeypatch, server)
        dictAcquired = fdictAcquirePinnedImage(
            _fdictEnvelope("sha256:" + hashlib.sha256(baTarball).hexdigest(),
                           len(baTarball)),
            S_REQUIRED_PLATFORM, fnStatusCallback=listEvents.append,
            dockerDisposable=store,
        )
    assert dictAcquired["sObtainedFrom"] == S_OBTAINED_ARCHIVE
    assert dictAcquired["sImageReference"] == S_LOADED_IMAGE_ID
    assert store.listLoaded == [gzip.decompress(baTarball)]
    assert [dictAttempt["bSucceeded"] for dictAttempt in dictAcquired["listAttempts"]] == [
        False, True,
    ]
    assert {dictEvent["sPhase"] for dictEvent in listEvents} >= {
        "attempt", "pulling", "downloading", "loading", "acquired",
    }
    assert _flistScratchLeftovers(tmp_path) == []


@pytest.mark.falsification
def test_a_tampered_deposit_never_reaches_the_daemon(
    tServedDeposit, tmp_path, monkeypatch,
):
    """Kills: loading before the hash is checked."""
    pathRoot, baTarball = tServedDeposit
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=False)
    with LoopbackDeposit(pathRoot) as server:
        _fnPointResolverAt(monkeypatch, server)
        with pytest.raises(ImageAcquisitionRefusedError) as excinfo:
            fdictAcquirePinnedImage(
                _fdictEnvelope("sha256:" + "0" * 64, len(baTarball)),
                S_REQUIRED_PLATFORM, dockerDisposable=store,
            )
    assert store.listLoaded == []
    assert "did not match its hash" in str(excinfo.value)
    assert "no copy" in str(excinfo.value)
    assert _flistScratchLeftovers(tmp_path) == []


def test_a_local_copy_is_the_last_link_and_says_it_is_author_only(
    tmp_path, monkeypatch,
):
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=False, bLocalCopy=True)
    dictEnvironment = {"dictContainer": {
        "sImageDigest": S_PINNED_REFERENCE, "sArchitecture": S_ARCHITECTURE,
    }}
    dictAcquired = fdictAcquirePinnedImage(
        dictEnvironment, S_REQUIRED_PLATFORM, dockerDisposable=store,
    )
    assert dictAcquired["sObtainedFrom"] == S_OBTAINED_LOCAL
    listDetails = [dictAttempt["sDetail"] for dictAttempt in dictAcquired["listAttempts"]]
    assert "no deposit on record" in listDetails[1]
    assert "only the author" in listDetails[2]


def test_every_link_failing_names_every_link(tmp_path, monkeypatch):
    _fnMakeScratchDirectories(tmp_path)
    store = FakeImageStore(bRegistryServes=False)
    with pytest.raises(ImageAcquisitionRefusedError) as excinfo:
        fdictAcquirePinnedImage(
            {"dictContainer": {"sImageDigest": S_PINNED_REFERENCE,
                               "sArchitecture": S_ARCHITECTURE}},
            S_REQUIRED_PLATFORM, dockerDisposable=store,
        )
    sMessage = str(excinfo.value)
    for sLink in ("registry pull", "archived deposit", "copy on this daemon"):
        assert sLink in sMessage


# ---------------------------------------------------------------------
# Three platform facts, kept apart
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_an_obtained_platform_that_differs_refuses_whatever_the_flag_says(
    tmp_path,
):
    """Kills: comparing the obtained platform against the daemon's."""
    _fnMakeScratchDirectories(tmp_path)
    for bAllowEmulation in (False, True):
        store = FakeImageStore(
            bRegistryServes=True, sImageArchitecture="arm64",
            sDaemonArchitecture="arm64",
        )
        with pytest.raises(ImageAcquisitionRefusedError) as excinfo:
            fdictAcquirePinnedImage(
                {"dictContainer": {"sImageDigest": S_PINNED_REFERENCE,
                                   "sArchitecture": "amd64"}},
                "linux/amd64", bAllowEmulation=bAllowEmulation,
                dockerDisposable=store,
            )
        assert "wrong bytes" in str(excinfo.value)


@pytest.mark.falsification
def test_a_daemon_of_another_architecture_is_emulation(tmp_path):
    """Refused without the flag, recorded with it.

    Kills: deriving the daemon architecture from the obtained image.
    """
    _fnMakeScratchDirectories(tmp_path)
    dictEnvironment = {"dictContainer": {
        "sImageDigest": S_PINNED_REFERENCE, "sArchitecture": "amd64",
    }}
    store = FakeImageStore(
        bRegistryServes=True, sImageArchitecture="amd64",
        sDaemonArchitecture="arm64",
    )
    with pytest.raises(ImageAcquisitionRefusedError) as excinfo:
        fdictAcquirePinnedImage(
            dictEnvironment, "linux/amd64", dockerDisposable=store,
        )
    assert "--allow-emulation" in str(excinfo.value)
    dictAcquired = fdictAcquirePinnedImage(
        dictEnvironment, "linux/amd64", bAllowEmulation=True,
        dockerDisposable=store,
    )
    assert dictAcquired["bEmulated"] is True
    assert dictAcquired["sDaemonArchitecture"] == "arm64"
    assert dictAcquired["sObtainedPlatform"] == "linux/amd64"


def test_an_empty_required_platform_is_refused_not_defaulted(tmp_path):
    store = FakeImageStore(bRegistryServes=True)
    with pytest.raises(ImageAcquisitionRefusedError):
        fdictAcquirePinnedImage(
            {"dictContainer": {"sImageDigest": S_PINNED_REFERENCE}}, "",
            dockerDisposable=store,
        )
    assert store.listPulls == []


# ---------------------------------------------------------------------
# Parity with the published script
# ---------------------------------------------------------------------


_S_DOCKER_STUB_REGISTRY_SERVES = """\
case "$1" in
    pull) printf '%s\\n' "$@" > "$VAIBIFY_TEST_RECORD_DIR/pull.argv"; exit 0 ;;
    image) exit 0 ;;
    run)  printf '%s\\n' "$@" > "$VAIBIFY_TEST_RECORD_DIR/run.argv"
          cat > /dev/null; exit 0 ;;
esac
echo "unexpected docker invocation: $*" >&2
exit 97
"""

_S_DOCKER_STUB_REGISTRY_FAILS = """\
case "$1" in
    pull) printf '%s\\n' "$@" > "$VAIBIFY_TEST_RECORD_DIR/pull.argv"
          echo "Error response from daemon: manifest unknown" >&2; exit 1 ;;
    load) cat > "$VAIBIFY_TEST_RECORD_DIR/loaded.bin"
          echo "Loaded image ID: $VAIBIFY_TEST_LOADED_ID"; exit 0 ;;
    image) exit 1 ;;
    run)  printf '%s\\n' "$@" > "$VAIBIFY_TEST_RECORD_DIR/run.argv"
          cat > /dev/null; exit 0 ;;
esac
echo "unexpected docker invocation: $*" >&2
exit 97
"""


def _fdictDriveTheScript(tmp_path, dictEnvironment, baTarball, sDockerStub):
    """Run the rendered script as its own tests do: stubs on PATH."""
    pathRepo = tmp_path / "scriptClone"
    (pathRepo / ".vaibify").mkdir(parents=True)
    (pathRepo / ".vaibify" / "environment.json").write_text(
        json.dumps(dictEnvironment), encoding="utf-8",
    )
    pathStubs = tmp_path / "stubs"
    pathStubs.mkdir(exist_ok=True)
    pathRecord = tmp_path / "record"
    pathRecord.mkdir(exist_ok=True)
    pathServed = tmp_path / "served.tar.gz"
    pathServed.write_bytes(baTarball)
    _fnWriteStub(pathStubs, "docker", sDockerStub)
    _fnWriteStub(pathStubs, "curl", _S_CURL_STUB)
    if shutil.which("sha256sum") is None:
        _fnWriteStub(pathStubs, "sha256sum", 'exec shasum -a 256 "$@"\n')
    pathScript = tmp_path / "reproduce.sh"
    pathScript.write_text(
        fsRenderReproduceScript({"listSteps": []}), encoding="utf-8",
    )
    dictEnvironmentVariables = dict(os.environ)
    dictEnvironmentVariables["PATH"] = (
        str(pathStubs) + os.pathsep + dictEnvironmentVariables.get("PATH", "")
    )
    dictEnvironmentVariables["VAIBIFY_TEST_RECORD_DIR"] = str(pathRecord)
    dictEnvironmentVariables["VAIBIFY_TEST_TARBALL"] = str(pathServed)
    dictEnvironmentVariables["VAIBIFY_TEST_LOADED_ID"] = S_LOADED_IMAGE_ID
    tResult = subprocess.run(
        ["bash", str(pathScript)], cwd=str(pathRepo),
        env=dictEnvironmentVariables, capture_output=True, text=True,
        timeout=120,
    )
    listRunArgv = (
        (pathRecord / "run.argv").read_text(encoding="utf-8").splitlines()
        if (pathRecord / "run.argv").exists() else []
    )
    listPullArgv = (
        (pathRecord / "pull.argv").read_text(encoding="utf-8").splitlines()
        if (pathRecord / "pull.argv").exists() else []
    )
    return {
        "iExit": tResult.returncode, "listRunArgv": listRunArgv,
        "listPullArgv": listPullArgv,
        "bLoaded": (pathRecord / "loaded.bin").exists(),
    }


@_skipWithoutTooling
@pytest.mark.parametrize("sScenario", [
    "registry-serves", "deposit-serves", "deposit-tampered",
])
@pytest.mark.falsification
def test_both_lanes_walk_the_same_chain(
    sScenario, tServedDeposit, tmp_path, monkeypatch,
):
    """The Python chain and the published script agree, link for link.

    Same envelope, same tarball bytes, same required platform: both
    choose the same image reference, both refuse the same tampered
    deposit before any load, and both request the same platform.

    Kills: loading the deposit before the hash check in the Python lane
    (the tampered scenario then loads where the script refuses).
    """
    pathRoot, baTarball = tServedDeposit
    _fnMakeScratchDirectories(tmp_path)
    sGoodSha = "sha256:" + hashlib.sha256(baTarball).hexdigest()
    sSha = "sha256:" + "0" * 64 if sScenario == "deposit-tampered" else sGoodSha
    dictEnvironment = _fdictEnvelope(sSha, len(baTarball))
    bRegistryServes = sScenario == "registry-serves"
    dictScript = _fdictDriveTheScript(
        tmp_path, dictEnvironment, baTarball,
        _S_DOCKER_STUB_REGISTRY_SERVES if bRegistryServes
        else _S_DOCKER_STUB_REGISTRY_FAILS,
    )
    store = FakeImageStore(bRegistryServes=bRegistryServes)
    with LoopbackDeposit(pathRoot) as server:
        _fnPointResolverAt(monkeypatch, server)
        try:
            dictPython = fdictAcquirePinnedImage(
                dictEnvironment, S_REQUIRED_PLATFORM, dockerDisposable=store,
            )
        except ImageAcquisitionRefusedError:
            dictPython = None
    if sScenario == "deposit-tampered":
        assert dictScript["iExit"] != 0 and not dictScript["bLoaded"]
        assert dictPython is None and store.listLoaded == []
        return
    assert dictScript["iExit"] == 0, sScenario
    sExpected = S_PINNED_REFERENCE if bRegistryServes else S_LOADED_IMAGE_ID
    assert sExpected in dictScript["listRunArgv"]
    assert dictPython["sImageReference"] == sExpected
    assert dictScript["bLoaded"] == (store.listLoaded != [])
    assert "--platform" in dictScript["listPullArgv"]
    assert S_REQUIRED_PLATFORM in dictScript["listPullArgv"]
    assert S_REQUIRED_PLATFORM in dictScript["listRunArgv"]
    assert store.listPulls == [(S_PINNED_REFERENCE, S_REQUIRED_PLATFORM)]
