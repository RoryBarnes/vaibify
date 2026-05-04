"""Tests for the host-side GitHub raw-content mirror.

Covers happy-path, mixed presence/absence, rate limiting, auth
failure, network errors, large-file streaming, token redaction,
ordering stability, the empty-input shortcut, and URL encoding of
special characters in paths.
"""

import hashlib
import io
from unittest.mock import patch, MagicMock

import pytest
import urllib.error
import urllib.request

from vaibify.reproducibility import githubMirror


# ----------------------------------------------------------------------
# Fakes for urllib.request.urlopen
# ----------------------------------------------------------------------


class _FakeResponse:
    """Minimal urllib HTTP response stand-in supporting context-manager use."""

    def __init__(self, baBody):
        self._buffer = io.BytesIO(baBody)

    def read(self, iSize=-1):
        return self._buffer.read(iSize)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fobjectMakeHttpError(iCode, dictHeaders=None):
    """Construct a urllib HTTPError with arbitrary status + headers."""
    headers = MagicMock()
    headers.get = lambda sKey, sDefault="": (dictHeaders or {}).get(
        sKey, sDefault
    )
    error = urllib.error.HTTPError(
        url="https://raw.githubusercontent.com/x/y/main/z",
        code=iCode,
        msg="error",
        hdrs=headers,
        fp=None,
    )
    error.headers = headers
    return error


def _fdispatcherFromMap(dictUrlToOutcome):
    """Return a urlopen replacement that consults a URL outcome map."""

    def fobjectFakeUrlOpen(objectRequest, *args, **kwargs):
        sUrl = objectRequest.full_url
        outcome = dictUrlToOutcome.get(sUrl)
        if outcome is None:
            raise AssertionError(f"Unexpected URL fetched: {sUrl}")
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome)

    return fobjectFakeUrlOpen


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_happy_path_three_files():
    dictBodies = {
        "alpha.txt": b"alpha-content",
        "beta.txt": b"beta-content-longer",
        "gamma.txt": b"\x00\x01\x02\x03\x04",
    }
    dictUrls = {
        f"https://raw.githubusercontent.com/owner/repo/main/{sName}": baBody
        for sName, baBody in dictBodies.items()
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        dictResult = githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main", list(dictBodies.keys()),
        )
    for sName, baBody in dictBodies.items():
        assert dictResult[sName] == hashlib.sha256(baBody).hexdigest()


# ----------------------------------------------------------------------
# Mixed: some 200, some 404
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_mixed_200_and_404():
    baAlpha = b"alpha"
    baGamma = b"gamma"
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/alpha.txt": baAlpha,
        "https://raw.githubusercontent.com/owner/repo/main/beta.txt":
            _fobjectMakeHttpError(404),
        "https://raw.githubusercontent.com/owner/repo/main/gamma.txt": baGamma,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        dictResult = githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main",
            ["alpha.txt", "beta.txt", "gamma.txt"],
        )
    assert dictResult["alpha.txt"] == hashlib.sha256(baAlpha).hexdigest()
    assert dictResult["beta.txt"] is None
    assert dictResult["gamma.txt"] == hashlib.sha256(baGamma).hexdigest()


# ----------------------------------------------------------------------
# Rate limit
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_rate_limit_raises_actionable():
    errorHttp = _fobjectMakeHttpError(
        403, {"X-RateLimit-Remaining": "0"},
    )
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/x.txt": errorHttp,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        with pytest.raises(githubMirror.GithubMirrorError) as excInfo:
            githubMirror.fdictFetchRemoteHashes(
                "owner", "repo", "main", ["x.txt"],
            )
    sMessage = str(excInfo.value)
    assert "GitHub" in sMessage
    assert "rate limit" in sMessage.lower()


# ----------------------------------------------------------------------
# Auth failure
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_auth_failure_raises_actionable():
    errorHttp = _fobjectMakeHttpError(401)
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/x.txt": errorHttp,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        with pytest.raises(githubMirror.GithubMirrorError) as excInfo:
            githubMirror.fdictFetchRemoteHashes(
                "owner", "repo", "main", ["x.txt"],
            )
    sMessage = str(excInfo.value)
    assert "401" in sMessage or "authentication" in sMessage.lower()


# ----------------------------------------------------------------------
# Network error
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_network_error_propagates_clean():
    errorUrl = urllib.error.URLError("Connection refused")
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/x.txt": errorUrl,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        with pytest.raises(githubMirror.GithubMirrorError) as excInfo:
            githubMirror.fdictFetchRemoteHashes(
                "owner", "repo", "main", ["x.txt"],
            )
    sMessage = str(excInfo.value)
    assert "network" in sMessage.lower()
    assert "Traceback" not in sMessage


# ----------------------------------------------------------------------
# Large file streaming (5 MB)
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_large_file_streamed_in_chunks():
    iSize = 5 * 1024 * 1024
    baBody = b"\xa5" * iSize
    sExpectedDigest = hashlib.sha256(baBody).hexdigest()
    dictReadCalls = {"iCount": 0}

    class _StreamingResponse:
        def __init__(self, baContent):
            self._buffer = io.BytesIO(baContent)

        def read(self, iSize=-1):
            dictReadCalls["iCount"] += 1
            return self._buffer.read(iSize)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fobjectFakeUrlOpen(objectRequest, *args, **kwargs):
        return _StreamingResponse(baBody)

    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=fobjectFakeUrlOpen,
    ):
        dictResult = githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main", ["big.bin"],
        )
    assert dictResult["big.bin"] == sExpectedDigest
    assert dictReadCalls["iCount"] >= 2


# ----------------------------------------------------------------------
# Token redaction in error paths
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_redacts_token_in_error_messages():
    sFakeToken = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    errorUrl = urllib.error.URLError(
        f"HTTPS proxy denied authorization {sFakeToken}"
    )
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/x.txt": errorUrl,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        with pytest.raises(githubMirror.GithubMirrorError) as excInfo:
            githubMirror.fdictFetchRemoteHashes(
                "owner", "repo", "main", ["x.txt"],
            )
    for sArg in excInfo.value.args:
        assert sFakeToken not in str(sArg)


def test_fsRedactStderr_scrubs_known_token_shapes():
    sToken = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sScrubbed = githubMirror.fsRedactStderr(
        f"oops the token was {sToken}"
    )
    assert sToken not in sScrubbed


def test_fsRedactStderr_scrubs_authorization_lines():
    sScrubbed = githubMirror.fsRedactStderr(
        "Authorization: Bearer secret-secret-secret"
    )
    assert "secret-secret-secret" not in sScrubbed


# ----------------------------------------------------------------------
# Stable ordering
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_preserves_caller_order():
    listPaths = ["zeta.txt", "alpha.txt", "mu.txt", "beta.txt"]
    dictUrls = {
        f"https://raw.githubusercontent.com/owner/repo/main/{sName}":
            sName.encode("utf-8")
        for sName in listPaths
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        dictResult = githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main", listPaths,
        )
    assert list(dictResult.keys()) == listPaths


# ----------------------------------------------------------------------
# Empty input
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_empty_list_returns_empty_dict():
    with patch.object(
        githubMirror.urllib.request, "urlopen",
    ) as mockUrlopen:
        dictResult = githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main", [],
        )
    assert dictResult == {}
    assert mockUrlopen.call_count == 0


# ----------------------------------------------------------------------
# URL encoding of special characters
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_url_encodes_special_characters():
    sCapturedUrl = {"sValue": ""}

    def fobjectFakeUrlOpen(objectRequest, *args, **kwargs):
        sCapturedUrl["sValue"] = objectRequest.full_url
        return _FakeResponse(b"content")

    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=fobjectFakeUrlOpen,
    ):
        githubMirror.fdictFetchRemoteHashes(
            "owner", "repo", "main",
            ["data/file with spaces.csv"],
        )
    assert "file%20with%20spaces.csv" in sCapturedUrl["sValue"]
    assert "data/" in sCapturedUrl["sValue"]


# ----------------------------------------------------------------------
# Owner / repo validation surfaces a clear ValueError
# ----------------------------------------------------------------------


def test_fdictFetchRemoteHashes_rejects_invalid_owner():
    with pytest.raises(ValueError):
        githubMirror.fdictFetchRemoteHashes(
            "owner/with/slash", "repo", "main", ["x.txt"],
        )


# ----------------------------------------------------------------------
# Keyring failure logging (Wave-1 hardening regression)
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Path traversal in URL builder (Wave-4 hardening regression)
# ----------------------------------------------------------------------


def test_build_raw_url_rejects_traversal_branch():
    """``..`` in a branch name must raise ValueError before any HTTP call."""
    with pytest.raises(ValueError, match="path traversal"):
        githubMirror._fsBuildRawUrl(
            "owner", "repo", "..", "x.txt",
        )


def test_build_raw_url_rejects_embedded_traversal_branch():
    """A branch like ``feature/../etc`` must also be rejected."""
    with pytest.raises(ValueError, match="path traversal"):
        githubMirror._fsBuildRawUrl(
            "owner", "repo", "feature/../etc", "x.txt",
        )


def test_build_raw_url_rejects_traversal_relpath():
    """A relative path containing ``..`` must be rejected at build time."""
    with pytest.raises(ValueError, match="path traversal"):
        githubMirror._fsBuildRawUrl(
            "owner", "repo", "main", "../etc/passwd",
        )


def test_build_raw_url_rejects_percent_encoded_traversal_branch():
    """``%2e%2e`` in a branch must also be rejected (case-insensitive)."""
    with pytest.raises(ValueError, match="path traversal"):
        githubMirror._fsBuildRawUrl(
            "owner", "repo", "%2E%2E", "x.txt",
        )


# ----------------------------------------------------------------------
# Authorization header stripping on cross-origin redirects (Wave-4)
# ----------------------------------------------------------------------


def test_redirect_handler_strips_auth_on_off_host_redirect():
    """A redirect to a non-raw.githubusercontent.com host drops Authorization."""
    handler = githubMirror._AuthStrippingRedirectHandler()
    objectRequest = urllib.request.Request(
        "https://raw.githubusercontent.com/o/r/main/x.txt",
        headers={"Authorization": "Bearer SECRETLEAKED"},
    )
    headers = MagicMock()
    headers.get_all = lambda *a, **k: []
    objectNew = handler.redirect_request(
        objectRequest, fp=None, code=302,
        msg="Found", headers=headers,
        newurl="https://attacker.example.com/leak",
    )
    listAuthHeaders = [
        sKey for sKey in objectNew.headers.keys()
        if sKey.lower() == "authorization"
    ]
    assert listAuthHeaders == []


def test_redirect_handler_keeps_auth_on_same_host_redirect():
    """A redirect that stays on raw.githubusercontent.com keeps Authorization."""
    handler = githubMirror._AuthStrippingRedirectHandler()
    objectRequest = urllib.request.Request(
        "https://raw.githubusercontent.com/o/r/main/x.txt",
        headers={"Authorization": "Bearer SECRETKEPT"},
    )
    headers = MagicMock()
    headers.get_all = lambda *a, **k: []
    objectNew = handler.redirect_request(
        objectRequest, fp=None, code=302,
        msg="Found", headers=headers,
        newurl="https://raw.githubusercontent.com/o/r/main/y.txt",
    )
    sAuth = objectNew.headers.get("Authorization", "")
    assert sAuth == "Bearer SECRETKEPT"


# ----------------------------------------------------------------------
# Token leak via exception chaining (Wave-4)
# ----------------------------------------------------------------------


def test_classified_http_error_has_no_chained_context():
    """``raise X from None`` ensures __cause__ / __context__ are clean."""
    errorHttp = _fobjectMakeHttpError(401)
    dictUrls = {
        "https://raw.githubusercontent.com/owner/repo/main/x.txt": errorHttp,
    }
    with patch.object(
        githubMirror.urllib.request, "urlopen",
        side_effect=_fdispatcherFromMap(dictUrls),
    ):
        try:
            githubMirror.fdictFetchRemoteHashes(
                "owner", "repo", "main", ["x.txt"],
            )
        except githubMirror.GithubMirrorError as exc:
            assert exc.__cause__ is None
            # __suppress_context__ is True when ``from None`` is used.
            assert exc.__suppress_context__ is True


def test_keyring_failure_emits_warning_log(caplog):
    """Keyring lookup failure must log WARNING with class name only.

    Regression test for the Wave-1 hardening: ``_fsResolveTokenSafely``
    falls back to anonymous fetch when keyring is broken and emits a
    single WARNING that names the exception class but never includes
    the exception message text (which could itself leak credentials).
    """
    sExceptionText = "keyring corrupt"
    with patch.object(
        githubMirror, "fsResolveToken",
        side_effect=RuntimeError(sExceptionText),
    ):
        with caplog.at_level(
            "WARNING", logger=githubMirror._LOGGER.name,
        ):
            sToken = githubMirror._fsResolveTokenSafely("owner", "repo")
    assert sToken == ""
    listWarnings = [
        recordLog for recordLog in caplog.records
        if recordLog.levelname == "WARNING"
    ]
    assert len(listWarnings) == 1
    sMessage = listWarnings[0].getMessage()
    assert "RuntimeError" in sMessage
    assert sExceptionText not in sMessage
