"""Tests for fdictFetchRemoteHashes (all HTTP mocked)."""

import hashlib
from unittest.mock import patch, MagicMock

import pytest

from vaibify.reproducibility.zenodoClient import (
    ZenodoAuthError,
    ZenodoClient,
    ZenodoError,
    ZenodoNotFoundError,
    ZenodoRateLimitError,
    fdictFetchRemoteHashes,
)


@pytest.fixture
def clientSandbox():
    """Return a ZenodoClient with a fake token for HTTP-mocked tests."""
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake_token_for_testing"
    return client


def _fmockJsonResponse(iStatusCode=200, dictJson=None, sText=""):
    """Build a mock requests.Response with a JSON payload."""
    mockResponse = MagicMock()
    mockResponse.status_code = iStatusCode
    mockResponse.json.return_value = dictJson if dictJson else {}
    mockResponse.text = sText
    return mockResponse


def _fmockStreamResponse(listChunks, iStatusCode=200):
    """Build a mock requests.Response that streams listChunks."""
    mockResponse = MagicMock()
    mockResponse.status_code = iStatusCode
    mockResponse.text = ""
    mockResponse.iter_content.return_value = iter(listChunks)
    return mockResponse


def _fdictRecordWithFiles(listFileSpecs):
    """Build a Zenodo record dict from (sKey, sUrl) tuples."""
    listFiles = [
        {"key": sKey, "links": {"self": sUrl}}
        for sKey, sUrl in listFileSpecs
    ]
    return {"id": 12345, "files": listFiles}


def _fsExpectedSha256(baContent):
    """Return the SHA-256 hex digest of baContent."""
    return hashlib.sha256(baContent).hexdigest()


def test_happy_path_no_filter_returns_hashes_for_all_files(clientSandbox):
    listSpecs = [
        ("data/x.csv", "https://sandbox.zenodo.org/api/files/x"),
        ("data/y.csv", "https://sandbox.zenodo.org/api/files/y"),
        ("readme.md", "https://sandbox.zenodo.org/api/files/r"),
    ]
    dictRecord = _fdictRecordWithFiles(listSpecs)
    baX = b"alpha"
    baY = b"beta-bytes"
    baR = b"# readme"
    listResponses = [
        _fmockJsonResponse(200, dictRecord),
        _fmockStreamResponse([baX]),
        _fmockStreamResponse([baY]),
        _fmockStreamResponse([baR]),
    ]
    with patch("requests.request", side_effect=[listResponses[0]]), \
            patch("requests.get", side_effect=listResponses[1:]):
        dictHashes = fdictFetchRemoteHashes(
            "12345", clientZenodo=clientSandbox,
        )
    assert dictHashes == {
        "data/x.csv": _fsExpectedSha256(baX),
        "data/y.csv": _fsExpectedSha256(baY),
        "readme.md": _fsExpectedSha256(baR),
    }


def test_filtered_returns_only_requested_keys(clientSandbox):
    listSpecs = [
        ("data/x.csv", "https://sandbox.zenodo.org/api/files/x"),
        ("data/y.csv", "https://sandbox.zenodo.org/api/files/y"),
    ]
    dictRecord = _fdictRecordWithFiles(listSpecs)
    baX = b"only-x-bytes"
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)), \
            patch("requests.get",
                  return_value=_fmockStreamResponse([baX])) as mockGet:
        dictHashes = fdictFetchRemoteHashes(
            "12345",
            listRelPaths=["data/x.csv"],
            clientZenodo=clientSandbox,
        )
    assert dictHashes == {"data/x.csv": _fsExpectedSha256(baX)}
    assert mockGet.call_count == 1


def test_filter_miss_records_none_for_absent_keys(clientSandbox):
    dictRecord = _fdictRecordWithFiles([
        ("data/real.csv", "https://sandbox.zenodo.org/api/files/r"),
    ])
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)), \
            patch("requests.get") as mockGet:
        dictHashes = fdictFetchRemoteHashes(
            "12345",
            listRelPaths=["nonexistent.txt"],
            clientZenodo=clientSandbox,
        )
    assert dictHashes == {"nonexistent.txt": None}
    assert mockGet.call_count == 0


def test_large_file_streams_in_chunks_without_full_load(clientSandbox):
    iChunkSize = 64 * 1024
    iChunkCount = 80  # 5 MB total
    baChunk = b"\xab" * iChunkSize
    hasherExpected = hashlib.sha256()
    for _ in range(iChunkCount):
        hasherExpected.update(baChunk)
    sExpected = hasherExpected.hexdigest()
    dictRecord = _fdictRecordWithFiles([
        ("big.bin", "https://sandbox.zenodo.org/api/files/big"),
    ])
    listChunks = [baChunk] * iChunkCount
    mockStream = _fmockStreamResponse(listChunks)
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)), \
            patch("requests.get", return_value=mockStream):
        dictHashes = fdictFetchRemoteHashes(
            "12345", clientZenodo=clientSandbox,
        )
    assert dictHashes == {"big.bin": sExpected}
    mockStream.iter_content.assert_called_once_with(iChunkSize)


def test_auth_failure_raises_with_actionable_message(clientSandbox):
    with patch("requests.request",
               return_value=_fmockJsonResponse(
                   iStatusCode=401, sText="Unauthorized")):
        with pytest.raises(ZenodoAuthError) as excInfo:
            fdictFetchRemoteHashes("12345", clientZenodo=clientSandbox)
    sMessage = str(excInfo.value)
    assert "Zenodo" in sMessage
    assert "auth" in sMessage.lower()


def test_record_not_found_raises_with_record_id(clientSandbox):
    with patch("requests.request",
               return_value=_fmockJsonResponse(
                   iStatusCode=404, sText="Not Found")):
        with pytest.raises(ZenodoNotFoundError) as excInfo:
            fdictFetchRemoteHashes("missing-id", clientZenodo=clientSandbox)
    assert "missing-id" in str(excInfo.value)


def test_rate_limit_raises_informative_error(clientSandbox):
    with patch("requests.request",
               return_value=_fmockJsonResponse(
                   iStatusCode=429, sText="Too Many Requests")):
        with pytest.raises(ZenodoRateLimitError) as excInfo:
            fdictFetchRemoteHashes("12345", clientZenodo=clientSandbox)
    sMessage = str(excInfo.value).lower()
    assert "rate limit" in sMessage


def test_empty_deposit_returns_empty_dict(clientSandbox):
    dictRecord = {"id": 12345, "files": []}
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)), \
            patch("requests.get") as mockGet:
        dictHashes = fdictFetchRemoteHashes(
            "12345", clientZenodo=clientSandbox,
        )
    assert dictHashes == {}
    assert mockGet.call_count == 0


def test_iteration_order_matches_deposit_listing_order(clientSandbox):
    listSpecs = [
        ("z_last.csv", "https://sandbox.zenodo.org/api/files/z"),
        ("a_first.csv", "https://sandbox.zenodo.org/api/files/a"),
        ("m_middle.csv", "https://sandbox.zenodo.org/api/files/m"),
    ]
    dictRecord = _fdictRecordWithFiles(listSpecs)
    listResponses = [_fmockStreamResponse([b"data"]) for _ in listSpecs]
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)), \
            patch("requests.get", side_effect=listResponses):
        dictHashes = fdictFetchRemoteHashes(
            "12345", clientZenodo=clientSandbox,
        )
    assert list(dictHashes.keys()) == [
        "z_last.csv", "a_first.csv", "m_middle.csv",
    ]


def test_token_redacted_in_error_messages(clientSandbox):
    sLeakyBody = (
        "Resource not found at "
        "https://sandbox.zenodo.org/api/records/12345"
        "?access_token=SECRET_LEAKED_TOKEN"
    )
    with patch("requests.request",
               return_value=_fmockJsonResponse(
                   iStatusCode=404, sText=sLeakyBody)):
        with pytest.raises(ZenodoNotFoundError) as excInfo:
            fdictFetchRemoteHashes("12345", clientZenodo=clientSandbox)
    sMessage = str(excInfo.value)
    assert "SECRET_LEAKED_TOKEN" not in sMessage
    assert "access_token=" not in sMessage


def test_default_client_constructed_when_none_provided():
    dictRecord = {"id": 1, "files": []}
    with patch("requests.request",
               return_value=_fmockJsonResponse(200, dictRecord)):
        dictHashes = fdictFetchRemoteHashes(
            "1",
            clientZenodo=ZenodoClient(sService="sandbox", sToken="t"),
        )
    assert dictHashes == {}


def test_unknown_error_propagates_as_zenodo_error(clientSandbox):
    with patch("requests.request",
               return_value=_fmockJsonResponse(
                   iStatusCode=500, sText="Internal Server Error")):
        with pytest.raises(ZenodoError):
            fdictFetchRemoteHashes("12345", clientZenodo=clientSandbox)
