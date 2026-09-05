"""The pin-drift judge must fail closed on anything but proven sameness.

CI auto-commits a pin bump when this tool answers IDENTICAL, so the
tool IS the guarantee that a rebuild did not silently change the
compiler. Every test here therefore tries to make it answer IDENTICAL
about a package that changed, or about a package it never actually
compared.
"""

import io
import tarfile

import pytest

from tools import checkToolchainPinDrift as moduleJudge


def fbaBuildPackage(dictPayload, dictSymlinks=None):
    """Return the bytes of a minimal .deb carrying the given payload.

    Uses data.tar.gz rather than zstd so the tests need no third-party
    decompressor; the tool's member dispatch reads the extension.
    """
    baTarBuffer = io.BytesIO()
    with tarfile.open(fileobj=baTarBuffer, mode="w:gz") as tarPayload:
        for sPath, sContent in sorted(dictPayload.items()):
            infoEntry = tarfile.TarInfo(f"./{sPath}")
            baContent = sContent.encode("utf-8")
            infoEntry.size = len(baContent)
            tarPayload.addfile(infoEntry, io.BytesIO(baContent))
        for sPath, sTarget in sorted((dictSymlinks or {}).items()):
            infoLink = tarfile.TarInfo(f"./{sPath}")
            infoLink.type = tarfile.SYMTYPE
            infoLink.linkname = sTarget
            tarPayload.addfile(infoLink)
    baPayload = baTarBuffer.getvalue()

    baDeb = bytearray(b"!<arch>\n")
    for sName, baMember in (
        ("debian-binary", b"2.0\n"),
        ("data.tar.gz", baPayload),
    ):
        baDeb += f"{sName:<16}0           0     0     100644  ".encode("ascii")
        baDeb += f"{len(baMember):<10}".encode("ascii") + b"`\n"
        baDeb += baMember
        if len(baMember) % 2:
            baDeb += b"\n"
    return bytes(baDeb)


def test_an_identical_payload_is_the_only_thing_that_passes():
    """Two packages differing only in the changelog are inert."""
    dictCommon = {"usr/include/linux/errno.h": "#define EPERM 1\n"}
    baOld = fbaBuildPackage(
        {**dictCommon, "usr/share/doc/pkg/changelog.Debian": "6.8.0-138\n"}
    )
    baNew = fbaBuildPackage(
        {**dictCommon, "usr/share/doc/pkg/changelog.Debian": "6.8.0-139\n"}
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_IDENTICAL, listDetail


def test_a_changed_header_is_never_called_identical():
    """The defect the tool exists to catch: a real compile input moved.

    If this ever answers IDENTICAL, CI auto-commits a compiler change
    nobody looked at.
    """
    baOld = fbaBuildPackage({"usr/include/linux/errno.h": "#define EPERM 1\n"})
    baNew = fbaBuildPackage({"usr/include/linux/errno.h": "#define EPERM 2\n"})
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "usr/include/linux/errno.h" in listDetail


def test_an_added_file_is_a_change():
    """A new header is a change even though every shared file matches."""
    baOld = fbaBuildPackage({"usr/include/linux/errno.h": "x\n"})
    baNew = fbaBuildPackage(
        {"usr/include/linux/errno.h": "x\n", "usr/include/linux/new.h": "y\n"}
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "usr/include/linux/new.h" in listDetail


def test_a_removed_file_is_a_change():
    """A dropped header is a change; comparing only shared keys misses it."""
    baOld = fbaBuildPackage(
        {"usr/include/linux/errno.h": "x\n", "usr/include/linux/gone.h": "y\n"}
    )
    baNew = fbaBuildPackage({"usr/include/linux/errno.h": "x\n"})
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "usr/include/linux/gone.h" in listDetail


def test_the_documentation_exemption_is_narrow():
    """"usr/share/doc/" must not swallow "usr/share/docbook/".

    Written as a bare prefix the exemption widens silently -- the same
    shape as a ".git" prefix exempting ".gitignore" -- and a real file
    then stops being compared while the verdict still reads IDENTICAL.
    """
    assert moduleJudge.fbIsDocumentationMember("usr/share/doc/pkg/changelog")
    assert not moduleJudge.fbIsDocumentationMember("usr/share/docbook/dtd.h")
    assert not moduleJudge.fbIsDocumentationMember("usr/share/documentation.h")
    assert not moduleJudge.fbIsDocumentationMember("usr/include/linux/errno.h")


def test_a_changed_file_under_a_docbook_lookalike_still_escalates():
    """The narrowness matters end to end, not just in the predicate."""
    baOld = fbaBuildPackage({"usr/share/docbook/dtd.h": "#define A 1\n"})
    baNew = fbaBuildPackage({"usr/share/docbook/dtd.h": "#define A 2\n"})
    sVerdict, _ = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED


def test_a_retargeted_symlink_is_a_change():
    """A link moved to a different target is a changed compile input.

    Comparing only regular-file contents calls this pair identical:
    both packages carry the same path list and no file body moved.
    `linux-libc-dev` ships the arch header links, so this is the shape
    an architecture repoint would take.
    """
    baOld = fbaBuildPackage({}, {"usr/include/asm": "asm-x86"})
    baNew = fbaBuildPackage({}, {"usr/include/asm": "asm-arm64"})
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "usr/include/asm" in listDetail


def test_an_empty_payload_is_uncomparable_not_identical():
    """Two packages with nothing to compare have not been shown equal.

    Set equality over two empty maps is trivially true, which would
    turn a failed extraction into a pass.
    """
    baEmpty = fbaBuildPackage({})
    sVerdict, _ = moduleJudge.ftComparePackagePayloads(baEmpty, baEmpty)
    assert sVerdict == moduleJudge.S_VERDICT_UNCOMPARABLE


def test_a_documentation_only_package_is_uncomparable():
    """Excluding every member leaves nothing compared, so escalate."""
    baOld = fbaBuildPackage({"usr/share/doc/pkg/changelog": "a\n"})
    baNew = fbaBuildPackage({"usr/share/doc/pkg/changelog": "b\n"})
    sVerdict, _ = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_UNCOMPARABLE


def test_a_missing_snapshot_escalates_rather_than_passing(monkeypatch):
    """An unfetchable superseded package is UNCOMPARABLE, never IDENTICAL."""
    monkeypatch.setattr(
        moduleJudge,
        "fbaFetchUrl",
        lambda sUrl: (_ for _ in ()).throw(OSError("no route")),
    )
    sVerdict, listDetail = moduleJudge.ftJudgeOnePin(
        "ubuntu:24.04",
        "linux-libc-dev",
        "6.8.0-138.138",
        "6.8.0-139.139",
        {"linux-libc-dev": "pool/main/l/linux/linux-libc-dev_6.8.0-139.139_amd64.deb"},
    )
    assert sVerdict == moduleJudge.S_VERDICT_UNCOMPARABLE
    assert listDetail


def test_an_unknown_pool_path_escalates():
    """A package the archive cannot place is not silently skipped."""
    sVerdict, _ = moduleJudge.ftJudgeOnePin(
        "ubuntu:24.04", "linux-libc-dev", "6.8.0-138.138", "6.8.0-139.139", {}
    )
    assert sVerdict == moduleJudge.S_VERDICT_UNCOMPARABLE


def test_the_pins_are_read_from_the_shipped_dockerfile():
    """The tool grades the real pin set, not a retyped copy."""
    dictPinned = moduleJudge.fdictParsePinnedVersions(moduleJudge.fsReadDockerfile())
    assert len(dictPinned) >= 40
    for sPackage in ("gcc", "gcc-13", "libc6", "linux-libc-dev", "make"):
        assert sPackage in dictPinned, f"{sPackage} missing from the parsed pins"
    assert dictPinned["gcc-13"].startswith("13.")


def test_the_base_image_is_read_with_its_digest():
    """A comparison against an unpinned base image proves nothing."""
    sBaseImage = moduleJudge.fsParseBaseImage(moduleJudge.fsReadDockerfile())
    assert "@sha256:" in sBaseImage


def test_drift_detection_names_only_moved_pins():
    """An unchanged pin must never be offered for a bump."""
    listDrift = moduleJudge.flistDetectDrift(
        {"gcc": "4:13.2.0-7ubuntu1", "linux-libc-dev": "6.8.0-138.138"},
        {"gcc": "4:13.2.0-7ubuntu1", "linux-libc-dev": "6.8.0-139.139"},
    )
    assert listDrift == [("linux-libc-dev", "6.8.0-138.138", "6.8.0-139.139")]


def test_applying_a_bump_rewrites_exactly_one_pin():
    """The rewrite must not disturb a neighbouring package's version."""
    sText = moduleJudge.fsReadDockerfile()
    dictBefore = moduleJudge.fdictParsePinnedVersions(sText)
    sUpdated = moduleJudge.fsApplyPinBumps(
        sText, [("linux-libc-dev", dictBefore["linux-libc-dev"], "9.9.9-9.9")]
    )
    dictAfter = moduleJudge.fdictParsePinnedVersions(sUpdated)
    assert dictAfter["linux-libc-dev"] == "9.9.9-9.9"
    assert {k: v for k, v in dictAfter.items() if k != "linux-libc-dev"} == {
        k: v for k, v in dictBefore.items() if k != "linux-libc-dev"
    }


def test_applying_a_bump_refuses_a_version_that_is_not_there():
    """A stale expectation must raise, not silently rewrite nothing."""
    with pytest.raises(ValueError):
        moduleJudge.fsApplyPinBumps(
            moduleJudge.fsReadDockerfile(),
            [("linux-libc-dev", "0.0.0-not-pinned", "9.9.9-9.9")],
        )


def test_a_missing_zstd_decompressor_raises_rather_than_returning_empty():
    """Degrading to an empty payload would read as IDENTICAL."""
    with pytest.raises((RuntimeError, Exception)):
        moduleJudge.fbaDecompressZstandard(b"not really zstd")
