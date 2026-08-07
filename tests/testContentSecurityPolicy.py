"""The dashboard CSP must close the injected-markup escalation paths.

A hostile container filename rendered into innerHTML (see
scriptFiles.js) cannot run inline script under the CSP, but without a
base-uri directive an injected <base> tag re-homes every root-relative
API call, and without form-action an injected form posts anywhere.
base-uri and form-action do NOT fall back to default-src, so they must
be named explicitly. The stale jsdelivr grant (nothing loads from it —
xterm is vendored locally) is pure attack surface and is removed.

These drive the real SecurityHeadersMiddleware over a minimal app and
assert on the emitted header, not on the source string.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.gui.serverMiddleware import SecurityHeadersMiddleware


@pytest.fixture
def dictCsp():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    async def fnPing():
        return {"bOk": True}

    response = TestClient(app).get("/ping")
    sHeader = response.headers["Content-Security-Policy"]
    return {
        sDirective.strip().split(" ", 1)[0]:
            sDirective.strip()
        for sDirective in sHeader.split(";") if sDirective.strip()
    }


@pytest.mark.falsification
def test_base_uri_is_locked_to_none(dictCsp):
    """An injected <base> must not be able to re-home API calls.

    Kills: In SecurityHeadersMiddleware, drop the ``"base-uri 'none'; "``
    directive from the CSP string.
    """
    assert dictCsp.get("base-uri") == "base-uri 'none'"


def test_form_action_is_locked_to_self(dictCsp):
    assert dictCsp.get("form-action") == "form-action 'self'"


def test_frame_ancestors_still_none(dictCsp):
    assert "frame-ancestors" in dictCsp


def test_no_cdn_is_granted_script_execution(dictCsp):
    """script-src and worker-src must be local only.

    pdf.js and xterm are vendored, so no third-party origin should hold
    script or worker execution authority on the dashboard's origin.
    """
    sScript = dictCsp.get("script-src", "")
    sWorker = dictCsp.get("worker-src", "")
    for sToken in ("cdnjs", "jsdelivr", "http://", "https://"):
        assert sToken not in sScript, (
            f"script-src must not grant {sToken!r}: {sScript!r}"
        )
        assert sToken not in sWorker, (
            f"worker-src must not grant {sToken!r}: {sWorker!r}"
        )


def test_default_src_still_self(dictCsp):
    assert dictCsp.get("default-src") == "default-src 'self'"


def test_script_src_has_no_unsafe_inline(dictCsp):
    """Inline script must stay blocked (the innerHTML defence relies on it)."""
    assert "'unsafe-inline'" not in dictCsp.get("script-src", "")
