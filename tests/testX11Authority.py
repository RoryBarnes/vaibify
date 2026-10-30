"""The X11 helper: cookie files, the server-cookie fallback, and revoke-after-probe.

The helper runs every X call in its own process; here its X library
handles are replaced by a recording fake, so these tests prove what
the helper DOES around the calls (what it writes, what it prints, what
it refuses to do) and nothing about the X server, which
``tests/testX11AuthorityLive.py`` drives for real.
"""

import json
import os
import stat
import struct
from argparse import Namespace

import pytest

from vaibify.host import x11Authority

S_NUMBER = "7"
BA_COOKIE = bytes(range(16))


class _FakeHandles(x11Authority._XlibHandles):
    """The helper's handles without any library: records what is asked of it."""

    def __init__(self, sProbeOutcome=x11Authority.S_OUTCOME_ACCEPTED,
                 bSecurity=True, bReachable=True):
        self.sProbeOutcome = sProbeOutcome
        self.bSecurity = bSecurity
        self.bReachable = bReachable
        self.listRevoked = []
        self.listErrors = []
        self.libX11 = Namespace(XCloseDisplay=lambda iDisplay: None)

    def fiOpenDisplay(self, sDisplay):
        return 1 if self.bReachable else 0

    def fbSecuritySupported(self, iDisplay):
        return self.bSecurity

    def ftGenerateUntrusted(self, iDisplay):
        return BA_COOKIE, 554

    def fsProbe(self, args):
        return self.sProbeOutcome

    def fnRevoke(self, iDisplay, iAuthorizationId):
        self.listRevoked.append(iAuthorizationId)


def _fnamespaceArguments(sDirectory, sOperation="generate", iAuthorizationId=554):
    return Namespace(
        operation=sOperation, display=":7", probe_display=":7",
        number=S_NUMBER, directory=sDirectory,
        authorization_id=iAuthorizationId, authority="", library_directory="",
    )


def _fdictPrinted(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def testAWildcardEntryRoundTripsThroughTheAuthorityParser(tmp_path):
    baEntry = x11Authority.fbaRenderWildcardAuthority(S_NUMBER, BA_COOKIE)
    assert struct.unpack(">H", baEntry[:2])[0] == x11Authority.I_FAMILY_WILD
    sPath = str(tmp_path / "Xauthority")
    with open(sPath, "wb") as fileAuthority:
        fileAuthority.write(baEntry)
    assert x11Authority.fbaFindServerCookie(sPath, S_NUMBER) == BA_COOKIE
    assert x11Authority.fbaFindServerCookie(sPath, "8") == b""
    assert x11Authority.fbaFindServerCookie(str(tmp_path / "absent"), S_NUMBER) == b""


def testTheCookieFileIsPrivateAndLeavesNoTemporaryBehind(tmp_path):
    x11Authority.fnWriteCookieFileAtomically(str(tmp_path), b"first")
    x11Authority.fnWriteCookieFileAtomically(str(tmp_path), b"second")
    sPath = str(tmp_path / x11Authority.S_COOKIE_FILE_NAME)
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
    with open(sPath, "rb") as fileCookie:
        assert fileCookie.read() == b"second"
    assert os.listdir(str(tmp_path)) == [x11Authority.S_COOKIE_FILE_NAME]


def testGenerateWritesTheCookieAndPrintsTheId(tmp_path, capsys):
    _FakeHandles().fnRunGenerate(_fnamespaceArguments(str(tmp_path)))
    assert _fdictPrinted(capsys) == {
        "iAuthorizationId": 554, "sOutcome": x11Authority.S_OUTCOME_GENERATED,
    }
    sPath = str(tmp_path / x11Authority.S_COOKIE_FILE_NAME)
    assert x11Authority.fbaFindServerCookie(sPath, S_NUMBER) == BA_COOKIE


def testGenerateWritesNothingWhenTheServerLacksSecurity(tmp_path, capsys):
    _FakeHandles(bSecurity=False).fnRunGenerate(_fnamespaceArguments(str(tmp_path)))
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_SECURITY_UNSUPPORTED
    assert os.listdir(str(tmp_path)) == []


def testGenerateReportsAnUnreachableServer(tmp_path, capsys):
    _FakeHandles(bReachable=False).fnRunGenerate(_fnamespaceArguments(str(tmp_path)))
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_SERVER_UNREACHABLE


@pytest.mark.falsification
def testARefusedProbeRevokesNothing(tmp_path, capsys):
    """A cookie the server already refuses names an id another container may now hold.

    Kills: revoking the recorded id without probing the cookie first.
    """
    handles = _FakeHandles(sProbeOutcome=x11Authority.S_OUTCOME_REFUSED)
    handles.fnRunRevoke(_fnamespaceArguments(str(tmp_path), "revoke"))
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_GONE
    assert handles.listRevoked == []


def testAnAcceptedProbeRevokesTheRecordedId(tmp_path, capsys):
    handles = _FakeHandles()
    handles.fnRunRevoke(_fnamespaceArguments(str(tmp_path), "revoke", 9001))
    assert _fdictPrinted(capsys) == {
        "iAuthorizationId": 9001, "sOutcome": x11Authority.S_OUTCOME_REVOKED,
    }
    assert handles.listRevoked == [9001]


def testAnUnreachableServerRevokesNothingAndSaysSo(tmp_path, capsys):
    handles = _FakeHandles(sProbeOutcome=x11Authority.S_OUTCOME_SERVER_UNREACHABLE)
    handles.fnRunRevoke(_fnamespaceArguments(str(tmp_path), "revoke"))
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_SERVER_UNREACHABLE
    assert handles.listRevoked == []


@pytest.mark.falsification
def testTheTimeoutIsNeitherZeroNorPastTheServersClock():
    """Xorg reads zero as "expire now" and a span past 2**31 ms as already expired.

    Kills: a zero timeout, which refuses the cookie on its first use.
    """
    iMilliseconds = x11Authority.I_SECURITY_NEVER_TIMEOUT_SECONDS * 1000
    assert 0 < iMilliseconds < 2 ** 31


def testCopyServerCookieThroughMainCopiesTheDisplaysEntry(tmp_path, capsys):
    sAuthority = str(tmp_path / "serverAuthority")
    with open(sAuthority, "wb") as fileAuthority:
        fileAuthority.write(x11Authority.fbaRenderWildcardAuthority("0", b"x" * 16))
        fileAuthority.write(x11Authority.fbaRenderWildcardAuthority(S_NUMBER, BA_COOKIE))
    sDirectory = str(tmp_path / "container")
    os.mkdir(sDirectory)
    x11Authority.main([
        "copy-server-cookie", "--directory", sDirectory, "--number", S_NUMBER,
        "--authority", sAuthority,
    ])
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_COPIED
    assert x11Authority.fbaFindServerCookie(
        os.path.join(sDirectory, x11Authority.S_COOKIE_FILE_NAME), S_NUMBER,
    ) == BA_COOKIE


def testCopyServerCookieIsAnErrorWhenTheDisplayHasNoEntry(tmp_path, capsys):
    sAuthority = str(tmp_path / "serverAuthority")
    with open(sAuthority, "wb") as fileAuthority:
        fileAuthority.write(x11Authority.fbaRenderWildcardAuthority("0", b"x" * 16))
    x11Authority.main([
        "copy-server-cookie", "--directory", str(tmp_path), "--number", S_NUMBER,
        "--authority", sAuthority,
    ])
    dictPrinted = _fdictPrinted(capsys)
    assert dictPrinted["sOutcome"] == x11Authority.S_OUTCOME_ERROR
    assert S_NUMBER in dictPrinted["sReason"]


def testMainReportsMissingLibrariesAsItsOwnOutcome(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(x11Authority, "_fsFindLibrary", lambda sName, sOverride="": "")
    x11Authority.main(["probe", "--directory", str(tmp_path)])
    assert _fdictPrinted(capsys)["sOutcome"] == x11Authority.S_OUTCOME_LIBRARY_MISSING


def testMainTurnsAnOperationFailureIntoTheErrorOutcome(tmp_path, capsys, monkeypatch):
    class _FailingHandles(_FakeHandles):
        def __init__(self, sDirectoryOverride=""):
            super().__init__()

        def fnRunOperation(self, args):
            raise RuntimeError("the fake fell over")

    monkeypatch.setattr(x11Authority, "_XlibHandles", _FailingHandles)
    x11Authority.main(["generate", "--directory", str(tmp_path)])
    dictPrinted = _fdictPrinted(capsys)
    assert dictPrinted["sOutcome"] == x11Authority.S_OUTCOME_ERROR
    assert "fell over" in dictPrinted["sReason"]
