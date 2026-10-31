"""Level 2 asks what a READER can retrieve, so the verify asks as one.

The paper's Level 2 table lists "GitHub repository is public" and
"Zenodo archive is public" as required rows, and until 2026-10-30
nothing checked either. The GitHub hash fetch sends the researcher's
token, so a PRIVATE repository whose files matched reached Level 2; a
restricted Zenodo record answers a reader 200 with an empty file list,
so a lone one read "every file diverged, publish a new deposit" -- an
immutable version that left it restricted -- while an open record
beside a restricted extra one passed outright.

Every server here is a real socket on loopback, its answers shaped from
the anonymous requests observed live that day (``remoteAccessServer``).
The observers' real functions are imported at module load, because the
suite's autouse guard answers both "public" for every other test.
"""

import hashlib
import json
import os
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tests.remoteAccessServer import (  # noqa: F401  (fixture)
    fbaJson,
    fdictGithubRepository,
    fixtureRemoteAccessServer,
)
from tests.syncStatusFixtures import fdictBuildCachedVerify, fsRecentVerifyIso
from vaibify.reproducibility import githubMirror, scheduledReverify, zenodoClient
from vaibify.reproducibility.githubMirror import (
    fdictProbePublicAccess as fdictProbeUnpatched,
)
from vaibify.reproducibility.zenodoClient import (
    fdictObserveRecord as fdictObserveRecordUnpatched,
)


BA_DATA = b"a,b\n1,2\n"
S_DATA_SHA = hashlib.sha256(BA_DATA).hexdigest()
S_TOKEN = "ghp_" + "T" * 36


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fixtureGithubOnLoopback(monkeypatch, fixtureRemoteAccessServer):
    """Point the REAL probe at the loopback server, with a token on hand.

    The keyring answers a token, so a probe that consulted it would send
    one: the guard under test is that the probe's code path never asks.
    """
    monkeypatch.setattr(
        githubMirror, "fdictProbePublicAccess", fdictProbeUnpatched)
    monkeypatch.setattr(
        githubMirror, "_S_API_HOST", fixtureRemoteAccessServer.sBaseUrl)
    monkeypatch.setattr(
        githubMirror, "fsResolveToken", lambda sSlot: S_TOKEN)
    githubMirror.fnClearAnonymousBackoff()
    yield fixtureRemoteAccessServer
    githubMirror.fnClearAnonymousBackoff()


@pytest.fixture
def fixtureZenodoOnLoopback(monkeypatch, fixtureRemoteAccessServer):
    """Point the sandbox service, and the REAL observer, at loopback."""
    monkeypatch.setitem(
        zenodoClient._SERVICES, "sandbox", fixtureRemoteAccessServer.sBaseUrl)
    monkeypatch.setattr(
        zenodoClient, "fdictObserveRecord", fdictObserveRecordUnpatched)
    return fixtureRemoteAccessServer


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """A project repository holding one declared output."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"))
    with open(os.path.join(sRepo, "step01", "data.csv"), "wb") as fileOut:
        fileOut.write(BA_DATA)
    return sRepo


def _fdictWorkflow(sRepo, dictRemotes):
    return {
        "sWorkflowId": "wf01", "sProjectRepoPath": sRepo,
        "dictRemotes": dictRemotes,
        "listSteps": [{
            "sDirectory": "step01", "saOutputDataFiles": ["data.csv"],
            "saPlotFiles": [],
        }],
    }


def _fdictZenodoRemote(*listRecordIds):
    """Declare the first id as the primary record and the rest as extras."""
    return {"zenodo": {
        "sRecordId": listRecordIds[0], "sService": "sandbox",
        "sDoi": "10.5072/zenodo." + listRecordIds[0],
        "listRecords": [{"sRecordId": s} for s in listRecordIds[1:]],
    }}


def _fdictGithubRemote():
    return {"github": {
        "sOwner": "someone", "sRepo": "something", "sBranch": "main",
    }}


def _fnServePublicRepository(serverAccess):
    serverAccess.fnServe(
        "/repos/someone/something", 200,
        fbaJson(fdictGithubRepository("someone/something")),
        {"X-RateLimit-Limit": "60"},
    )


def _fnWriteGreenCache(sRepo, sService):
    """Leave a previous, successful record for a verify to keep or replace."""
    dictGreen = fdictBuildCachedVerify(
        sService=sService, listComparedPaths=["step01/data.csv"],
        sLastVerified="2026-10-29T12:00:00Z",
    )
    scheduledReverify.fnWriteSyncStatus(sRepo, dictGreen)
    return dictGreen


def _fdictReadCachedEntry(sRepo, sService):
    sPath = os.path.join(sRepo, ".vaibify", "syncStatus.json")
    with open(sPath, encoding="utf-8") as fileIn:
        return json.load(fileIn)[sService]


def _flistAuthorizationHeaders(serverAccess):
    return [
        dictRequest["dictHeaders"]["authorization"]
        for dictRequest in serverAccess.listRequests
        if "authorization" in dictRequest["dictHeaders"]
    ]


# ── the GitHub probe ─────────────────────────────────────────────────


@pytest.mark.falsification
def test_the_github_probe_sends_no_credential(fixtureGithubOnLoopback):
    """A reader has no login, so the probe must not carry one.

    The keyring here ANSWERS a token, so the only thing keeping it off
    the wire is the probe's own code path, which builds its request
    from a builder that takes no token.

    Kills: fdictProbePublicAccess building its request with
    _frequestBuildGithub and the researcher's token.
    """
    _fnServePublicRepository(fixtureGithubOnLoopback)
    dictAccess = fdictProbeUnpatched("someone", "something")
    assert dictAccess["sState"] == "public"
    assert len(fixtureGithubOnLoopback.listRequests) == 1
    assert _flistAuthorizationHeaders(fixtureGithubOnLoopback) == [], (
        "the public-access probe sent a credential, so it measured what "
        "the researcher is served rather than what a reader is"
    )


def test_a_private_repository_reads_not_public_with_its_remedy(
    fixtureGithubOnLoopback,
):
    """GitHub answers a reader 404 for a private repository."""
    fixtureGithubOnLoopback.fnServe("/repos/someone/something", 404, b"{}")
    dictAccess = fdictProbeUnpatched("someone", "something")
    assert dictAccess["sState"] == "not-public"
    assert "Change visibility" in dictAccess["sReason"]
    assert "someone/something" in dictAccess["sReason"]


def test_a_legally_withheld_repository_is_not_public_and_says_why(
    fixtureGithubOnLoopback,
):
    """451 is a legal restriction; visibility is not the remedy."""
    fixtureGithubOnLoopback.fnServe("/repos/someone/something", 451, b"{}")
    dictAccess = fdictProbeUnpatched("someone", "something")
    assert dictAccess["sState"] == "not-public"
    assert "legal" in dictAccess["sReason"]
    assert "will not restore" in dictAccess["sReason"]


def test_a_renamed_repository_is_uncheckable_and_names_the_new_name(
    fixtureGithubOnLoopback,
):
    """An answer about another repository is no verdict about this one."""
    fixtureGithubOnLoopback.fnServe(
        "/repos/someone/something", 200,
        fbaJson(fdictGithubRepository("someone/renamed")),
    )
    with pytest.raises(githubMirror.GithubMirrorError, match="someone/renamed"):
        fdictProbeUnpatched("someone", "something")


@pytest.mark.parametrize("dictBody", [
    {"full_name": "someone/something", "private": True},
    {"full_name": "someone/something"},
    {"full_name": "someone/something", "private": "false"},
])
def test_a_body_that_does_not_say_private_false_is_uncheckable(
    fixtureGithubOnLoopback, dictBody,
):
    """``public`` needs positive evidence, never an absence."""
    fixtureGithubOnLoopback.fnServe(
        "/repos/someone/something", 200, fbaJson(dictBody))
    with pytest.raises(githubMirror.GithubMirrorError, match="did not say"):
        fdictProbeUnpatched("someone", "something")


@pytest.mark.parametrize("iStatus", [500, 502, 401])
def test_a_status_that_says_neither_is_uncheckable(
    fixtureGithubOnLoopback, iStatus,
):
    fixtureGithubOnLoopback.fnServe("/repos/someone/something", iStatus, b"{}")
    with pytest.raises(githubMirror.GithubMirrorError, match=str(iStatus)):
        fdictProbeUnpatched("someone", "something")


def test_an_unreachable_github_is_uncheckable(monkeypatch):
    """A refused connection is no claim about the repository."""
    monkeypatch.setattr(githubMirror, "_S_API_HOST", "http://127.0.0.1:9")
    githubMirror.fnClearAnonymousBackoff()
    with pytest.raises(githubMirror.GithubMirrorError, match="could not reach"):
        fdictProbeUnpatched("someone", "something")


@pytest.mark.falsification
def test_rate_limits_are_uncheckable_never_not_public(fixtureGithubOnLoopback):
    """A secondary-limit 403 and a 429 are "ask later", not "private".

    The 403 carries no ``X-RateLimit-Remaining: 0`` -- GitHub does not
    promise one on a secondary limit -- so a classifier that knew only
    the primary limit, or that read every 403 as a refusal to a reader,
    would turn a busy network address into a private repository.

    Kills: _fdictJudgeProbeRefusal reading a 403 as not-public.
    """
    fixtureGithubOnLoopback.fnServe(
        "/repos/someone/something", 403,
        b'{"message": "You have exceeded a secondary rate limit."}',
    )
    with pytest.raises(githubMirror.GithubMirrorError, match="wait"):
        fdictProbeUnpatched("someone", "something")
    githubMirror.fnClearAnonymousBackoff()
    fixtureGithubOnLoopback.fnServe(
        "/repos/someone/something", 429, b"{}", {"Retry-After": "30"},
    )
    with pytest.raises(githubMirror.GithubMirrorError, match="wait"):
        fdictProbeUnpatched("someone", "something")


@pytest.mark.falsification
def test_the_backoff_never_blocks_and_never_spends(
    monkeypatch, fixtureGithubOnLoopback,
):
    """Inside the deadline a probe answers at once and sends nothing.

    The anonymous budget is shared by the network address, so a probe
    sent during the backoff spends what the next real check needs; and
    the probe runs in a verify worker, where a sleep holds a thread for
    nothing. ``time.sleep`` fails if called, and the server counts.

    Kills: removing the deadline check at the top of
    fdictProbePublicAccess, which sends the second request.
    """
    fixtureGithubOnLoopback.fnServe(
        "/repos/someone/something", 429, b"{}", {"Retry-After": "120"},
    )
    with pytest.raises(githubMirror.GithubMirrorError):
        fdictProbeUnpatched("someone", "something")
    assert len(fixtureGithubOnLoopback.listRequests) == 1
    _fnServePublicRepository(fixtureGithubOnLoopback)

    def fnRefuseToSleep(fSeconds):
        raise AssertionError(f"the probe slept {fSeconds}s in a worker")

    monkeypatch.setattr(time, "sleep", fnRefuseToSleep)
    with pytest.raises(githubMirror.GithubMirrorError, match="UTC"):
        fdictProbeUnpatched("someone", "something")
    assert len(fixtureGithubOnLoopback.listRequests) == 1, (
        "a probe inside the backoff reached GitHub and spent the budget"
    )


def _ferrorHttp(iCode, dictHeaders):
    headersFake = MagicMock()
    headersFake.get = lambda sKey, sDefault="": dictHeaders.get(sKey, sDefault)
    errorHttp = urllib.error.HTTPError(
        "https://api.github.com/repos/someone/something", iCode, "refused",
        headersFake, None,
    )
    errorHttp.headers = headersFake
    return errorHttp


def test_the_deadline_follows_github_s_documented_order():
    """retry-after, then the reset when the count is spent, then a minute."""
    fNow = 1_000_000.0
    assert githubMirror._ffComputeBackoffDeadline(
        _ferrorHttp(429, {"Retry-After": "45",
                          "X-RateLimit-Remaining": "0",
                          "X-RateLimit-Reset": str(fNow + 900)}),
        fNow) == fNow + 45
    assert githubMirror._ffComputeBackoffDeadline(
        _ferrorHttp(403, {"X-RateLimit-Remaining": "0",
                          "X-RateLimit-Reset": str(fNow + 900)}),
        fNow) == fNow + 900
    assert githubMirror._ffComputeBackoffDeadline(
        _ferrorHttp(403, {"X-RateLimit-Reset": str(fNow + 900)}),
        fNow) == fNow + 60


def test_a_deadline_beyond_github_s_window_is_bounded():
    """A reset days away is a clock fault, not a reason to stop checking."""
    fNow = 1_000_000.0
    assert githubMirror._ffComputeBackoffDeadline(
        _ferrorHttp(429, {"Retry-After": str(10 * 86400)}), fNow,
    ) == fNow + 3600


@pytest.mark.parametrize("iCode, dictHeaders, sBody", [
    (429, {}, ""),
    (403, {}, "You have exceeded a secondary rate limit"),
    (403, {"Retry-After": "60"}, ""),
])
def test_the_authenticated_fetch_names_secondary_limits_too(
    iCode, dictHeaders, sBody,
):
    """One classifier: the hash fetch used to miss secondary limits and 429."""
    import io
    errorHttp = _ferrorHttp(iCode, dictHeaders)
    errorHttp.fp = io.BytesIO(sBody.encode("utf-8"))
    errorHttp.read = errorHttp.fp.read
    with pytest.raises(githubMirror.GithubMirrorError, match="rate limit"):
        githubMirror._fnRaiseClassifiedHttpError(
            errorHttp, "https://raw.githubusercontent.com/o/r/s/x")


def test_a_plain_refusal_is_not_called_a_rate_limit():
    errorHttp = _ferrorHttp(403, {})
    with pytest.raises(githubMirror.GithubMirrorError) as excInfo:
        githubMirror._fnRaiseClassifiedHttpError(
            errorHttp, "https://raw.githubusercontent.com/o/r/s/x")
    assert "rate limit" not in str(excInfo.value).lower()


# ── the GitHub verify records access and identity ────────────────────


def _fdictVerifyGithub(sRepo, dictWorkflow):
    with patch.object(
        githubMirror, "fdictFetchRemoteHashes",
        return_value={"step01/data.csv": S_DATA_SHA},
    ):
        return scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "github", sNowIso=fsRecentVerifyIso(),
        )


def test_the_github_verify_records_access_and_the_remote_it_checked(
    fixtureGithubOnLoopback, fixtureProjectRepo,
):
    _fnServePublicRepository(fixtureGithubOnLoopback)
    dictStatus = _fdictVerifyGithub(
        fixtureProjectRepo,
        _fdictWorkflow(fixtureProjectRepo, _fdictGithubRemote()),
    )
    assert dictStatus["dictPublicAccess"]["sState"] == "public"
    assert dictStatus["dictRemoteIdentity"] == {
        "sOwner": "someone", "sRepo": "something", "sBranch": "main",
    }
    assert dictStatus["iMatching"] == dictStatus["iTotalFiles"] == 1


def test_a_private_repository_is_recorded_with_its_comparison(
    fixtureGithubOnLoopback, fixtureProjectRepo,
):
    """Not public is a RESULT: the record is written, files compared."""
    fixtureGithubOnLoopback.fnServe("/repos/someone/something", 404, b"{}")
    dictStatus = _fdictVerifyGithub(
        fixtureProjectRepo,
        _fdictWorkflow(fixtureProjectRepo, _fdictGithubRemote()),
    )
    assert dictStatus["dictPublicAccess"]["sState"] == "not-public"
    assert dictStatus["listComparedPaths"] == ["step01/data.csv"]


def test_an_uncheckable_probe_writes_nothing_over_the_last_record(
    fixtureGithubOnLoopback, fixtureProjectRepo,
):
    fixtureGithubOnLoopback.fnServe("/repos/someone/something", 503, b"{}")
    dictGreen = _fnWriteGreenCache(fixtureProjectRepo, "github")
    with patch.object(
        githubMirror, "fdictFetchRemoteHashes",
        return_value={"step01/data.csv": S_DATA_SHA},
    ):
        dictResult = scheduledReverify.fdictAttemptOneVerify(
            fixtureProjectRepo,
            _fdictWorkflow(fixtureProjectRepo, _fdictGithubRemote()),
            "github", fsRecentVerifyIso(),
        )
    assert dictResult["sStatus"] == "error"
    assert "503" in dictResult["sError"]
    assert _fdictReadCachedEntry(fixtureProjectRepo, "github") == dictGreen


# ── one Zenodo observation per record ────────────────────────────────


def test_an_open_record_is_public_and_holds_its_files(fixtureZenodoOnLoopback):
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "open", {"data.csv": BA_DATA})
    dictObservation = fdictObserveRecordUnpatched("1", "sandbox")
    assert dictObservation["sState"] == "public"
    assert [d["key"] for d in dictObservation["dictRecord"]["files"]] == [
        "data.csv"]


@pytest.mark.parametrize("sAccessRight", ["restricted", "closed"])
def test_a_restricted_record_is_not_public_with_its_remedy(
    fixtureZenodoOnLoopback, sAccessRight,
):
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", sAccessRight)
    dictObservation = fdictObserveRecordUnpatched("1", "sandbox")
    assert dictObservation["sState"] == "not-public"
    assert "set its files to public" in dictObservation["sReason"]
    assert "DOI does not change" in dictObservation["sReason"]


def test_an_embargoed_record_names_its_date_and_is_not_public(
    fixtureZenodoOnLoopback,
):
    """The date is named; only a later observation of "open" grants access."""
    fixtureZenodoOnLoopback.fnServeZenodoRecord(
        "1", "embargoed", sEmbargoDate="2027-01-31")
    dictObservation = fdictObserveRecordUnpatched("1", "sandbox")
    assert dictObservation["sState"] == "not-public"
    assert "2027-01-31" in dictObservation["sReason"]


@pytest.mark.parametrize("iStatus", [401, 403, 404, 410])
def test_a_record_zenodo_will_not_serve_is_not_public(
    fixtureZenodoOnLoopback, iStatus,
):
    fixtureZenodoOnLoopback.fnServe("/api/records/1", iStatus, b"{}")
    dictObservation = fdictObserveRecordUnpatched("1", "sandbox")
    assert dictObservation["sState"] == "not-public"
    assert "does not serve record 1" in dictObservation["sReason"]


@pytest.mark.parametrize("iStatus, baBody", [
    (429, b"{}"), (500, b"{}"), (503, b"{}"),
    (200, fbaJson({"metadata": {"access_right": "something-new"}})),
    (200, b"not json"),
])
def test_an_answer_that_says_neither_is_unchecked(
    fixtureZenodoOnLoopback, iStatus, baBody,
):
    fixtureZenodoOnLoopback.fnServe("/api/records/1", iStatus, baBody)
    assert fdictObserveRecordUnpatched("1", "sandbox")["sState"] == "unchecked"


def test_an_unreachable_zenodo_is_unchecked(monkeypatch):
    monkeypatch.setitem(zenodoClient._SERVICES, "sandbox", "http://127.0.0.1:9")
    dictObservation = fdictObserveRecordUnpatched("1", "sandbox")
    assert dictObservation["sState"] == "unchecked"
    assert "could not reach Zenodo" in dictObservation["sReason"]


# ── the Zenodo verify, end to end ────────────────────────────────────


def _fdictAttemptZenodo(sRepo, *listRecordIds):
    return scheduledReverify.fdictAttemptOneVerify(
        sRepo, _fdictWorkflow(sRepo, _fdictZenodoRemote(*listRecordIds)),
        "zenodo", fsRecentVerifyIso(),
    )


@pytest.mark.falsification
def test_the_zenodo_verify_sends_no_credential(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    """Every request of a full Zenodo verify goes out without a login.

    The verify was tokenless before the access question existed, but
    nothing pinned it. Here the record is open and serves the file, so
    the verify reaches every read it makes -- observation, file hash and
    attestation lookup -- and each is inspected on the wire.

    Kills: _fclientAnonymous building its client with a token.
    """
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "open", {"data.csv": BA_DATA})
    dictResult = _fdictAttemptZenodo(fixtureProjectRepo, "1")
    assert dictResult["sStatus"] == "ok", dictResult
    assert fixtureZenodoOnLoopback.flistRequestsTo(
        "/api/records/1/files/data.csv"), "the file was never hashed"
    assert _flistAuthorizationHeaders(fixtureZenodoOnLoopback) == [], (
        "the Zenodo verify sent a credential, so it measured what the "
        "researcher is served rather than what a reader is"
    )


@pytest.mark.falsification
def test_a_mixed_archive_withholds_the_comparison(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    """An open record serving every file does not excuse a restricted one.

    Under any-record-agrees merging, record 1 supplied every file and
    record 2 contributed nothing, so the archive passed Level 2 while a
    reader could not open half of it. Now no file is compared, nothing
    is hashed, and the record names the restricted deposit.

    Kills: _flistObserveZenodoRecords observing only the primary record.
    """
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "open", {"data.csv": BA_DATA})
    fixtureZenodoOnLoopback.fnServeZenodoRecord("2", "restricted")
    dictResult = _fdictAttemptZenodo(fixtureProjectRepo, "1", "2")
    assert dictResult["sStatus"] == "ok", dictResult
    dictEntry = _fdictReadCachedEntry(fixtureProjectRepo, "zenodo")
    assert dictEntry["bComparisonWithheld"] is True
    assert dictEntry["dictPublicAccess"]["sState"] == "not-public"
    assert "record 2" in dictEntry["dictPublicAccess"]["sReason"]
    assert dictEntry["listComparedPaths"] == []
    assert dictEntry["listDiverged"] == []
    assert dictEntry["dictArchivedAttestation"] is None
    assert fixtureZenodoOnLoopback.flistRequestsTo(
        "/api/records/1/files/data.csv") == [], "a withheld archive was hashed"


def test_every_per_record_verdict_is_kept(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    """The record keeps each declared record's verdict, unchecked included."""
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "restricted")
    fixtureZenodoOnLoopback.fnServe("/api/records/2", 429, b"{}")
    _fdictAttemptZenodo(fixtureProjectRepo, "1", "2")
    dictEntry = _fdictReadCachedEntry(fixtureProjectRepo, "zenodo")
    assert [
        (d["sRecordId"], d["sState"])
        for d in dictEntry["dictPublicAccess"]["listRecords"]
    ] == [("1", "not-public"), ("2", "unchecked")]


@pytest.mark.falsification
@pytest.mark.parametrize("tDeclaredOrder", [("1", "2"), ("2", "1")])
def test_a_known_restriction_survives_an_uncheckable_sibling(
    fixtureZenodoOnLoopback, fixtureProjectRepo, tDeclaredOrder,
):
    """Record 1 restricted, record 2 rate-limited: the restriction is kept.

    Whatever record 2 would have said, the archive is not public, so
    the verify records that rather than lose it to the sibling's 429 --
    in both declared orders, because the verdict must not depend on
    which record was asked first.

    Kills: _fdictJudgeZenodoArchiveAccess raising on an unchecked record
    before it records a known restriction.
    """
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "restricted")
    fixtureZenodoOnLoopback.fnServe("/api/records/2", 429, b"{}")
    _fnWriteGreenCache(fixtureProjectRepo, "zenodo")
    dictResult = _fdictAttemptZenodo(fixtureProjectRepo, *tDeclaredOrder)
    assert dictResult["sStatus"] == "ok", dictResult
    dictEntry = _fdictReadCachedEntry(fixtureProjectRepo, "zenodo")
    assert dictEntry["dictPublicAccess"]["sState"] == "not-public"
    assert "record 1 restricts" in dictEntry["dictPublicAccess"]["sReason"]


@pytest.mark.falsification
def test_an_uncheckable_record_alone_keeps_the_previous_record(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    """Record 1 open, record 2 rate-limited: no verdict, nothing written.

    Record 1 serves every file, so a verify that compared anyway would
    write a fresh "matched" -- a claim about an archive half of which
    nobody could read.

    Kills: _fdictJudgeZenodoArchiveAccess treating unchecked records as
    public.
    """
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "open", {"data.csv": BA_DATA})
    fixtureZenodoOnLoopback.fnServe("/api/records/2", 429, b"{}")
    dictGreen = _fnWriteGreenCache(fixtureProjectRepo, "zenodo")
    dictResult = _fdictAttemptZenodo(fixtureProjectRepo, "1", "2")
    assert dictResult["sStatus"] == "error"
    assert "HTTP 429" in dictResult["sError"]
    assert _fdictReadCachedEntry(fixtureProjectRepo, "zenodo") == dictGreen


@pytest.mark.falsification
def test_each_record_is_fetched_once_so_a_404_cannot_abort_the_verdict(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    """The comparison and the attestation read share one observation.

    The attestation reader used to fetch every record again through the
    client, which re-raises 401, 403 and 404: a declared record Zenodo
    would not serve aborted the verify before its verdict was saved.
    Reading from the observations, each record is asked exactly once,
    and a 404 record's verdict is a result like any other.

    Kills: _fjsonFetchArchivedAttestation re-fetching each record
    through ZenodoClient.fjsonFetchRecordFile.
    """
    fixtureZenodoOnLoopback.fnServeZenodoRecord("1", "open", {"data.csv": BA_DATA})
    fixtureZenodoOnLoopback.fnServeZenodoRecord(
        "2", "open", {"l3_attestation.json": b'{"sStatus": "passed"}'})
    assert _fdictAttemptZenodo(fixtureProjectRepo, "1", "2")["sStatus"] == "ok"
    for sRecordId in ("1", "2"):
        assert len(fixtureZenodoOnLoopback.flistRequestsTo(
            f"/api/records/{sRecordId}")) == 1, (
            f"record {sRecordId} was fetched more than once in one verify")
    fixtureZenodoOnLoopback.fnServe("/api/records/2", 404, b"{}")
    dictResult = _fdictAttemptZenodo(fixtureProjectRepo, "1", "2")
    assert dictResult["sStatus"] == "ok", dictResult
    dictEntry = _fdictReadCachedEntry(fixtureProjectRepo, "zenodo")
    assert "does not serve record 2" in dictEntry["dictPublicAccess"]["sReason"]


def test_an_open_archive_records_its_identity_and_comparison(
    fixtureZenodoOnLoopback, fixtureProjectRepo,
):
    fixtureZenodoOnLoopback.fnServeZenodoRecord("2", "open", {"data.csv": BA_DATA})
    fixtureZenodoOnLoopback.fnServeZenodoRecord("10", "open")
    _fdictAttemptZenodo(fixtureProjectRepo, "2", "10")
    dictEntry = _fdictReadCachedEntry(fixtureProjectRepo, "zenodo")
    assert dictEntry["bComparisonWithheld"] is False
    assert dictEntry["dictPublicAccess"]["sState"] == "public"
    assert dictEntry["dictRemoteIdentity"] == {
        "sEndpoint": "sandbox", "listRecordIds": ["10", "2"],
    }
    assert dictEntry["iMatching"] == dictEntry["iTotalFiles"] == 1


def test_a_cache_written_before_the_question_reads_its_access_as_absent(
    fixtureProjectRepo,
):
    """Absent is not public: the backfill says nobody asked."""
    dictOld = fdictBuildCachedVerify(sService="github")
    scheduledReverify.fnWriteSyncStatus(fixtureProjectRepo, dictOld)
    dictRead = scheduledReverify.fdictReadCachedSyncStatus(
        fixtureProjectRepo, "github")
    assert dictRead["dictPublicAccess"] is None
    assert dictRead["dictRemoteIdentity"] is None
