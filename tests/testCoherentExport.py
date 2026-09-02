"""The coherence check, and the two independent ways an export tears.

``get_archive`` walks a directory without freezing it, so a concurrent
write leaves the archive holding a mixture of two moments. This file
asserts that the mixture is REFUSED, and — more importantly — that the
two halves of the check are not redundant: each catches a tear the
other is blind to.

Every fixture here is built so the property can actually fail. A
coherence check tested only against a quiet repository is a check that
has never been asked a question.
"""

import hashlib
import io
import posixpath
import subprocess
import tarfile

import pytest

from vaibify.docker import coherentExport


S_REPO_PATH = "/workspace/probeRepo"
S_REPO_BASENAME = "probeRepo"


def _fsIdentity(baContent):
    hashBlob = hashlib.sha1()
    hashBlob.update(f"blob {len(baContent)}\0".encode())
    hashBlob.update(baContent)
    return hashBlob.hexdigest()


def _fdictObservation(dictFiles, dictExtraPaths=None, sHead="a" * 40):
    """Build a well-formed observation over a file dict."""
    dictIdentities = {
        sName: {"sType": "file", "sIdentity": _fsIdentity(baContent)}
        for sName, baContent in dictFiles.items()
    }
    dictIdentities.update(dictExtraPaths or {})
    return {
        "bSuccess": True, "sReason": "", "sHeadSha": sHead,
        "sPorcelainDigest": "b" * 64, "listIgnoredPaths": [],
        "dictPathIdentities": dictIdentities,
    }


def _fbaArchive(dictFiles, dictSymlinks=None, listDirectories=()):
    """Build a tar named the way ``get_archive`` names one."""
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName in listDirectories:
            infoDirectory = tarfile.TarInfo(
                name=posixpath.join(S_REPO_BASENAME, sName))
            infoDirectory.type = tarfile.DIRTYPE
            fileTar.addfile(infoDirectory)
        for sName, baContent in sorted(dictFiles.items()):
            infoMember = tarfile.TarInfo(
                name=posixpath.join(S_REPO_BASENAME, sName))
            infoMember.size = len(baContent)
            fileTar.addfile(infoMember, io.BytesIO(baContent))
        for sName, sTarget in sorted((dictSymlinks or {}).items()):
            infoLink = tarfile.TarInfo(
                name=posixpath.join(S_REPO_BASENAME, sName))
            infoLink.type = tarfile.SYMTYPE
            infoLink.linkname = sTarget
            fileTar.addfile(infoLink)
    return bufferTar.getvalue()


class _StubConnection:
    """Answers the two reads the export makes, from scripted values."""

    def __init__(self, listObservations, baArchive):
        self._listObservations = list(listObservations)
        self._baArchive = baArchive
        self.iObservationCount = 0

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
        del sContainerId, sRepoPath
        self.iObservationCount += 1
        return self._listObservations.pop(0)

    def fbaFetchDirectoryArchive(self, sContainerId, sPath, iMaxBytes):
        del sContainerId, sPath, iMaxBytes
        return self._baArchive


def _fbaExport(listObservations, baArchive):
    return coherentExport.fbaExportRepositoryCoherently(
        _StubConnection(listObservations, baArchive),
        "containerId", S_REPO_PATH, 1 << 20,
    )


def testAQuietRepositoryExportsUnchanged():
    """The check must not refuse a repository nobody touched.

    Stated first because a check that refuses everything satisfies
    every other test in this file and is worthless.
    """
    dictFiles = {"a.txt": b"alpha\n", "sub/b.txt": b"beta\n"}
    dictObservation = _fdictObservation(dictFiles)
    baArchive = _fbaExport(
        [dictObservation, dictObservation],
        _fbaArchive(dictFiles, listDirectories=("sub",)),
    )
    assert baArchive


def testTheObservationHalfCatchesARepositoryThatMovedOn():
    """A file rewritten after the walk passed it leaves a valid archive.

    The archive agrees with the before-observation perfectly, because
    the walk read the file before it changed. Only comparing the two
    observations reveals that the repository is no longer in the state
    the archive describes.
    """
    dictBefore = {"a.txt": b"alpha\n"}
    baArchive = _fbaArchive(dictBefore)
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        _fbaExport(
            [_fdictObservation(dictBefore),
             _fdictObservation({"a.txt": b"rewritten\n"})],
            baArchive,
        )
    assert "a.txt was rewritten during the copy" in str(errorRaised.value)


def testTheArchiveHalfCatchesAFileChangedAndChangedBack():
    """Identical observations, contradicting bytes — the redundancy test.

    If either half of the check were dropped as duplicative, exactly
    one of these two tests would keep passing. That is the whole reason
    both exist.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    dictObservation = _fdictObservation(dictFiles)
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        _fbaExport(
            [dictObservation, dictObservation],
            _fbaArchive({"a.txt": b"caught mid-write\n"}),
        )
    assert "'a.txt' was rewritten while it was being copied" in str(
        errorRaised.value)


def testAHeadThatMovedIsNamedAsACommitNotAsFortyFiles():
    """The message must name the most intelligible cause available.

    A new commit changes every file identity it touches. Reporting the
    first of forty paths would be true and useless; "HEAD moved" is what
    a researcher can act on.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        _fbaExport(
            [_fdictObservation(dictFiles, sHead="a" * 40),
             _fdictObservation({"a.txt": b"next commit\n"},
                               sHead="c" * 40)],
            _fbaArchive(dictFiles),
        )
    assert "HEAD moved from" in str(errorRaised.value)


def testAnUnobservedMemberIsRefusedAndNamesItsLikelyCause():
    """A file git cannot enumerate must refuse, not pass unchecked.

    The exemption is for ``.git/`` alone. A checked-out submodule's
    files are the case that produces an unobserved member in practice,
    and the message says so — a bare "unexpected file" would leave the
    researcher with nothing to look for.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        _fbaExport(
            [_fdictObservation(dictFiles)] * 2,
            _fbaArchive({**dictFiles, "vendor/lib.c": b"int main(){}\n"}),
        )
    assert "vendor/lib.c" in str(errorRaised.value)
    assert "submodule" in str(errorRaised.value)


@pytest.mark.falsification
def testTheGitInternalsExemptionIsNarrow():
    """``.git/`` passes unchecked; a lookalike sibling does not.

    The exemption is a statement about what git can SEE, so it must
    match the repository's own internals and nothing that merely starts
    the same way. A prefix test written as ``startswith(".git")`` would
    silently exempt ``.gitignore`` — a real, manifest-relevant file —
    and this is the assertion that fails when it does.
    
    Kills: matching the exemption prefix with the trailing slash
    stripped, which exempts every path merely BEGINNING with
    ".git" -- .gitignore, .gitattributes, .gitmodules.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    dictObservation = _fdictObservation(dictFiles)
    baArchive = _fbaExport(
        [dictObservation, dictObservation],
        _fbaArchive({**dictFiles, ".git/objects/ab/cdef": b"packed\n"}),
    )
    assert baArchive, ".git internals must be exempt from the check"

    with pytest.raises(coherentExport.ExportTornError) as errorRaised:
        _fbaExport(
            [dictObservation, dictObservation],
            _fbaArchive({**dictFiles, ".gitignore": b"*.pyc\n"}),
        )
    assert ".gitignore" in str(errorRaised.value), (
        "the exemption leaked onto a file that only shares a prefix"
    )


def testASymlinkIsComparedByItsTargetNotItsContents():
    """Hashing a symlink reads THROUGH it, so the target is the identity.

    A check that hashed the link would compare the target file's bytes
    and pass a link repointed at a file with identical contents.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    dictObservation = _fdictObservation(
        dictFiles,
        {"link.txt": {"sType": "symlink", "sIdentity": "a.txt"}},
    )
    assert _fbaExport(
        [dictObservation, dictObservation],
        _fbaArchive(dictFiles, dictSymlinks={"link.txt": "a.txt"}),
    )
    with pytest.raises(coherentExport.ExportTornError,
                       match="points at"):
        _fbaExport(
            [dictObservation, dictObservation],
            _fbaArchive(dictFiles,
                        dictSymlinks={"link.txt": "elsewhere.txt"}),
        )


def testAPathTheRepositoryHoldsMustReachTheCopy():
    """An omitted file is a torn export in the other direction.

    Checking only archive-against-observation would accept a copy that
    silently dropped half the repository, because every member it DID
    carry would match.
    """
    dictFiles = {"a.txt": b"alpha\n", "b.txt": b"beta\n"}
    dictObservation = _fdictObservation(dictFiles)
    with pytest.raises(coherentExport.ExportTornError,
                       match="absent from the copy"):
        _fbaExport(
            [dictObservation, dictObservation],
            _fbaArchive({"a.txt": b"alpha\n"}),
        )


def testATrackedPathDeletedFromTheWorktreeIsNotTreatedAsMissingFromTheCopy():
    """``missing`` means "git tracks it, it is not on disk".

    It legitimately has no archive member, so counting it as an omission
    would refuse every repository with a deleted-but-not-committed file
    — a common, harmless state.
    """
    dictFiles = {"a.txt": b"alpha\n"}
    dictObservation = _fdictObservation(
        dictFiles, {"deleted.txt": {"sType": "missing", "sIdentity": ""}},
    )
    assert _fbaExport(
        [dictObservation, dictObservation], _fbaArchive(dictFiles),
    )


def testTheHostSideIdentityAgreesWithGitItself():
    """The two sides of the comparison must both agree with git.

    An INDEPENDENT oracle: the container computes identities one way and
    this module recomputes them another, and both are checked against
    ``git hash-object --no-filters`` rather than against each other.
    Two implementations that agree only with one another would freeze a
    shared mistake — the danger the falsification registry's
    independent-oracle rule names.
    """
    processGit = subprocess.run(
        ["git", "hash-object", "--no-filters", "--stdin"],
        input=b"alpha\n", capture_output=True,
    )
    if processGit.returncode != 0:
        pytest.skip("git is not available to answer as the oracle")
    assert coherentExport.fsComputeGitBlobIdentity(b"alpha\n") == (
        processGit.stdout.decode().strip()
    )
