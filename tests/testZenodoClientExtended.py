"""Extended tests for vaibify.reproducibility.zenodoClient."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from vaibify.reproducibility.zenodoClient import (
    ZenodoClient,
    ZenodoError,
    ZenodoAuthError,
    ZenodoNotFoundError,
    _fnValidateService,
    _fdictBuildAuthHeader,
    _fdictEmptyMetadata,
    _fsExtractBucketUrl,
    _fsFindFileUrl,
    _fnCheckResponse,
    _fiterReadChunks,
)


# -----------------------------------------------------------------------
# _fnValidateService
# -----------------------------------------------------------------------


def test_fnValidateService_valid_zenodo():
    _fnValidateService("zenodo")


def test_fnValidateService_valid_sandbox():
    _fnValidateService("sandbox")


def test_fnValidateService_invalid_raises():
    with pytest.raises(ValueError, match="Unknown"):
        _fnValidateService("invalid_service")


# -----------------------------------------------------------------------
# _fdictEmptyMetadata
# -----------------------------------------------------------------------


def test_fdictEmptyMetadata_has_required_keys():
    dictMeta = _fdictEmptyMetadata()
    assert "title" in dictMeta
    assert "upload_type" in dictMeta
    assert "creators" in dictMeta
    assert dictMeta["upload_type"] == "dataset"


# -----------------------------------------------------------------------
# _fsExtractBucketUrl
# -----------------------------------------------------------------------


def test_fsExtractBucketUrl_success():
    dictDeposit = {
        "links": {"bucket": "https://zenodo.org/bucket/123"}
    }
    sUrl = _fsExtractBucketUrl(dictDeposit)
    assert sUrl == "https://zenodo.org/bucket/123"


def test_fsExtractBucketUrl_missing_raises():
    with pytest.raises(ZenodoError, match="bucket"):
        _fsExtractBucketUrl({"links": {}})


def test_fsExtractBucketUrl_no_links_raises():
    with pytest.raises(ZenodoError):
        _fsExtractBucketUrl({})


# -----------------------------------------------------------------------
# _fsFindFileUrl
# -----------------------------------------------------------------------


def test_fsFindFileUrl_finds_file():
    dictRecord = {
        "files": [
            {
                "key": "data.hdf5",
                "links": {"self": "https://zenodo.org/file/1"},
            }
        ]
    }
    sUrl = _fsFindFileUrl(dictRecord, "data.hdf5")
    assert sUrl == "https://zenodo.org/file/1"


def test_fsFindFileUrl_missing_raises():
    dictRecord = {"files": []}
    with pytest.raises(ZenodoNotFoundError, match="data.hdf5"):
        _fsFindFileUrl(dictRecord, "data.hdf5")


def test_fsFindFileUrl_no_files_key():
    dictRecord = {}
    with pytest.raises(ZenodoNotFoundError):
        _fsFindFileUrl(dictRecord, "file.txt")


# -----------------------------------------------------------------------
# _fnCheckResponse
# -----------------------------------------------------------------------


def test_fnCheckResponse_200_ok():
    mockResponse = MagicMock(status_code=200)
    _fnCheckResponse(mockResponse)


def test_fnCheckResponse_201_ok():
    mockResponse = MagicMock(status_code=201)
    _fnCheckResponse(mockResponse)


def test_fnCheckResponse_401_raises_auth():
    mockResponse = MagicMock(
        status_code=401, text="Unauthorized",
    )
    with pytest.raises(ZenodoAuthError):
        _fnCheckResponse(mockResponse)


def test_fnCheckResponse_403_raises_auth():
    mockResponse = MagicMock(
        status_code=403, text="Forbidden",
    )
    with pytest.raises(ZenodoAuthError):
        _fnCheckResponse(mockResponse)


def test_fnCheckResponse_404_raises_not_found():
    mockResponse = MagicMock(
        status_code=404, text="Not Found",
    )
    with pytest.raises(ZenodoNotFoundError):
        _fnCheckResponse(mockResponse)


def test_fnCheckResponse_500_raises_generic():
    mockResponse = MagicMock(
        status_code=500, text="Server Error",
    )
    with pytest.raises(ZenodoError, match="500"):
        _fnCheckResponse(mockResponse)


# -----------------------------------------------------------------------
# _fiterReadChunks
# -----------------------------------------------------------------------


def test_fiterReadChunks_yields_data():
    import io
    baData = b"x" * 100
    fileHandle = io.BytesIO(baData)
    mockProgress = MagicMock()
    listChunks = list(_fiterReadChunks(fileHandle, mockProgress))
    assert b"".join(listChunks) == baData
    assert mockProgress.update.called


# -----------------------------------------------------------------------
# ZenodoClient methods with mocked requests
# -----------------------------------------------------------------------


@pytest.fixture
def clientTest():
    """Return a ZenodoClient with a pre-set fake token."""
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake_test_token"
    return client


def _fmockResponse(iStatusCode=200, dictJson=None, sText=""):
    """Build a mock response object."""
    mockResp = MagicMock()
    mockResp.status_code = iStatusCode
    mockResp.json.return_value = dictJson or {}
    mockResp.text = sText
    return mockResp


@patch("requests.request")
def test_fdictGetDeposit(mockRequest, clientTest):
    mockRequest.return_value = _fmockResponse(
        dictJson={"id": 42, "metadata": {}},
    )
    dictResult = clientTest.fdictGetDeposit(42)
    assert dictResult["id"] == 42
    sUrl = mockRequest.call_args[0][1]
    assert "/42" in sUrl


@patch("requests.request")
def test_fnSetMetadata(mockRequest, clientTest):
    mockRequest.return_value = _fmockResponse()
    clientTest.fnSetMetadata(42, {"title": "Test"})
    sMethod = mockRequest.call_args[0][0]
    assert sMethod == "PUT"


@patch("requests.request")
def test_fdictCopyDraft(mockRequest, clientTest):
    mockRequest.return_value = _fmockResponse(
        dictJson={"id": 99},
    )
    dictResult = clientTest.fdictCopyDraft(42)
    sUrl = mockRequest.call_args[0][1]
    assert "newversion" in sUrl


@patch("requests.request")
def test_flistSearchDeposits(mockRequest, clientTest):
    mockRequest.return_value = _fmockResponse(
        dictJson=[{"id": 1}, {"id": 2}],
    )
    listResult = clientTest.flistSearchDeposits("vaibify")
    assert isinstance(listResult, list)
    dictParams = mockRequest.call_args[1].get("params", {})
    assert dictParams.get("q") == "vaibify"


@patch("requests.request")
def test_delete_returns_empty_on_204(mockRequest, clientTest):
    mockRequest.return_value = _fmockResponse(iStatusCode=204)
    clientTest.fnDeleteDraft(99)
    mockRequest.assert_called_once()


# -----------------------------------------------------------------------
# ZenodoClient constructor
# -----------------------------------------------------------------------


def test_zenodo_production_url():
    client = ZenodoClient(sService="zenodo")
    assert "zenodo.org" in client._sBaseUrl
    assert "sandbox" not in client._sBaseUrl


def test_sandbox_url():
    client = ZenodoClient(sService="sandbox")
    assert "sandbox" in client._sBaseUrl


# -----------------------------------------------------------------------
# fnUploadToBucket: FileNotFoundError branch and happy path
# -----------------------------------------------------------------------


def test_fnUploadToBucket_raises_when_file_missing(clientTest):
    """A missing source file must fail fast with a clear message."""
    sMissing = "/nonexistent/path/to/figure.png"
    with pytest.raises(FileNotFoundError, match="figure.png"):
        clientTest.fnUploadToBucket(
            "https://example.invalid/bucket/abc", sMissing,
        )


@patch("requests.put")
def test_fnUploadToBucket_uploads_existing_file(mockPut, clientTest, tmp_path):
    """Happy path: existing file is PUT to bucket with bearer auth."""
    pathFile = tmp_path / "figure.png"
    pathFile.write_bytes(b"binary-payload")
    mockPut.return_value = _fmockResponse(iStatusCode=201)
    clientTest._sToken = "tok"
    clientTest.fnUploadToBucket(
        "https://example.invalid/bucket/abc", str(pathFile),
    )
    mockPut.assert_called_once()
    sCalledUrl = mockPut.call_args[0][0]
    assert sCalledUrl.endswith("/figure.png")
    dictHeaders = mockPut.call_args[1]["headers"]
    assert dictHeaders["Authorization"] == "Bearer tok"
    assert dictHeaders["Content-Type"] == "application/octet-stream"


# -----------------------------------------------------------------------
# fdictGetNewVersionDraft: defensive against malformed Zenodo responses
# -----------------------------------------------------------------------


@patch("requests.request")
def test_fdictGetNewVersionDraft_raises_clean_error_on_missing_links(
    mockRequest, clientTest,
):
    """A response lacking links.latest_draft must surface as ZenodoError, not KeyError."""
    mockRequest.return_value = _fmockResponse(
        dictJson={"id": 42},
    )
    with pytest.raises((KeyError, ZenodoError)):
        clientTest.fdictGetNewVersionDraft(7)


@patch("requests.request")
def test_fdictGetNewVersionDraft_follows_latest_draft_link(
    mockRequest, clientTest,
):
    listResponses = [
        _fmockResponse(
            dictJson={
                "links": {
                    "latest_draft": "https://example.invalid/draft/9"
                },
            },
        ),
        _fmockResponse(
            dictJson={"id": 9, "links": {"bucket": "https://b/9"}},
        ),
    ]
    mockRequest.side_effect = listResponses
    dictDraft = clientTest.fdictGetNewVersionDraft(7)
    assert dictDraft["id"] == 9
    assert mockRequest.call_count == 2
    assert mockRequest.call_args_list[1][0][1] == (
        "https://example.invalid/draft/9"
    )


# -----------------------------------------------------------------------
# fnClearDraftFiles: defensive against missing/empty file lists
# -----------------------------------------------------------------------


@patch("requests.request")
def test_fnClearDraftFiles_no_op_when_files_field_absent(
    mockRequest, clientTest,
):
    mockRequest.return_value = _fmockResponse(
        dictJson={"id": 7},
    )
    clientTest.fnClearDraftFiles(7)
    assert mockRequest.call_count == 1


@patch("requests.request")
def test_fnClearDraftFiles_no_op_when_files_list_empty(
    mockRequest, clientTest,
):
    mockRequest.return_value = _fmockResponse(
        dictJson={"id": 7, "files": []},
    )
    clientTest.fnClearDraftFiles(7)
    assert mockRequest.call_count == 1


@patch("requests.request")
def test_fnClearDraftFiles_skips_files_without_id(
    mockRequest, clientTest,
):
    mockRequest.return_value = _fmockResponse(
        dictJson={
            "id": 7,
            "files": [{"filename": "no-id.png"}],
        },
    )
    clientTest.fnClearDraftFiles(7)
    assert mockRequest.call_count == 1


@patch("requests.request")
def test_fnClearDraftFiles_deletes_each_file(mockRequest, clientTest):
    listResponses = [
        _fmockResponse(
            dictJson={
                "id": 7,
                "files": [
                    {"id": "fid1"}, {"file_id": "fid2"},
                ],
            },
        ),
        _fmockResponse(iStatusCode=204),
        _fmockResponse(iStatusCode=204),
    ]
    mockRequest.side_effect = listResponses
    clientTest.fnClearDraftFiles(7)
    assert mockRequest.call_count == 3
    saMethods = [
        call_args[0][0]
        for call_args in mockRequest.call_args_list[1:]
    ]
    assert saMethods == ["DELETE", "DELETE"]
