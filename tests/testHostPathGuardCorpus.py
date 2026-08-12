"""Every path a host project can be handed, on every method that takes one.

The existing guard tests prove the guard REFUSES the hostile shapes.
What they cannot prove is that every entry point CONSULTS it — and that
is the failure mode with history here: a boundary that holds everywhere
somebody remembered to call it, and a new method added six months later
that nobody did. So this file is a table, and its most important test
is the one that fails when a method is missing FROM the table.

Three things are deliberate.

**The registry is real.** These tests resolve the project root through
``registryManager``, not through an injected lambda, because the guard's
first act is to ask what this resource's directory is and a stubbed
answer proves nothing about the lookup. The project's registered NAME
differs from its directory's basename throughout: this repository has
shipped a fatal bug under a fully green suite whose fixtures collapsed
two identifiers that production keeps distinct.

**The corpus is hostile input, not typos.** Traversal (absolute and
relative), absolute smuggling, a symlink escaping at validation time,
a symlinked DIRECTORY inside the project, the prefix collision that a
naive ``startswith`` admits, and the scratch subtree's own sibling.
Each names where it comes from — a wire field, a project.json, a
config file — because the guard exists for values vaibify did not
write.

A plain relative path is deliberately NOT in the hostile list. It
resolves against the project root, which is what the container leg has
always done with one (docker exec runs in the image's working
directory), and the repo-relative form is the wire contract for every
step directory, script and output in a workflow. The join happens
before containment is checked, so a relative path that escapes is
refused exactly as its absolute spelling is — which is why the two
entries above it exist.

**The generators are consumed.** ``fiterStreamFile`` validates nothing
until its first ``next()``, so a test that merely calls it passes
against a completely unguarded implementation.
"""

import os

import pytest

from vaibify.config import registryManager
from vaibify.host.hostConnection import (
    HostConnection,
    HostPathOutsideProjectError,
)


S_PROJECT_NAME = "corpus-host-project"
S_PROJECT_DIRECTORY_BASENAME = "aDifferentDirectoryName"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the registry so the REAL resolver answers from tmp_path."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


@pytest.fixture()
def tProjectAndConnection(tmp_path):
    """Return (project root, connection) over a REGISTERED host project.

    The name and the directory basename differ on purpose, so a
    resolver that answered with either one in place of the other would
    show here instead of in production.
    """
    sProjectRoot = str(tmp_path / S_PROJECT_DIRECTORY_BASENAME)
    os.makedirs(sProjectRoot, exist_ok=True)
    with open(
        os.path.join(sProjectRoot, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT_NAME}\n")
    # The name comes from the config file, so a directory basename
    # that differs from it is exactly what production looks like.
    registryManager.fnAddProject(sProjectRoot, sMode="host")
    return sProjectRoot, HostConnection()


def _flistBuildHostileCorpus(sProjectRoot, tmp_path):
    """Return (label, path) pairs a host project must never open.

    Every entry is a value that can arrive from outside vaibify: an
    HTTP or WebSocket field, a project.json a researcher edited, a
    config file. None of them is a mistake somebody made once.
    """
    sSecretPath = str(tmp_path / "someoneElsesSecret.env")
    with open(sSecretPath, "w") as fileSecret:
        fileSecret.write("token")

    sEscapingLink = os.path.join(sProjectRoot, "escapingLink")
    if not os.path.lexists(sEscapingLink):
        os.symlink(sSecretPath, sEscapingLink)

    sEscapingDirectoryLink = os.path.join(sProjectRoot, "escapingDir")
    if not os.path.lexists(sEscapingDirectoryLink):
        os.symlink(str(tmp_path), sEscapingDirectoryLink)

    # The sibling whose name EXTENDS the project's. A prefix comparison
    # without the separator admits it, and it is a directory the
    # researcher may genuinely have beside their project.
    sPrefixSibling = str(
        tmp_path / (S_PROJECT_DIRECTORY_BASENAME + "Backup")
    )
    os.makedirs(sPrefixSibling, exist_ok=True)

    return [
        # A relative path is NOT hostile -- it resolves against the
        # project root, exactly as it does against the container root
        # on the other leg. A relative path that ESCAPES is, and that
        # is what belongs here.
        ("relative traversal", os.path.join("..", "escaped.txt")),
        (
            "relative traversal into a sibling",
            os.path.join("..", S_PROJECT_DIRECTORY_BASENAME + "Backup"),
        ),
        ("plain traversal", os.path.join(sProjectRoot, "..", "escaped")),
        (
            "traversal buried mid-path",
            os.path.join(sProjectRoot, "data", "..", "..", "escaped"),
        ),
        ("absolute smuggling", sSecretPath),
        ("the project's own parent", str(tmp_path)),
        ("a symlink escaping the project", sEscapingLink),
        ("a symlinked directory escaping it", sEscapingDirectoryLink),
        ("a sibling whose name extends the root's", sPrefixSibling),
        (
            "a file inside that sibling",
            os.path.join(sPrefixSibling, "stolen.txt"),
        ),
        ("the root of the filesystem", os.sep),
    ]


# The table. Each entry is (method name, callable taking a path). A
# method absent from here is a method whose guard nothing checks --
# which is why the last test in this file exists.
def _fdictBuildPathTakingCalls(connection):
    """Return {method name: call(sPath)} for every path-taking method."""
    def fnStreamAndConsume(sPath):
        for _ in connection.fiterStreamFile(S_PROJECT_NAME, sPath):
            break

    def fnRunWithWorkdir(sPath):
        connection.ftRunInContainerStreamed(
            S_PROJECT_NAME, "true", sWorkdir=sPath,
        )

    def fnRunChunkedWithWorkdir(sPath):
        connection.ftRunInContainerStreamedWithChunks(
            S_PROJECT_NAME, "true", lambda sStream, sLine: None,
            sWorkdir=sPath,
        )

    return {
        "fbaFetchFile": lambda sPath: connection.fbaFetchFile(
            S_PROJECT_NAME, sPath,
        ),
        "flistDirectoryEntries": (
            lambda sPath: connection.flistDirectoryEntries(
                S_PROJECT_NAME, sPath,
            )
        ),
        "fbContainerPathIsFile": (
            lambda sPath: connection.fbContainerPathIsFile(
                S_PROJECT_NAME, sPath,
            )
        ),
        "fbContainerPathIsDirectory": (
            lambda sPath: connection.fbContainerPathIsDirectory(
                S_PROJECT_NAME, sPath,
            )
        ),
        "flistContainerPathsExist": (
            lambda sPath: connection.flistContainerPathsExist(
                S_PROJECT_NAME, [sPath],
            )
        ),
        "flistContainerDirectoriesExist": (
            lambda sPath: connection.flistContainerDirectoriesExist(
                S_PROJECT_NAME, [sPath],
            )
        ),
        "fdictStatPathMtimes": (
            lambda sPath: connection.fdictStatPathMtimes(
                S_PROJECT_NAME, [sPath],
            )
        ),
        "fsHashContainerFileSha256": (
            lambda sPath: connection.fsHashContainerFileSha256(
                S_PROJECT_NAME, sPath,
            )
        ),
        "fdictReadFilesystemUsage": (
            lambda sPath: connection.fdictReadFilesystemUsage(
                S_PROJECT_NAME, sPath,
            )
        ),
        "fiterStreamFile": fnStreamAndConsume,
        "fnWriteFile": lambda sPath: connection.fnWriteFile(
            S_PROJECT_NAME, sPath, b"nope",
        ),
        "fnWriteFileViaTar": lambda sPath: connection.fnWriteFileViaTar(
            S_PROJECT_NAME, sPath, b"nope",
        ),
        "ftRunInContainerStreamed": fnRunWithWorkdir,
        "ftRunInContainerStreamedWithChunks": fnRunChunkedWithWorkdir,
        "ftResultExecuteCommand": (
            lambda sPath: connection.ftResultExecuteCommand(
                S_PROJECT_NAME, "true", sWorkdir=sPath,
            )
        ),
    }


@pytest.mark.falsification
def testEveryPathTakingMethodRefusesEveryHostilePath(
    tProjectAndConnection, tmp_path,
):
    """The corpus, applied to the whole surface rather than one method.

    Kills: removing the guard call from any single method — the ones
    that swallow ``OSError`` to answer "absent" are the easiest place
    for an escape to hide, because there the refusal looks like a
    perfectly ordinary empty answer.
    """
    sProjectRoot, connection = tProjectAndConnection
    listCorpus = _flistBuildHostileCorpus(sProjectRoot, tmp_path)
    dictCalls = _fdictBuildPathTakingCalls(connection)
    listAdmitted = []
    for sMethodName, fnCall in sorted(dictCalls.items()):
        for sLabel, sHostilePath in listCorpus:
            try:
                fnCall(sHostilePath)
            except HostPathOutsideProjectError:
                continue
            except Exception as error:
                listAdmitted.append(
                    f"{sMethodName} answered {type(error).__name__} for "
                    f"{sLabel} ({sHostilePath!r}) instead of refusing"
                )
                continue
            listAdmitted.append(
                f"{sMethodName} ADMITTED {sLabel}: {sHostilePath!r}"
            )
    assert not listAdmitted, (
        "the host path guard was not consulted, or did not refuse:\n  "
        + "\n  ".join(listAdmitted)
    )


@pytest.mark.falsification
def testTheLegitimateProjectPathsAreStillAdmitted(tProjectAndConnection):
    """The other direction, or the guard could simply refuse everything.

    A boundary that refuses its own project is not secure, it is
    broken — and a corpus that only asserts refusals cannot tell the
    two apart.

    Kills: hardening the guard into a blanket refusal.
    """
    sProjectRoot, connection = tProjectAndConnection
    sFilePath = os.path.join(sProjectRoot, "results.json")
    connection.fnWriteFile(S_PROJECT_NAME, sFilePath, b"{}")
    assert connection.fbaFetchFile(S_PROJECT_NAME, sFilePath) == b"{}"
    # The repo-relative spelling of the same file, which is the form
    # the file poll and every step command actually use.
    assert connection.fbaFetchFile(
        S_PROJECT_NAME, "results.json",
    ) == b"{}"
    assert connection.fbContainerPathIsFile(S_PROJECT_NAME, sFilePath)
    assert connection.fbContainerPathIsDirectory(
        S_PROJECT_NAME, sProjectRoot,
    )
    assert connection.flistContainerPathsExist(
        S_PROJECT_NAME, [sFilePath],
    ) == [True]
    assert connection.flistContainerDirectoriesExist(
        S_PROJECT_NAME, [sFilePath, sProjectRoot],
    ) == [False, True]


@pytest.mark.falsification
def testEveryPathTakingMethodIsInTheCorpusTable(tProjectAndConnection):
    """The class-level guard: a new method cannot skip the corpus.

    The instance-level failure is "this method forgot to validate". The
    CLASS-level failure is "the corpus does not know the method
    exists", and only this test can see it — the one above passes
    happily while a brand-new unguarded entry point sits beside the
    fourteen it drives.

    The signature is the oracle rather than a hand-kept list: a
    parameter named for a path is a parameter the guard has to see.

    Kills: adding a path-taking method to ``HostConnection`` without
    adding it here.
    """
    import inspect
    _, connection = tProjectAndConnection
    setTabled = set(_fdictBuildPathTakingCalls(connection))
    setPathParameters = {
        "sPath", "sFilePath", "sDirectoryPath", "listPaths", "sWorkdir",
    }
    listUntabled = []
    for sMethodName in dir(HostConnection):
        if sMethodName.startswith("_"):
            continue
        objMember = getattr(HostConnection, sMethodName)
        if not callable(objMember):
            continue
        setParameters = set(
            inspect.signature(objMember).parameters,
        )
        if not (setParameters & setPathParameters):
            continue
        if sMethodName not in setTabled:
            listUntabled.append(sMethodName)
    assert not listUntabled, (
        "these HostConnection methods take a path and no adversarial "
        f"corpus drives them: {sorted(listUntabled)}. Add them to "
        "_fdictBuildPathTakingCalls."
    )
