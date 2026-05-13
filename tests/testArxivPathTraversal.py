"""Security tests isolating the tarball extraction safeguards.

These tests construct hostile tarballs in memory and assert that
:func:`arxivClient._fnExtractTarballSafely` rejects them before any
malicious file lands on disk. They are deliberately separate from the
happy-path test suite because each scenario maps to a distinct attack
mode (path traversal, symlink escape, special files, zip-bomb).
"""

import io
import os
import tarfile

import pytest

from vaibify.reproducibility import arxivClient
from vaibify.reproducibility.arxivClient import ArxivExtractionError


def _fsBuildTarballOnDisk(tmp_path, listMembers, fnPostAddHook=None):
    """Write listMembers to a real .tar.gz under tmp_path; return its path."""
    sPath = str(tmp_path / "hostile.tar.gz")
    with tarfile.open(sPath, mode="w:gz") as tarballHandle:
        for infoTar, baContent in listMembers:
            if baContent is None:
                tarballHandle.addfile(infoTar)
            else:
                tarballHandle.addfile(infoTar, io.BytesIO(baContent))
        if fnPostAddHook is not None:
            fnPostAddHook(tarballHandle)
    return sPath


def _ftInfoForFile(sName, baContent):
    """Return a (TarInfo, content) tuple for one regular file."""
    infoTar = tarfile.TarInfo(name=sName)
    infoTar.size = len(baContent)
    return (infoTar, baContent)


def _ftInfoForAbsoluteFile(sAbsoluteName, baContent):
    """Return a (TarInfo, content) tuple whose name is absolute."""
    infoTar = tarfile.TarInfo(name=sAbsoluteName)
    infoTar.size = len(baContent)
    return (infoTar, baContent)


def _ftInfoForSymlink(sName, sTarget):
    """Return a (TarInfo, None) tuple for a symlink entry."""
    infoTar = tarfile.TarInfo(name=sName)
    infoTar.type = tarfile.SYMTYPE
    infoTar.linkname = sTarget
    return (infoTar, None)


def _ftInfoForHardlink(sName, sTarget):
    """Return a (TarInfo, None) tuple for a hard-link entry."""
    infoTar = tarfile.TarInfo(name=sName)
    infoTar.type = tarfile.LNKTYPE
    infoTar.linkname = sTarget
    return (infoTar, None)


def _ftInfoForFifo(sName):
    """Return a (TarInfo, None) tuple for a FIFO."""
    infoTar = tarfile.TarInfo(name=sName)
    infoTar.type = tarfile.FIFOTYPE
    return (infoTar, None)


def _ftInfoForDevice(sName):
    """Return a (TarInfo, None) tuple for a character device."""
    infoTar = tarfile.TarInfo(name=sName)
    infoTar.type = tarfile.CHRTYPE
    return (infoTar, None)


def _fnExtractInto(tmp_path, sTarballPath):
    """Invoke the extractor against an extraction dir under tmp_path."""
    sExtractDir = str(tmp_path / "extracted")
    arxivClient._fnExtractTarballSafely(sTarballPath, sExtractDir)
    return sExtractDir


def test_rejects_parent_traversal_entry(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFile("../escape.txt", b"X")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)
    assert not os.path.exists(tmp_path / "escape.txt")


def test_rejects_deep_parent_traversal(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFile("a/b/../../../escape.txt", b"X")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_rejects_absolute_path_entry(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForAbsoluteFile("/tmp/escape.txt", b"X")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)
    assert not os.path.exists("/tmp/escape.txt")


def test_rejects_symlink_entry(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForSymlink("link", "/etc/passwd")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_rejects_hardlink_entry(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFile("real.txt", b"X"),
         _ftInfoForHardlink("link", "real.txt")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_rejects_fifo_special_file(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFifo("named-pipe")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_rejects_device_special_file(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForDevice("dev-node")],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_rejects_oversize_uncompressed_payload(tmp_path):
    iCeiling = arxivClient._I_MAX_UNCOMPRESSED_BYTES
    baBig = b"\x00" * (iCeiling + 1)
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFile("big.bin", baBig)],
    )
    with pytest.raises(ArxivExtractionError):
        _fnExtractInto(tmp_path, sTarball)


def test_accepts_safe_nested_entries(tmp_path):
    sTarball = _fsBuildTarballOnDisk(
        tmp_path,
        [_ftInfoForFile("paper/figs/fig1.png", b"PNG-CONTENT"),
         _ftInfoForFile("paper/main.tex", b"%dummy")],
    )
    sExtractDir = _fnExtractInto(tmp_path, sTarball)
    sFig = os.path.join(sExtractDir, "paper/figs/fig1.png")
    sTex = os.path.join(sExtractDir, "paper/main.tex")
    assert os.path.isfile(sFig)
    assert os.path.isfile(sTex)
