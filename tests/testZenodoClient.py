"""Tests for vaibify.reproducibility.zenodoClient (all HTTP mocked)."""

from unittest.mock import patch, MagicMock

import pytest

from vaibify.reproducibility.zenodoClient import (
    ZenodoClient,
    ZenodoError,
    ZenodoAuthError,
    _fdictBuildAuthHeader,
    _fnCheckResponse,
)


@pytest.fixture
def clientSandbox():
    """Return a ZenodoClient pointed at the sandbox with a fake token."""
    client = ZenodoClient(sService="sandbox")
    client._sToken = "fake_token_for_testing"
    return client


def _fmockResponse(iStatusCode=200, dictJson=None, sText=""):
    """Build a mock requests.Response object."""
    mockResponse = MagicMock()
    mockResponse.status_code = iStatusCode
    mockResponse.json.return_value = dictJson if dictJson else {}
    mockResponse.text = sText
    return mockResponse


def test_fdictCreateDraft_sends_correct_request(clientSandbox):
    dictExpectedReturn = {"id": 12345, "metadata": {"title": ""}}

    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(
            iStatusCode=201, dictJson=dictExpectedReturn,
        )

        dictResult = clientSandbox.fdictCreateDraft()

    mockRequest.assert_called_once()
    sMethod = mockRequest.call_args[0][0]
    sUrl = mockRequest.call_args[0][1]

    assert sMethod == "POST"
    assert "/deposit/depositions" in sUrl
    assert dictResult["id"] == 12345


def test_fnPublishDraft_sends_correct_request(clientSandbox):
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(iStatusCode=202)

        clientSandbox.fnPublishDraft(12345)

    mockRequest.assert_called_once()
    sMethod = mockRequest.call_args[0][0]
    sUrl = mockRequest.call_args[0][1]

    assert sMethod == "POST"
    assert "/12345/actions/publish" in sUrl


def test_fnDeleteDraft_sends_correct_request(clientSandbox):
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(iStatusCode=204)

        clientSandbox.fnDeleteDraft(99999)

    mockRequest.assert_called_once()
    sMethod = mockRequest.call_args[0][0]
    sUrl = mockRequest.call_args[0][1]

    assert sMethod == "DELETE"
    assert "/99999" in sUrl


def test_fdictRequest_raises_on_error(clientSandbox):
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(
            iStatusCode=500,
            sText="Internal Server Error",
        )

        with pytest.raises(ZenodoError, match="500"):
            clientSandbox.fdictCreateDraft()


def test_fdictRequest_raises_auth_error(clientSandbox):
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(
            iStatusCode=401,
            sText="Unauthorized",
        )

        with pytest.raises(ZenodoAuthError):
            clientSandbox.fdictCreateDraft()


def test_bearer_auth_used_not_url_params(clientSandbox):
    with patch("requests.request") as mockRequest:
        mockRequest.return_value = _fmockResponse(
            iStatusCode=200, dictJson={"id": 1},
        )

        clientSandbox.fdictCreateDraft()

    dictCallKwargs = mockRequest.call_args[1]
    dictHeaders = dictCallKwargs.get("headers", {})
    assert "Authorization" in dictHeaders
    assert dictHeaders["Authorization"].startswith("Bearer ")

    sUrl = mockRequest.call_args[0][1]
    assert "access_token" not in sUrl
    assert "token=" not in sUrl

    dictParams = dictCallKwargs.get("params", {})
    if dictParams:
        assert "access_token" not in dictParams


def test_fdictBuildAuthHeader_format():
    dictHeader = _fdictBuildAuthHeader("mytoken")

    assert dictHeader == {"Authorization": "Bearer mytoken"}


def test_constructor_rejects_unknown_service():
    with pytest.raises(ValueError, match="Unknown Zenodo service"):
        ZenodoClient(sService="production_typo")
