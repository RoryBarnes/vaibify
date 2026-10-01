"""Zenodo and arXiv clients at their HTTP boundary.

Only the transport is replaced: ``zenodoClient.requests`` and
``arxivClient.requests`` become a recording double that answers by URL,
and ``arxivClient.time`` a clock the test drives. Everything the
clients do with an answer -- redirects, allowlists, byte caps, JSON
decoding, size comparisons, credential redaction -- runs for real.
"""

import json
import types

import pytest
import requests

from vaibify.reproducibility import arxivClient, zenodoClient


S_RECORD_URL = "https://sandbox.zenodo.org/api/records/777"
S_FILE_URL = "https://sandbox.zenodo.org/api/records/777/files/meta.json"


class _FakeResponse:
    """The slice of ``requests.Response`` the clients read."""

    def __init__(self, iStatus=200, jsonBody=None, baContent=b"",
                 dictHeaders=None, sText=None):
        self.status_code = iStatus
        self.headers = dict(dictHeaders or {})
        self._jsonBody = jsonBody
        self._baContent = baContent
        if sText is None:
            sText = json.dumps(jsonBody) if jsonBody is not None else ""
        self.text = sText

    def json(self):
        return self._jsonBody

    def iter_content(self, iChunkBytes):
        for iStart in range(0, len(self._baContent), iChunkBytes):
            yield self._baContent[iStart:iStart + iChunkBytes]

    def close(self):
        pass


class TransportDouble:
    """Answer each GET by URL from a table; record URL and headers."""

    def __init__(self):
        self.dictAnswers = {}
        self.fresponseDefault = None
        self.listGets = []
        self.listRequests = []

    def fresponseGet(self, sUrl, **kwargs):
        self.listGets.append((sUrl, dict(kwargs.get("headers") or {})))
        responseAnswer = self.dictAnswers.get(sUrl)
        if isinstance(responseAnswer, Exception):
            raise responseAnswer
        if responseAnswer is not None:
            return responseAnswer
        if self.fresponseDefault is not None:
            return self.fresponseDefault(sUrl)
        return _FakeResponse(404, {"message": "gone"})

    def fresponseRequest(self, sMethod, sUrl, **kwargs):
        self.listRequests.append((sMethod, sUrl, kwargs))
        return self.dictAnswers.get((sMethod, sUrl)) or _FakeResponse(500)

    def fnInstall(self, monkeypatch, moduleTarget):
        monkeypatch.setattr(moduleTarget, "requests", types.SimpleNamespace(
            get=self.fresponseGet, request=self.fresponseRequest,
            RequestException=requests.RequestException,
        ))


@pytest.fixture
def transportZenodo(monkeypatch):
    """A recording transport installed under ``zenodoClient``."""
    doubleTransport = TransportDouble()
    doubleTransport.fnInstall(monkeypatch, zenodoClient)
    return doubleTransport


def fclientSandbox():
    """Return a sandbox client with an explicit (fixture) token."""
    return zenodoClient.ZenodoClient("sandbox", sToken="tokenFixture")


def fnPublishRecordWithFile(transportZenodo, baContent):
    """Serve record 777 listing one file, and that file's bytes."""
    transportZenodo.dictAnswers[S_RECORD_URL] = _FakeResponse(200, {
        "files": [{"key": "meta.json", "links": {"self": S_FILE_URL}}],
    })
    transportZenodo.dictAnswers[S_FILE_URL] = _FakeResponse(
        200, baContent=baContent,
    )


# ── origins and redirects ────────────────────────────────────────


def testAClientOverAnInjectedBaseMayReachThatBaseAndZenodoOnly():
    """A loopback base joins the table's hosts; nothing else is added."""
    clientLoopback = zenodoClient.ZenodoClient(
        "sandbox", sToken="", sBaseUrl="http://127.0.0.1:8765/api",
    )
    assert clientLoopback.flistAllowedOrigins() == [
        "https://zenodo.org", "https://sandbox.zenodo.org",
        "http://127.0.0.1:8765",
    ]
    assert fclientSandbox().flistAllowedOrigins() == [
        "https://zenodo.org", "https://sandbox.zenodo.org",
    ]


@pytest.mark.parametrize("sUrl", ["", "not a url", "/relative/path"])
def testAUrlWithoutSchemeOrHostHasNoOrigin(sUrl):
    """No scheme or no host is the empty origin, which no allowlist holds."""
    assert zenodoClient.fsOriginOfUrl(sUrl) == ""


def testAnEndlessRedirectChainIsRefusedAfterTheHopLimit(transportZenodo):
    """Six redirects are refused, naming the origin and not the path."""
    transportZenodo.fresponseDefault = lambda sUrl: _FakeResponse(
        302, dictHeaders={
            "Location": sUrl.split("?")[0] + "x?access_token=secretFixture",
        },
    )
    with pytest.raises(
        zenodoClient.ZenodoRedirectRefusedError,
        match=r"more than 5 redirects from https://sandbox\.zenodo\.org$",
    ) as infoError:
        zenodoClient.fresponseGetWithinAllowlist(
            S_RECORD_URL, zenodoClient.flistAllowedZenodoOrigins(),
        )
    assert "secretFixture" not in str(infoError.value)
    assert len(transportZenodo.listGets) == 6


def testACrossOriginRedirectDropsTheToken(transportZenodo):
    """A hop to another allowed origin is fetched without Authorization."""
    sProductionUrl = "https://zenodo.org/api/records/777"
    transportZenodo.dictAnswers[S_RECORD_URL] = _FakeResponse(
        301, dictHeaders={"Location": sProductionUrl},
    )
    transportZenodo.dictAnswers[sProductionUrl] = _FakeResponse(200, {})
    zenodoClient.fresponseGetWithinAllowlist(
        S_RECORD_URL, zenodoClient.flistAllowedZenodoOrigins(),
        dictHeaders={"Authorization": "Bearer tokenFixture"},
    )
    assert transportZenodo.listGets == [
        (S_RECORD_URL, {"Authorization": "Bearer tokenFixture"}),
        (sProductionUrl, {}),
    ]


# ── reading one JSON file out of a record ────────────────────────


def testARecordFileIsDecodedAsJson(transportZenodo):
    """A served JSON file is returned decoded."""
    fnPublishRecordWithFile(transportZenodo, b'{"iRuns": 3}')
    assert fclientSandbox().fjsonFetchRecordFile("777", "meta.json") == {
        "iRuns": 3,
    }


def testARecordWithoutTheFileAnswersNone(transportZenodo):
    """A key the record does not list is absent, not an error."""
    fnPublishRecordWithFile(transportZenodo, b"{}")
    assert fclientSandbox().fjsonFetchRecordFile("777", "other.json") is None


def testAFileThatIsNotJsonAnswersNone(transportZenodo):
    """Bytes that do not decode as JSON are an unreadable file."""
    fnPublishRecordWithFile(transportZenodo, b"\xff\xfe not json")
    assert fclientSandbox().fjsonFetchRecordFile("777", "meta.json") is None


def testAFileTheServerRefusesAnswersNone(transportZenodo):
    """A failing file download is None; the record fetch itself succeeded."""
    fnPublishRecordWithFile(transportZenodo, b"{}")
    transportZenodo.dictAnswers[S_FILE_URL] = _FakeResponse(500, sText="err")
    assert fclientSandbox().fjsonFetchRecordFile("777", "meta.json") is None


def testAFileLargerThanTheCapIsNeverMaterialized(
    transportZenodo, monkeypatch,
):
    """A body past the in-memory ceiling is refused, not truncated."""
    monkeypatch.setattr(zenodoClient, "_I_JSON_FETCH_BYTE_CAP", 8)
    monkeypatch.setattr(zenodoClient, "_HASH_CHUNK_SIZE", 4)
    fnPublishRecordWithFile(transportZenodo, b'{"s": "0123456789"}')
    assert fclientSandbox().fjsonFetchRecordFile("777", "meta.json") is None


def testAFileAtTheCapIsRead(transportZenodo, monkeypatch):
    """The same cap admits a body that fits it exactly."""
    monkeypatch.setattr(zenodoClient, "_I_JSON_FETCH_BYTE_CAP", 8)
    monkeypatch.setattr(zenodoClient, "_HASH_CHUNK_SIZE", 4)
    fnPublishRecordWithFile(transportZenodo, b'{"s": 1}')
    assert fclientSandbox().fjsonFetchRecordFile("777", "meta.json") == {
        "s": 1,
    }


# ── publishing and hashing ───────────────────────────────────────


def testPublishReturnsTheDepositAsZenodoAnsweredIt(transportZenodo):
    """The publish response dict comes back, via the publish action URL."""
    sPublishUrl = (
        "https://sandbox.zenodo.org/api/deposit/depositions/55/actions/publish"
    )
    transportZenodo.dictAnswers[("POST", sPublishUrl)] = _FakeResponse(
        202, {"doi": "10.5072/zenodo.55", "conceptdoi": "10.5072/zenodo.54"},
    )
    assert fclientSandbox().fdictPublishDraft(55) == {
        "doi": "10.5072/zenodo.55", "conceptdoi": "10.5072/zenodo.54",
    }
    sMethod, sUrl, dictKwargs = transportZenodo.listRequests[0]
    assert (sMethod, sUrl) == ("POST", sPublishUrl)
    assert dictKwargs["allow_redirects"] is False


def testANetworkErrorWhileHashingIsRedactedBeforeItIsRaised(
    transportZenodo,
):
    """The transport's message may carry a token; the raised one does not."""
    fnPublishRecordWithFile(transportZenodo, b"{}")
    transportZenodo.dictAnswers[S_FILE_URL] = requests.ConnectionError(
        "connection reset fetching " + S_FILE_URL
        + "?access_token=secretFixture",
    )
    with pytest.raises(
        zenodoClient.ZenodoError, match="Network error fetching Zenodo file",
    ) as infoError:
        zenodoClient.fdictFetchRemoteHashes(
            "777", clientZenodo=fclientSandbox(),
        )
    assert "secretFixture" not in str(infoError.value)


# ── deposit agreement when the archive reports ``size`` ──────────


def fdictDepositReportingSize(iSize):
    """Return a deposit whose one file reports ``size``, not ``filesize``."""
    return {"files": [{
        "key": "environment-image.tar.gz", "size": iSize,
        "checksum": "md5:" + "a" * 32,
    }]}


def fdictExpectedUpload():
    """Return what vaibify says it uploaded."""
    return {"sKey": "environment-image.tar.gz", "sMd5": "a" * 32,
            "iBytes": 2048}


def testASizeFieldThatAgreesIsAgreement():
    """An archive spelling the size ``size`` is read, and agrees."""
    assert zenodoClient.flistDescribeDepositDisagreement(
        fdictDepositReportingSize(2048), [fdictExpectedUpload()],
    ) == []


def testASizeFieldThatDiffersIsADisagreement():
    """The same spelling with one byte fewer is named as a problem."""
    listProblems = zenodoClient.flistDescribeDepositDisagreement(
        fdictDepositReportingSize(2047), [fdictExpectedUpload()],
    )
    assert listProblems == [
        "environment-image.tar.gz: the archive reports 2047 bytes and "
        "vaibify uploaded 2048.",
    ]


# ── arXiv ────────────────────────────────────────────────────────


class ClockDouble:
    """A monotonic clock the test advances, and a recorded sleep."""

    def __init__(self, listReadings):
        self.listReadings = list(listReadings)
        self.listSlept = []

    def monotonic(self):
        return self.listReadings.pop(0)

    def sleep(self, fSeconds):
        self.listSlept.append(fSeconds)


def testASecondArxivRequestWaitsOutTheInterval(monkeypatch):
    """A request 1 s after the last one sleeps the remaining 2 s."""
    clockDouble = ClockDouble([101.0, 103.0])
    monkeypatch.setattr(arxivClient, "time", clockDouble)
    monkeypatch.setattr(arxivClient, "_fLastRequestTime", 100.0)
    arxivClient._fnEnforceRateLimit()
    assert clockDouble.listSlept == [pytest.approx(2.0)]
    assert arxivClient._fLastRequestTime == 103.0


def testAnArxivRequestAfterTheIntervalDoesNotWait(monkeypatch):
    """A request 5 s after the last one is sent at once."""
    clockDouble = ClockDouble([105.0, 105.0])
    monkeypatch.setattr(arxivClient, "time", clockDouble)
    monkeypatch.setattr(arxivClient, "_fLastRequestTime", 100.0)
    arxivClient._fnEnforceRateLimit()
    assert clockDouble.listSlept == []


def fnServeArxivFeed(monkeypatch, iStatus, sAtomXml):
    """Answer the arXiv API with one status and body; no rate limit."""
    monkeypatch.setattr(arxivClient, "_fnEnforceRateLimit", lambda: None)
    monkeypatch.setattr(arxivClient, "requests", types.SimpleNamespace(
        get=lambda sUrl, **kwargs: _FakeResponse(iStatus, sText=sAtomXml),
    ))


S_ATOM_OPEN = '<feed xmlns="http://www.w3.org/2005/Atom">'


def testAnUnexpectedArxivStatusIsAGeneralError(monkeypatch):
    """A 503 is neither not-found nor rate-limited, and says its status."""
    fnServeArxivFeed(monkeypatch, 503, "")
    with pytest.raises(
        arxivClient.ArxivError, match=r"failed \(503\)",
    ) as infoError:
        arxivClient.fsResolveLatestVersion("2401.00001")
    assert type(infoError.value) is arxivClient.ArxivError


def testAnArxivEntryWithoutAnIdIsRefused(monkeypatch):
    """An entry lacking its id cannot name a version."""
    fnServeArxivFeed(monkeypatch, 200, S_ATOM_OPEN + "<entry/></feed>")
    with pytest.raises(arxivClient.ArxivError, match="lacks an id field"):
        arxivClient.fsResolveLatestVersion("2401.00001")


@pytest.mark.parametrize("sAbsUrl", [
    "http://arxiv.org/abs/2401.00001",
    "http://arxiv.org/abs/2401.00001vx",
])
def testAnArxivIdWithoutAVersionSuffixIsRefused(monkeypatch, sAbsUrl):
    """No trailing ``vN`` is an error, never a guessed version."""
    fnServeArxivFeed(
        monkeypatch, 200,
        S_ATOM_OPEN + "<entry><id>" + sAbsUrl + "</id></entry></feed>",
    )
    with pytest.raises(arxivClient.ArxivError, match="no version suffix"):
        arxivClient.fsResolveLatestVersion("2401.00001")


def testAnArxivIdWithAVersionSuffixResolves(monkeypatch):
    """The same feed with ``v3`` resolves to ``v3``."""
    fnServeArxivFeed(
        monkeypatch, 200,
        S_ATOM_OPEN + "<entry><id>http://arxiv.org/abs/2401.00001v3</id>"
        "</entry></feed>",
    )
    assert arxivClient.fsResolveLatestVersion("2401.00001") == "v3"


def testRemovingAnAlreadyRemovedTarballIsQuiet(tmp_path):
    """The post-extraction cleanup is best effort and never raises."""
    sTarball = str(tmp_path / "gone.tar")
    arxivClient._fnRemoveTarballAfterExtract(sTarball)
    (tmp_path / "present.tar").write_bytes(b"x")
    arxivClient._fnRemoveTarballAfterExtract(str(tmp_path / "present.tar"))
    assert list(tmp_path.iterdir()) == []


def testTheDefaultArxivCacheIsAPrivateDirectoryUnderTmp(
    monkeypatch, tmp_path,
):
    """The fallback cache lives under the temp root with mode 0700."""
    import os
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    sCache = arxivClient._fsBuildDefaultCacheDir()
    assert sCache == str(tmp_path / "vaibify-arxiv-cache")
    assert (os.stat(sCache).st_mode & 0o777) == 0o700
    assert arxivClient._fsBuildDefaultCacheDir() == sCache
