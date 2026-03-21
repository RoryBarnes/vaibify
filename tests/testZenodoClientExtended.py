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
    listResult = clientTest.flistSearchDeposits("vplanet")
    assert isinstance(listResult, list)
    dictParams = mockRequest.call_args[1].get("params", {})
    assert dictParams.get("q") == "vplanet"


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
