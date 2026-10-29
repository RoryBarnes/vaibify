"""The host confined writer lands a pulled file where it says, and nowhere else.

Source: ``vaibify/host/hostConfinedWrite.py``.

A pull writes the researcher's machine with bytes the container
produced. The destination directory is resolved once; everything below
it is reached through directory descriptors opened without following a
link, and the final name is refused when it is a link or a directory.
The oracle is the filesystem itself: after every refusal the directory
the link pointed at holds nothing new.
"""

import os
import stat

import pytest

from vaibify.docker.confinedWrite import ContainerWriteRefusedError
from vaibify.host import hostConfinedRead, hostConfinedWrite
from vaibify.host.hostConfinedRead import HostReadUnsupportedError
from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot


@pytest.fixture
def dictTree(tmp_path):
    """A destination root, a nested directory and an outside directory."""
    sRoot = os.path.realpath(str(tmp_path / "destination"))
    sOutside = os.path.realpath(str(tmp_path / "outside"))
    os.makedirs(os.path.join(sRoot, "nested", "deeper"))
    os.makedirs(sOutside)
    return {"sRoot": sRoot, "sOutside": sOutside}


def _fiterChunks(baContent, iChunk=5):
    for iOffset in range(0, len(baContent), iChunk):
        yield baContent[iOffset:iOffset + iChunk]


def _flistNewEntries(sDirectory):
    return sorted(os.listdir(sDirectory))


def testAFileLandsWithItsBytesAndModeAndNoTemporaryRemains(dictTree):
    sTarget = os.path.join(dictTree["sRoot"], "nested", "pulled.bin")
    sLanded = fsWriteFileInsideRoot(
        dictTree["sRoot"], sTarget, _fiterChunks(b"0123456789abc"),
    )
    assert sLanded == sTarget
    with open(sTarget, "rb") as fileLanded:
        assert fileLanded.read() == b"0123456789abc"
    assert stat.S_IMODE(os.stat(sTarget).st_mode) == 0o644
    assert _flistNewEntries(os.path.dirname(sTarget)) == ["deeper", "pulled.bin"]


def testAnExistingRegularFileIsReplacedWhole(dictTree):
    sTarget = os.path.join(dictTree["sRoot"], "pulled.bin")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old content that is longer")
    fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"new"))
    with open(sTarget, "rb") as fileLanded:
        assert fileLanded.read() == b"new"


@pytest.mark.falsification
def testAFinalNameThatIsALinkIsRefusedAndTheTargetUntouched(dictTree):
    """A link planted at the destination name is refused, and its target untouched.

    Kills: ``_fnRefuseWhenFinalIsUnwritable`` no longer refusing a link
    or a directory at the final name.
    """
    sVictim = os.path.join(dictTree["sOutside"], "authorized_keys")
    sTarget = os.path.join(dictTree["sRoot"], "pulled.bin")
    os.symlink(sVictim, sTarget)
    with pytest.raises(ContainerWriteRefusedError, match="symlink or a directory"):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"x"))
    assert not os.path.exists(sVictim)
    assert os.path.islink(sTarget)
    assert _flistNewEntries(dictTree["sRoot"]) == ["nested", "pulled.bin"]


def testAFinalNameThatIsADirectoryIsRefused(dictTree):
    sTarget = os.path.join(dictTree["sRoot"], "nested")
    with pytest.raises(ContainerWriteRefusedError, match="symlink or a directory"):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"x"))
    assert _flistNewEntries(dictTree["sRoot"]) == ["nested"]


def testAParentThatIsALinkIsRefused(dictTree):
    os.symlink(dictTree["sOutside"], os.path.join(dictTree["sRoot"], "linked"))
    sTarget = os.path.join(dictTree["sRoot"], "linked", "pulled.bin")
    with pytest.raises(ContainerWriteRefusedError, match="symlink"):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"x"))
    assert _flistNewEntries(dictTree["sOutside"]) == []


def testADestinationOutsideTheRootIsRefused(dictTree):
    sTarget = os.path.join(dictTree["sOutside"], "pulled.bin")
    with pytest.raises(ContainerWriteRefusedError, match="not below"):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"x"))
    with pytest.raises(ContainerWriteRefusedError, match="not below"):
        fsWriteFileInsideRoot(
            dictTree["sRoot"], dictTree["sRoot"], _fiterChunks(b"x"),
        )
    assert _flistNewEntries(dictTree["sOutside"]) == []


@pytest.mark.falsification
def testAParentSwappedForALinkMidWalkIsRefusedAndNothingLandsOutside(
    dictTree, monkeypatch,
):
    """The parent-directory race the string-path check could not close.

    A hook between two walk steps replaces the directory the walk is
    about to enter with a link to a directory outside the root. The walk
    opens each component relative to the descriptor above it without
    following a link, so the swap is seen and refused; a walk that
    resolved the string path again would follow it and write outside.

    Kills: dropping ``O_NOFOLLOW`` from the directory open the walk
    uses (``hostConfinedRead._fiOpenDirectoryWithoutFollowing``).
    """
    sRoot = dictTree["sRoot"]
    fnOpenReal = hostConfinedRead._fiOpenDirectoryWithoutFollowing

    def fnOpenSwappingBeforeDeeper(iParent, sName):
        if sName == "deeper":
            os.rename(
                os.path.join(sRoot, "nested", "deeper"),
                os.path.join(sRoot, "nested", "deeperMoved"),
            )
            os.symlink(
                dictTree["sOutside"], os.path.join(sRoot, "nested", "deeper"),
            )
        return fnOpenReal(iParent, sName)

    monkeypatch.setattr(
        hostConfinedRead, "_fiOpenDirectoryWithoutFollowing",
        fnOpenSwappingBeforeDeeper,
    )
    sTarget = os.path.join(sRoot, "nested", "deeper", "pulled.bin")
    with pytest.raises(ContainerWriteRefusedError):
        fsWriteFileInsideRoot(sRoot, sTarget, _fiterChunks(b"escaped"))
    assert _flistNewEntries(dictTree["sOutside"]) == []
    assert _flistNewEntries(os.path.join(sRoot, "nested", "deeperMoved")) == []


def testAFailingSourceLeavesNoTemporaryBehind(dictTree):
    def fiterBroken():
        yield b"first"
        raise OSError("the stream broke")

    sTarget = os.path.join(dictTree["sRoot"], "pulled.bin")
    with pytest.raises(OSError, match="the stream broke"):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, fiterBroken())
    assert _flistNewEntries(dictTree["sRoot"]) == ["nested"]


def testItFailsClosedWithoutDirectoryRelativeAccess(dictTree, monkeypatch):
    monkeypatch.setattr(hostConfinedRead.os, "supports_dir_fd", set())
    sTarget = os.path.join(dictTree["sRoot"], "pulled.bin")
    with pytest.raises(HostReadUnsupportedError):
        fsWriteFileInsideRoot(dictTree["sRoot"], sTarget, _fiterChunks(b"x"))
    assert _flistNewEntries(dictTree["sRoot"]) == ["nested"]
    assert hostConfinedWrite.__all__ == ["fsWriteFileInsideRoot"]
