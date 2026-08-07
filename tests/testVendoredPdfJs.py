"""PDF.js must be vendored locally and authentic, not loaded from a CDN.

A remote <script> in the dashboard's origin runs with the dashboard's
authority (session token, container-control routes), so a CDN
compromise or MITM inherited full control; a network-isolated install
also silently lost PDF support. PDF.js and its worker are now served
from /static/vendor. These tests pin the files' SHA-512 to the upstream
cdnjs SRI for 3.11.174, so a corrupt or swapped vendored blob fails
CI, and assert nothing in the frontend still points at a CDN for them.
"""

import base64
import hashlib
import os

import pytest

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)
_sVendorDir = os.path.join(_sStaticDir, "vendor")

# Upstream cdnjs SRI (sha512) for pdf.js 3.11.174. Authenticity anchor:
# a vendored file whose hash differs from what cdnjs published for this
# version is not the file it claims to be.
_DICT_EXPECTED_SRI = {
    "pdf.min.js":
        "q+4liFwdPC/bNdhUpZx6aXDx/h77yEQtn4I1slHydcbZK34nLaR3cAeYSJ"
        "shoxIOq3mjEf7xJE8YWIUHMn+oCQ==",
    "pdf.worker.min.js":
        "BbrZ76UNZq5BhH7LL7pn9A4TKQpQeNCHOo65/akfelcIBbcVvYWOFQKPXI"
        "rykE3qZxYjmDX573oa4Ywsc7rpTw==",
}


def _fsSha512Base64(sPath):
    with open(sPath, "rb") as fileHandle:
        return base64.b64encode(
            hashlib.sha512(fileHandle.read()).digest()
        ).decode("ascii")


@pytest.mark.parametrize("sName", sorted(_DICT_EXPECTED_SRI))
def test_vendored_pdf_file_is_present_and_authentic(sName):
    sPath = os.path.join(_sVendorDir, sName)
    assert os.path.isfile(sPath), f"{sName} must be vendored locally"
    assert _fsSha512Base64(sPath) == _DICT_EXPECTED_SRI[sName], (
        f"{sName} does not match the pinned upstream SHA-512; a corrupt "
        f"or swapped vendored blob"
    )


def _fsRead(sName):
    with open(os.path.join(_sStaticDir, sName), "r",
              encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_index_loads_pdf_from_vendor_not_cdn():
    sHtml = _fsRead("index.html")
    assert '/static/vendor/pdf.min.js' in sHtml
    assert "cdnjs.cloudflare.com" not in sHtml, (
        "no dashboard asset may load from a CDN"
    )


def test_figure_viewer_worker_is_local():
    sSource = _fsRead("scriptFigureViewer.js")
    assert "/static/vendor/pdf.worker.min.js" in sSource
    assert "cdnjs.cloudflare.com" not in sSource


def test_vendor_dir_is_packaged():
    """The wheel must ship the vendored assets (static/vendor/*)."""
    sPyproject = os.path.join(
        os.path.dirname(_sStaticDir), "..", "..", "pyproject.toml",
    )
    with open(sPyproject, "r", encoding="utf-8") as fileHandle:
        sText = fileHandle.read()
    assert "static/vendor/*" in sText, (
        "static/vendor must be declared as package data or the wheel "
        "ships without pdf.js"
    )
