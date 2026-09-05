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


def fbaBuildTar(dictFiles, dictSymlinks=None, dictModes=None):
    """Return gzipped tar bytes for the given members."""
    baBuffer = io.BytesIO()
    with tarfile.open(fileobj=baBuffer, mode="w:gz") as tarArchive:
        for sPath, sContent in sorted(dictFiles.items()):
            infoEntry = tarfile.TarInfo(f"./{sPath}")
            baContent = sContent.encode("utf-8")
            infoEntry.size = len(baContent)
            infoEntry.mode = (dictModes or {}).get(sPath, 0o644)
            tarArchive.addfile(infoEntry, io.BytesIO(baContent))
        for sPath, sTarget in sorted((dictSymlinks or {}).items()):
            infoLink = tarfile.TarInfo(f"./{sPath}")
            infoLink.type = tarfile.SYMTYPE
            infoLink.linkname = sTarget
            tarArchive.addfile(infoLink)
    return baBuffer.getvalue()


def fbaBuildPackage(
    dictPayload, dictSymlinks=None, dictModes=None, dictControl=None
):
    """Return the bytes of a minimal .deb carrying the given members.

    Uses gzip rather than zstd so the tests need no third-party
    decompressor; the tool's member dispatch reads the extension. Both
    sub-archives are built, because the tool compares both.
    """
    baPayload = fbaBuildTar(dictPayload, dictSymlinks, dictModes)
    baControl = fbaBuildTar(
        dictControl
        if dictControl is not None
        else {"control": "Package: pkg\nVersion: 1\n"}
    )

    baDeb = bytearray(b"!<arch>\n")
    for sName, baMember in (
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", baControl),
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
    assert "data/usr/include/linux/errno.h" in listDetail


def test_an_added_file_is_a_change():
    """A new header is a change even though every shared file matches."""
    baOld = fbaBuildPackage({"usr/include/linux/errno.h": "x\n"})
    baNew = fbaBuildPackage(
        {"usr/include/linux/errno.h": "x\n", "usr/include/linux/new.h": "y\n"}
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "data/usr/include/linux/new.h" in listDetail


def test_a_removed_file_is_a_change():
    """A dropped header is a change; comparing only shared keys misses it."""
    baOld = fbaBuildPackage(
        {"usr/include/linux/errno.h": "x\n", "usr/include/linux/gone.h": "y\n"}
    )
    baNew = fbaBuildPackage({"usr/include/linux/errno.h": "x\n"})
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "data/usr/include/linux/gone.h" in listDetail


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
    assert "data/usr/include/asm" in listDetail


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


def test_a_permission_change_is_never_called_identical():
    """Same bytes, different mode, is a changed package.

    Measured before the fix: a binary gaining setuid compared
    IDENTICAL, because only file CONTENT was hashed.
    """
    baOld = fbaBuildPackage(
        {"usr/bin/cc1": "binary"}, dictModes={"usr/bin/cc1": 0o755}
    )
    baNew = fbaBuildPackage(
        {"usr/bin/cc1": "binary"}, dictModes={"usr/bin/cc1": 0o4755}
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "data/usr/bin/cc1" in listDetail


def test_a_changed_maintainer_script_is_never_called_identical():
    """control.tar carries code too; comparing only data.tar misses it."""
    dictData = {"usr/include/linux/errno.h": "x\n"}
    baOld = fbaBuildPackage(
        dictData,
        dictControl={"control": "Package: p\n", "postinst": "#!/bin/sh\ntrue\n"},
    )
    baNew = fbaBuildPackage(
        dictData,
        dictControl={"control": "Package: p\n", "postinst": "#!/bin/sh\nfalse\n"},
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "control/postinst" in listDetail


def test_a_changed_dependency_is_never_called_identical():
    """A different Depends line changes what gets installed alongside."""
    dictData = {"usr/include/linux/errno.h": "x\n"}
    baOld = fbaBuildPackage(
        dictData, dictControl={"control": "Package: p\nDepends: libc6 (>= 2.39)\n"}
    )
    baNew = fbaBuildPackage(
        dictData, dictControl={"control": "Package: p\nDepends: libc6 (>= 2.40)\n"}
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_CHANGED
    assert "control/control" in listDetail


def test_the_version_field_alone_does_not_make_a_package_changed():
    """Version and Installed-Size differ by definition; the rest must not.

    Without this normalisation every rotation reports CHANGED and the
    tool is useless -- the failure mode opposite to the ones above.
    """
    dictData = {"usr/include/linux/errno.h": "x\n"}
    baOld = fbaBuildPackage(
        dictData,
        dictControl={"control": "Package: p\nVersion: 1\nInstalled-Size: 10\n"},
    )
    baNew = fbaBuildPackage(
        dictData,
        dictControl={"control": "Package: p\nVersion: 2\nInstalled-Size: 11\n"},
    )
    sVerdict, listDetail = moduleJudge.ftComparePackagePayloads(baOld, baNew)
    assert sVerdict == moduleJudge.S_VERDICT_IDENTICAL, listDetail


def test_a_pin_line_the_parser_cannot_read_raises():
    """A PARTIAL parse silently exempts a package from every comparison.

    Measured: one extra space took the parsed count from 45 to 44 and
    nothing else changed, so the dropped pin would be reported as "no
    drift" forever.
    """
    sText = moduleJudge.fsReadDockerfile()
    sBroken = sText.replace(
        "            make=4.3-4.1build2 \\", "            make=4.3-4.1build2  \\"
    )
    assert sBroken != sText, "the fixture no longer matches the Dockerfile"
    with pytest.raises(ValueError, match="silently exempt"):
        moduleJudge.fdictParsePinnedVersions(sBroken)


def test_a_package_entering_the_closure_is_reported():
    """An unpinned new dependency is the very thing the block prevents."""
    listReasons = moduleJudge.flistDetectClosureMembershipChanges(
        {"gcc": "1"}, {"gcc": "1", "libbrandnew0": "2"}
    )
    assert any("libbrandnew0" in sReason for sReason in listReasons)


def test_a_package_leaving_the_closure_is_reported():
    """A stale pin can make apt unsatisfiable, not merely redundant."""
    listReasons = moduleJudge.flistDetectClosureMembershipChanges(
        {"gcc": "1", "libgone0": "2"}, {"gcc": "1"}
    )
    assert any("libgone0" in sReason for sReason in listReasons)


def test_a_pool_filename_carries_no_epoch():
    """gcc 4:13.2.0-7ubuntu1 lives at gcc_13.2.0-7ubuntu1_amd64.deb.

    Confirmed against the live archive. Percent-encoding the colon
    404s, which fails closed but means the seven epoch-bearing pins --
    gcc, g++ and cpp among them -- could never be compared.
    """
    sBasename = moduleJudge.fsSupersededPoolBasename(
        "pool/main/g/gcc-defaults/gcc_13.2.0-7ubuntu1_amd64.deb",
        "gcc",
        "4:13.2.0-7ubuntu1",
    )
    assert sBasename == "gcc_13.2.0-7ubuntu1_amd64.deb"
    assert "%3a" not in sBasename and ":" not in sBasename
