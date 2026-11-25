"""Every credential failure is classed, and the class decides the remedy.

Source: ``vaibify/config/credentialFailures.py``, the classifiers in
``overleafAuth``, ``zenodoClient`` and ``githubAuth``, and the CLI's
GitHub refusal in ``commandCredential``.

The expected classes come from what the services actually answered, not
from the code under test. Recorded on 2026-11-24 with FAKE credentials:
git.overleaf.com answered a wrong token for a real-shaped project ID
with exit 128 and "fatal: Authentication failed for '<url>'", and a
project ID naming no project with "remote: This Overleaf project does
not exist." and "fatal: repository '<url>' not found";
sandbox.zenodo.org answered a fake bearer token on the deposit listing
with 403 and the body below, byte for byte the answer it gives no token
at all; api.github.com answered a fake token on /user with 401 "Bad
credentials". GitHub's spent rate limit (403 or 429 with
``X-RateLimit-Remaining: 0``) is from its REST documentation and was not
provoked.
"""

import io
import urllib.error
from unittest.mock import patch

import pytest
import requests
from click.testing import CliRunner

from vaibify.config import credentialFailures as failures
from vaibify.reproducibility import githubAuth, overleafAuth, zenodoClient


S_OVERLEAF_WRONG_TOKEN = (
    "remote: Enter your Git authentication token when prompted for a password.\n"
    "fatal: Authentication failed for 'https://git.overleaf.com/000000000000000000000000/'"
)
S_OVERLEAF_NO_SUCH_PROJECT = (
    "remote: This Overleaf project does not exist.\n"
    "fatal: repository 'https://git.overleaf.com/abc123proj/' not found"
)
S_ZENODO_FORBIDDEN_BODY = '{"status": 403, "message": "Permission denied."}'
LIST_EVERY_CLASS = [
    getattr(failures, sName) for sName in failures.__all__
    if sName.startswith("S_CLASS_")
]


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def testOnlyAMissingOrRejectedTokenLeadsWithStoringOne():
    """Store or Replace leads only where the token is known to be the problem."""
    dictLeading = {
        sClass: (failures.flistActionsForClass(sClass) or [""])[0]
        for sClass in LIST_EVERY_CLASS
    }
    assert dictLeading[failures.S_CLASS_TOKEN_MISSING] == failures.S_ACTION_STORE
    assert dictLeading[failures.S_CLASS_TOKEN_REJECTED] == failures.S_ACTION_REPLACE
    for sClass, sLeading in dictLeading.items():
        if sClass not in (failures.S_CLASS_TOKEN_MISSING, failures.S_CLASS_TOKEN_REJECTED):
            assert sLeading in (failures.S_ACTION_RETRY, ""), sClass


def testAnUnknownClassRaisesRatherThanGuessingItsButtons():
    with pytest.raises(KeyError):
        failures.flistActionsForClass("token-misplaced")


def testTheDetailIsRedactedButTheSentenceIsNot():
    """The redactor blanks any line naming a credential; sentences name them."""
    dictFailure = failures.fdictDescribeFailure(
        failures.S_CLASS_TOKEN_REJECTED,
        "The service rejected this token.",
        "remote: password=hunter2 was refused",
    )
    assert dictFailure["sMessage"].startswith("The service rejected this token.")
    assert "hunter2" not in dictFailure["sMessage"]


@pytest.mark.parametrize("sClass", LIST_EVERY_CLASS)
def testEveryClassHasAnOverleafAndAZenodoSentence(sClass):
    for dictFailure in (
        overleafAuth.fdictDescribeOverleafFailure(sClass),
        zenodoClient.fdictDescribeZenodoFailure(sClass, "sandbox"),
    ):
        assert dictFailure["sClass"] == sClass
        assert len(dictFailure["sMessage"]) > 20


# ---------------------------------------------------------------------------
# What each service actually said
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sStderr, sExpected", [
    (S_OVERLEAF_WRONG_TOKEN, failures.S_CLASS_TOKEN_REJECTED),
    (S_OVERLEAF_NO_SUCH_PROJECT, failures.S_CLASS_ACCESS_DENIED),
    ("fatal: unable to access 'https://git.overleaf.com/x/': "
     "Could not resolve host: git.overleaf.com", failures.S_CLASS_NO_ANSWER),
    ("fatal: unable to access: The requested URL returned error: 503",
     failures.S_CLASS_NO_ANSWER),
    ("fatal: unable to access: The requested URL returned error: 429",
     failures.S_CLASS_RATE_LIMITED),
    ("remote: something nobody has seen", failures.S_CLASS_SERVICE_REFUSED),
])
def testOverleafGitRefusalsAreClassedByWhatGitSaid(sStderr, sExpected):
    assert overleafAuth.fsClassifyOverleafGitFailure(sStderr) == sExpected


def testANonexistentOverleafProjectPointsAtTheIdNotTheToken():
    sMessage = overleafAuth.fdictDescribeOverleafFailure(
        overleafAuth.fsClassifyOverleafGitFailure(S_OVERLEAF_NO_SUCH_PROJECT),
    )["sMessage"]
    assert "Check the project ID" in sMessage


class _ZenodoAnswer:
    def __init__(self, iStatus, sText):
        self.status_code = iStatus
        self.text = sText
        self.headers = {}

    def json(self):
        return {}


@pytest.mark.parametrize("iStatus, sBody, sExpected", [
    (403, S_ZENODO_FORBIDDEN_BODY, failures.S_CLASS_TOKEN_REJECTED),
    (401, '{"status": 401}', failures.S_CLASS_TOKEN_REJECTED),
    (429, "slow down", failures.S_CLASS_RATE_LIMITED),
    (503, "unavailable", failures.S_CLASS_NO_ANSWER),
])
def testTheZenodoProbeClassesTheAnswerItGot(iStatus, sBody, sExpected):
    with patch.object(
        zenodoClient, "fresponseGetWithinAllowlist",
        return_value=_ZenodoAnswer(iStatus, sBody),
    ):
        dictProbe = zenodoClient.fdictProbeToken("sandbox", "a-fake-token")
    assert dictProbe["sClass"] == sExpected


def testAZenodoConnectionThatNeverOpenedIsNoAnswer():
    with patch.object(
        zenodoClient, "fresponseGetWithinAllowlist",
        side_effect=requests.ConnectionError("no route"),
    ):
        dictProbe = zenodoClient.fdictProbeToken("sandbox", "a-fake-token")
    assert dictProbe["sClass"] == failures.S_CLASS_NO_ANSWER
    assert dictProbe["sOutcome"] == zenodoClient.S_PROBE_UNDETERMINED


def testAZenodoRejectionNeverCarriesTheResponseBody():
    """A body can echo what was sent; only a missing answer keeps its detail."""
    sMessage = zenodoClient.fdictDescribeZenodoFailure(
        failures.S_CLASS_TOKEN_REJECTED, "sandbox", "body: ECHOED",
    )["sMessage"]
    assert "ECHOED" not in sMessage
    assert "deposit:write" in sMessage


def _ferrorFromGithub(iStatus, dictHeaders=None):
    return urllib.error.HTTPError(
        "https://api.github.com/user", iStatus, "status", dictHeaders or {},
        io.BytesIO(b'{"message": "Bad credentials"}'),
    )


@pytest.mark.parametrize("iStatus, dictHeaders, sExpected", [
    (401, {}, failures.S_CLASS_TOKEN_REJECTED),
    (403, {"X-RateLimit-Remaining": "0"}, failures.S_CLASS_RATE_LIMITED),
    (429, {}, failures.S_CLASS_RATE_LIMITED),
    (403, {"X-RateLimit-Remaining": "4999"}, failures.S_CLASS_ACCESS_DENIED),
    (502, {}, failures.S_CLASS_NO_ANSWER),
])
def testGithubUserLookupFailuresAreClassed(iStatus, dictHeaders, sExpected):
    sClass = githubAuth._fsClassifyUserEndpointFailure(
        _ferrorFromGithub(iStatus, dictHeaders),
    )
    assert sClass == sExpected


# ---------------------------------------------------------------------------
# GitHub's refusals name the credential and its remedy
# ---------------------------------------------------------------------------


def testNoGithubCredentialSendsTheResearcherToGhAuthLogin():
    sReason = githubAuth._fsBindingFailureReason("", "owner")
    assert "gh auth login" in sReason
    assert "Settings" not in sReason


def testARejectedPerRepositoryTokenIsNamedWithTheCommandThatRemovesOnlyIt():
    sSlot = "github_token:owner/repo"
    with patch.object(
        githubAuth, "_ftFetchLoginFresh",
        return_value=("", failures.S_CLASS_TOKEN_REJECTED),
    ):
        githubAuth.fnClearTokenLoginCache()
        sReason = githubAuth._fsBindingFailureReason("stale", "owner", sSlot)
    assert sSlot in sReason
    assert f"vaibify credential delete github --keyring-slot {sSlot}" in sReason


def testARateLimitIsNotCalledACredentialProblem():
    with patch.object(
        githubAuth, "_ftFetchLoginFresh",
        return_value=("", failures.S_CLASS_RATE_LIMITED),
    ):
        githubAuth.fnClearTokenLoginCache()
        sReason = githubAuth._fsBindingFailureReason("fine", "owner")
    assert "limiting requests" in sReason
    assert "not a problem with the credential" in sReason


@pytest.mark.falsification
def testClearingOneRepositorysSlotLeavesTheGhLoginInPlace():
    """A stale slot outranks gh; removing it must not also log gh out.

    Kills: logging out of gh even when a slot is named.
    """
    with patch.object(githubAuth.subprocess, "run") as mockRun, patch(
        "vaibify.config.secretManager.fnDeleteSecret",
    ):
        dictResult = githubAuth.fdictRevokeGitHubToken("github_token:owner/repo")
    mockRun.assert_not_called()
    assert dictResult["bLocalCleared"] is True


def testCredentialSetGithubRefusesBeforeAskingForAToken():
    from vaibify.cli.commandCredential import fnCredentialCommand
    resultCli = CliRunner().invoke(fnCredentialCommand, ["set", "github"], input="")
    assert resultCli.exit_code == 2
    assert "gh auth login" in resultCli.output
    assert "github token" not in resultCli.output.lower().replace(
        "no github token", "",
    )


def testAFailedPullSaysWhereTheOverleafTokenIsReplaced():
    from vaibify.gui.routes.syncRoutes import _fsDescribeManuscriptPullFailure
    sMessage = _fsDescribeManuscriptPullFailure(S_OVERLEAF_WRONG_TOKEN)
    assert sMessage.startswith("Manuscript pull failed. Overleaf rejected the token.")
    assert "Edit Overleaf settings" in sMessage


# ---------------------------------------------------------------------------
# The candidate checks themselves
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAZenodoCandidateIsAcceptedOnlyWhenZenodoAcceptsIt():
    """The recorded 403 answer must fail the check, never pass it.

    Kills: treating every probe answer as accepted.
    """
    with patch.object(
        zenodoClient, "fresponseGetWithinAllowlist",
        return_value=_ZenodoAnswer(403, S_ZENODO_FORBIDDEN_BODY),
    ):
        tRefused = zenodoClient.ftValidateZenodoCandidate("sandbox", "fake")
    with patch.object(
        zenodoClient, "fresponseGetWithinAllowlist",
        return_value=_ZenodoAnswer(200, "[]"),
    ):
        tAccepted = zenodoClient.ftValidateZenodoCandidate("sandbox", "fake")
    assert tRefused[0] == failures.S_CLASS_TOKEN_REJECTED
    assert tAccepted == ("", "")


@pytest.mark.falsification
def testAnOverleafCandidateReachesGitThroughAFileThatIsGoneAfterwards(tmp_path):
    """git is handed the candidate, never the stored token, and the file goes.

    The askpass git would run is executed here for real: asked for a
    password it must print the candidate, and asked for a user name,
    ``git``. Both files are removed once the check returns, and a refusal
    git reports is classed from git's own words.

    Kills: reporting a candidate as accepted whatever git answered.
    """
    import os
    import subprocess as subprocessReal
    import sys as sysReal
    from vaibify.gui import syncDispatcher
    dictSeen = {}

    def ftRunLsRemote(sProjectId, sAskpass):
        sToken = subprocessReal.run(
            [sysReal.executable, sAskpass, "Password for 'https://git@x':"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        sUser = subprocessReal.run(
            [sysReal.executable, sAskpass, "Username for 'https://x':"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dictSeen.update(sToken=sToken, sUser=sUser, sAskpass=sAskpass)
        return (False, S_OVERLEAF_WRONG_TOKEN)

    with patch.object(syncDispatcher, "_ftRunHostLsRemote", ftRunLsRemote):
        tResult = syncDispatcher.ftValidateOverleafCandidate(
            "64a1b2c3d4e5f6a7b8c9d0e1", "the-candidate",
        )
    assert (dictSeen["sToken"], dictSeen["sUser"]) == ("the-candidate", "git")
    assert not os.path.exists(dictSeen["sAskpass"])
    assert tResult[0] == failures.S_CLASS_TOKEN_REJECTED
    from vaibify.config.ephemeralStore import fsGetEphemeralRoot
    assert not [
        sName for sName in os.listdir(fsGetEphemeralRoot())
        if sName.startswith("vc_candidate_")
    ]
