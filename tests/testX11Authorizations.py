"""The per-container X11 authorization record and what the hub does with it.

The helper is replaced by a recording fake, so these prove the hub's
half: what the record holds (never a cookie), when the fallback
applies, that a release probes before it revokes, that a renewal never
revokes the id the restarted server may have handed to someone else,
and that the mount is read-only. The real helper is driven once,
through its own subprocess, for the operation that needs no X server.
"""

import json
import os
import stat

import pytest

from vaibify.docker import x11Authorizations
from vaibify.host import x11Authority

S_NAME = "demoEnvironment"
S_UUID = "a" * 32


class _FakeHelper:
    """Answers each operation from a script; records every call."""

    def __init__(self, dictAnswers):
        self.dictAnswers = dict(dictAnswers)
        self.listCalls = []

    def __call__(self, listArguments, fTimeoutSeconds=15.0):
        self.listCalls.append(list(listArguments))
        return dict(self.dictAnswers[listArguments[0]])

    def flistOperations(self):
        return [listCall[0] for listCall in self.listCalls]


@pytest.fixture
def fixtureX11Home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XAUTHORITY", raising=False)
    return tmp_path


def _fhelperInstall(monkeypatch, dictAnswers):
    helper = _FakeHelper(dictAnswers)
    monkeypatch.setattr(x11Authorizations, "fdictRunHelper", helper)
    return helper


DICT_GENERATED = {"sOutcome": x11Authority.S_OUTCOME_GENERATED, "iAuthorizationId": 554}
DICT_COPIED = {"sOutcome": x11Authority.S_OUTCOME_COPIED}


@pytest.mark.falsification
def testAPreparedAuthorizationIsRecordedWithoutItsCookie(fixtureX11Home, monkeypatch):
    """Kills: writing the record world-readable."""
    helper = _fhelperInstall(monkeypatch, {"generate": DICT_GENERATED})
    dictOutcome = x11Authorizations.fdictPrepareAuthorization(
        S_NAME, S_UUID, "/tmp/launchd/org.xquartz:0", "localhost:0", "0",
    )
    assert x11Authorizations.fbAuthorizationUsable(dictOutcome)
    dictEntry = x11Authorizations.fdictReadAuthorizations()[S_NAME]
    assert dictEntry["iAuthorizationId"] == 554
    assert dictEntry["sTrustLevel"] == x11Authorizations.S_TRUST_UNTRUSTED
    assert dictEntry["sProjectUuid"] == S_UUID
    assert dictEntry["sDisplay"] == "localhost:0"
    sRecordPath = os.path.join(x11Authorizations.fsX11Root(), "authorizations.json")
    assert stat.S_IMODE(os.stat(sRecordPath).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(dictEntry["sDirectory"]).st_mode) == 0o700
    assert "--directory" in helper.listCalls[0]
    assert "--probe-display" in helper.listCalls[0]


@pytest.mark.falsification
def testTheAuthorizationIsMountedReadOnly():
    """A container that could rewrite its own cookie file could plant any cookie it likes.

    Kills: mounting the authorization directory writable.
    """
    listArguments = x11Authorizations.flistMountArguments("/home/r/.vaibify/x11/demo")
    sMount = listArguments[listArguments.index("--mount") + 1]
    assert "readonly" in sMount.split(",")
    assert f"target={x11Authorizations.S_CONTAINER_AUTHORITY_DIRECTORY}" in sMount
    assert f"XAUTHORITY={x11Authorizations.S_CONTAINER_AUTHORITY_PATH}" in listArguments


@pytest.mark.falsification
def testAServerWithoutSecurityFallsBackToItsOwnCookieAndSaysSo(fixtureX11Home, monkeypatch):
    """Kills: staying silent about a copied server cookie, which is full display access."""
    helper = _fhelperInstall(monkeypatch, {
        "generate": {"sOutcome": x11Authority.S_OUTCOME_SECURITY_UNSUPPORTED},
        "copy-server-cookie": DICT_COPIED,
    })
    dictOutcome = x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    assert helper.flistOperations() == ["generate", "copy-server-cookie"]
    assert dictOutcome["sFallbackCause"] == x11Authority.S_OUTCOME_SECURITY_UNSUPPORTED
    dictEntry = x11Authorizations.fdictReadAuthorizations()[S_NAME]
    assert dictEntry["sTrustLevel"] == x11Authorizations.S_TRUST_SERVER_COOKIE
    assert x11Authorizations.flistDescribeAuthorization(S_NAME) == [
        x11Authorizations.S_SERVER_COOKIE_NOTICE,
    ]


@pytest.mark.falsification
def testATransientFailureNeverFallsBackToTheServerCookie(fixtureX11Home, monkeypatch):
    """The fallback is full display access; an unreachable server earns a retry, not that.

    Kills: falling back to the server's cookie when the server could not be reached.
    """
    helper = _fhelperInstall(monkeypatch, {
        "generate": {"sOutcome": x11Authority.S_OUTCOME_SERVER_UNREACHABLE,
                     "sReason": "the trusted connection was refused"},
        "copy-server-cookie": DICT_COPIED,
    })
    dictOutcome = x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    assert not x11Authorizations.fbAuthorizationUsable(dictOutcome)
    assert helper.flistOperations() == ["generate"]
    listLines = x11Authorizations.flistDescribeAuthorization(S_NAME)
    assert len(listLines) == 1 and "could not be set up" in listLines[0]
    assert "serverUnreachable" in listLines[0]


def testAReleaseRevokesTheRecordedIdThenForgetsTheContainer(fixtureX11Home, monkeypatch):
    helper = _fhelperInstall(monkeypatch, {
        "generate": DICT_GENERATED,
        "revoke": {"sOutcome": x11Authority.S_OUTCOME_REVOKED, "iAuthorizationId": 554},
    })
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    sDirectory = x11Authorizations.fdictReadAuthorizations()[S_NAME]["sDirectory"]
    dictOutcome = x11Authorizations.fdictReleaseAuthorization(S_NAME)
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_REVOKED
    listRevoke = helper.listCalls[-1]
    assert listRevoke[listRevoke.index("--authorization-id") + 1] == "554"
    assert listRevoke[listRevoke.index("--directory") + 1] == sDirectory
    assert not os.path.exists(sDirectory)
    assert S_NAME not in x11Authorizations.fdictReadAuthorizations()


@pytest.mark.falsification
def testAReleaseAgainstAnUnreachableServerKeepsTheRecordForARetry(fixtureX11Home, monkeypatch):
    """Kills: dropping the record although the server could not be asked to revoke."""
    _fhelperInstall(monkeypatch, {
        "generate": DICT_GENERATED,
        "revoke": {"sOutcome": x11Authority.S_OUTCOME_SERVER_UNREACHABLE},
    })
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    x11Authorizations.fdictReleaseAuthorization(S_NAME)
    dictEntry = x11Authorizations.fdictReadAuthorizations()[S_NAME]
    assert dictEntry["bReleasePending"] is True
    assert os.path.isdir(dictEntry["sDirectory"])


def testACopiedServerCookieHasNothingToRevoke(fixtureX11Home, monkeypatch):
    helper = _fhelperInstall(monkeypatch, {
        "generate": {"sOutcome": x11Authority.S_OUTCOME_LIBRARY_MISSING},
        "copy-server-cookie": DICT_COPIED,
    })
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    dictOutcome = x11Authorizations.fdictReleaseAuthorization(S_NAME)
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_GONE
    assert "revoke" not in helper.flistOperations()
    assert S_NAME not in x11Authorizations.fdictReadAuthorizations()


def testReleasingAnUnknownContainerIsGoneNotAnError(fixtureX11Home, monkeypatch):
    helper = _fhelperInstall(monkeypatch, {})
    assert x11Authorizations.fdictReleaseAuthorization("never")["sOutcome"] == (
        x11Authority.S_OUTCOME_GONE
    )
    x11Authorizations.fnReleaseAuthorizationQuietly("never")
    assert helper.listCalls == []


@pytest.mark.falsification
def testARefusedProbeRenewsAndNeverRevokesTheOldId(fixtureX11Home, monkeypatch):
    """After a server restart the old id may be another container's live authorization.

    Kills: leaving a refused cookie in place, so the container keeps a display it cannot use.
    """
    helper = _fhelperInstall(monkeypatch, {
        "generate": DICT_GENERATED,
        "probe": {"sOutcome": x11Authority.S_OUTCOME_REFUSED},
    })
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    helper.dictAnswers["generate"] = dict(DICT_GENERATED, iAuthorizationId=555)
    dictOutcome = x11Authorizations.fdictRenewIfGone(S_NAME)
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_GENERATED
    assert helper.flistOperations() == ["generate", "probe", "generate"]
    dictEntry = x11Authorizations.fdictReadAuthorizations()[S_NAME]
    assert dictEntry["iAuthorizationId"] == 555
    assert dictEntry["bRenewed"] is True
    assert x11Authorizations.flistDescribeAuthorization(S_NAME) == [
        "X11 access renewed after the X server restarted.",
    ]


def testAnAcceptedProbeChangesNothing(fixtureX11Home, monkeypatch):
    helper = _fhelperInstall(monkeypatch, {
        "generate": DICT_GENERATED,
        "probe": {"sOutcome": x11Authority.S_OUTCOME_ACCEPTED},
    })
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    assert x11Authorizations.fdictRenewIfGone(S_NAME)["sOutcome"] == (
        x11Authority.S_OUTCOME_ACCEPTED
    )
    assert helper.flistOperations() == ["generate", "probe"]
    assert x11Authorizations.flistDescribeAuthorization(S_NAME) == []


def testReconcileReleasesTheGoneAndRenewsTheLiving(fixtureX11Home, monkeypatch):
    helper = _fhelperInstall(monkeypatch, {
        "generate": DICT_GENERATED,
        "probe": {"sOutcome": x11Authority.S_OUTCOME_ACCEPTED},
        "revoke": {"sOutcome": x11Authority.S_OUTCOME_REVOKED, "iAuthorizationId": 554},
    })
    x11Authorizations.fdictPrepareAuthorization("living", S_UUID, ":0", ":0", "0")
    x11Authorizations.fdictPrepareAuthorization("gone", "b" * 32, ":0", ":0", "0")
    dictOutcomes = x11Authorizations.fdictReconcile(lambda sName: sName == "living")
    assert dictOutcomes == {
        "gone": x11Authority.S_OUTCOME_REVOKED, "living": x11Authority.S_OUTCOME_ACCEPTED,
    }
    assert sorted(x11Authorizations.fdictReadAuthorizations()) == ["living"]
    assert helper.flistOperations()[-2:] == ["revoke", "probe"]


@pytest.mark.falsification
def testAContainerNameThatIsNotOneIsRefused(fixtureX11Home):
    """Kills: building the authorization directory from an unvalidated name."""
    with pytest.raises(ValueError):
        x11Authorizations.fsAuthorizationDirectory("../escape")
    with pytest.raises(ValueError):
        x11Authorizations.fsAuthorizationDirectory("")


def testTheRealHelperAnswersThroughItsOwnProcess(fixtureX11Home):
    """The subprocess contract, on the one operation that needs no X server."""
    sAuthority = str(fixtureX11Home / "serverAuthority")
    with open(sAuthority, "wb") as fileAuthority:
        fileAuthority.write(x11Authority.fbaRenderWildcardAuthority("0", b"c" * 16))
    sDirectory = x11Authorizations.fsAuthorizationDirectory(S_NAME)
    dictOutcome = x11Authorizations.fdictRunHelper([
        "copy-server-cookie", "--directory", sDirectory, "--number", "0",
        "--authority", sAuthority,
    ])
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_COPIED
    assert x11Authority.fbaFindServerCookie(
        os.path.join(sDirectory, x11Authority.S_COOKIE_FILE_NAME), "0",
    ) == b"c" * 16


def testAHelperThatExitsNonzeroIsAnErrorOutcomeNotAnException(fixtureX11Home):
    dictOutcome = x11Authorizations.fdictRunHelper(["copy-server-cookie"])
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_ERROR
    assert "exited 2" in dictOutcome["sReason"]


def testAHelperThatTimesOutIsAnErrorOutcome(fixtureX11Home, monkeypatch):
    import subprocess

    def fnHang(*listArguments, **dictKeywords):
        raise subprocess.TimeoutExpired(cmd="helper", timeout=0.1)

    monkeypatch.setattr(x11Authorizations.subprocess, "run", fnHang)
    dictOutcome = x11Authorizations.fdictRunHelper(["probe", "--directory", "/x"], 0.1)
    assert dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_ERROR
    assert "did not answer" in dictOutcome["sReason"]


def testTheRecordFileNeverHoldsACookieValue(fixtureX11Home, monkeypatch):
    _fhelperInstall(monkeypatch, {"generate": DICT_GENERATED})
    x11Authorizations.fdictPrepareAuthorization(S_NAME, S_UUID, ":0", ":0", "0")
    sDirectory = x11Authorizations.fdictReadAuthorizations()[S_NAME]["sDirectory"]
    x11Authority.fnWriteCookieFileAtomically(sDirectory, b"\x01" * 16)
    with open(os.path.join(x11Authorizations.fsX11Root(), "authorizations.json")) as fileRecord:
        sRecord = fileRecord.read()
    json.loads(sRecord)
    assert "\\u0001" not in sRecord and "Xauthority" not in sRecord


@pytest.mark.falsification
def testTheContainerNameRuleRefusesATraversal():
    """Kills: accepting any string as a container name."""
    for sName in ("../escape", "", "a b", "/absolute", "x" * 129):
        with pytest.raises(ValueError):
            x11Authorizations._fnValidateContainerName(sName)
    x11Authorizations._fnValidateContainerName("demo_env.1-a")
