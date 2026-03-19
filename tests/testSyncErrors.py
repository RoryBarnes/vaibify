"""Tests for sync error classification in syncDispatcher.py."""

import pytest
from vaibify.gui.syncDispatcher import (
    fdictClassifyError, fdictSyncResult,
)


class TestClassifyError:
    """Test fdictClassifyError pattern matching."""

    def test_fbAuthenticationFailure(self):
        dictResult = fdictClassifyError(
            128, "fatal: Authentication failed for 'https://...'"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbHttp401(self):
        dictResult = fdictClassifyError(
            1, "HTTP 401 Unauthorized"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbHttp403(self):
        dictResult = fdictClassifyError(
            1, "403 Forbidden: insufficient permissions"
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbRateLimit(self):
        dictResult = fdictClassifyError(
            1, "Error: rate limit exceeded. Try again later."
        )
        assert dictResult["sErrorType"] == "rateLimit"

    def test_fbHttp429(self):
        dictResult = fdictClassifyError(
            1, "HTTP 429 Too Many Requests"
        )
        assert dictResult["sErrorType"] == "rateLimit"

    def test_fbNotFound(self):
        dictResult = fdictClassifyError(
            1, "Error: repository not found"
        )
        assert dictResult["sErrorType"] == "notFound"

    def test_fbHttp404(self):
        dictResult = fdictClassifyError(
            1, "HTTP 404: deposit not found"
        )
        assert dictResult["sErrorType"] == "notFound"

    def test_fbNetworkTimeout(self):
        dictResult = fdictClassifyError(
            1, "fatal: unable to access: Connection timeout"
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbConnectionRefused(self):
        dictResult = fdictClassifyError(
            1, "Connection refused to git.overleaf.com"
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbUnknownError(self):
        dictResult = fdictClassifyError(
            1, "some random failure message"
        )
        assert dictResult["sErrorType"] == "unknown"

    def test_fbOutputPreserved(self):
        sOutput = "detailed error description here"
        dictResult = fdictClassifyError(1, sOutput)
        assert dictResult["sMessage"] == sOutput


class TestSyncResult:
    """Test fdictSyncResult wrapper."""

    def test_fbSuccessResult(self):
        dictResult = fdictSyncResult(0, "abc1234\n")
        assert dictResult["bSuccess"] is True
        assert dictResult["sOutput"] == "abc1234"

    def test_fbFailureResult(self):
        dictResult = fdictSyncResult(
            128, "Authentication failed"
        )
        assert dictResult["bSuccess"] is False
        assert dictResult["sErrorType"] == "auth"

    def test_fbUnknownFailure(self):
        dictResult = fdictSyncResult(1, "oops")
        assert dictResult["bSuccess"] is False
        assert dictResult["sErrorType"] == "unknown"
