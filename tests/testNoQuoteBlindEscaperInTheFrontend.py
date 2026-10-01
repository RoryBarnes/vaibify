"""No frontend module may escape HTML with the textContent/innerHTML idiom.

Source: ``vaibify/gui/static/*.js``.

``el.textContent = s; return el.innerHTML`` escapes ``&``, ``<`` and
``>`` and leaves ``"`` and ``'`` alone. That is correct for text between
tags and wrong inside an attribute value, where one quote closes the
attribute and lets the rest of the string add its own. A static scan
cannot tell which of a helper's callers sit in an attribute, so the idiom
is banned outright: every module escapes through
``VaibifyUtilities.fnEscapeHtml``, which escapes both quotes, and a
constructed element should take ``setAttribute`` / ``textContent`` rather
than concatenated markup.
"""

import pathlib
import re

import pytest

PATH_STATIC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "vaibify" / "gui" / "static"
)

REGEX_QUOTE_BLIND_IDIOM = re.compile(
    r"\.textContent\s*=[^;]+;\s*return\s+\w+\.innerHTML\b"
)

TUPLE_DELEGATING_MODULES = (
    "scriptCouncilSnapshotScope.js", "scriptCouncilConsent.js",
    "scriptAgentCouncil.js", "scriptSetupWizard.js",
)


def _flistFindIdiom(sSource):
    return [matchIdiom.start() for matchIdiom in
            REGEX_QUOTE_BLIND_IDIOM.finditer(sSource)]


def _flistOwnScripts():
    return sorted(PATH_STATIC.glob("script*.js"))


@pytest.mark.falsification
def testNoModuleEscapesWithTheTextContentInnerHtmlIdiom():
    """Kills: reintroducing the idiom in any frontend module."""
    listOffenders = [
        pathScript.name for pathScript in _flistOwnScripts()
        if _flistFindIdiom(pathScript.read_text(encoding="utf-8"))
    ]
    assert listOffenders == [], (
        "these modules escape with textContent -> innerHTML, which leaves "
        "double quotes alone and so is unsafe in an attribute value; use "
        f"VaibifyUtilities.fnEscapeHtml: {listOffenders}"
    )


def testTheScanCanFail():
    sOldCouncilEscaper = (
        "function _fsEscape(sText) {\n"
        '    var elDiv = document.createElement("div");\n'
        "    elDiv.textContent = sText === undefined || sText === null\n"
        '        ? "" : String(sText);\n'
        "    return elDiv.innerHTML;\n"
        "}\n"
    )
    sOldWizardEscaper = (
        "function fnEscapeHtml(sText) {\n"
        '    var el = document.createElement("span");\n'
        "    el.textContent = sText;\n"
        "    return el.innerHTML;\n"
        "}\n"
    )
    assert _flistFindIdiom(sOldCouncilEscaper)
    assert _flistFindIdiom(sOldWizardEscaper)
    assert not _flistFindIdiom(
        "function _fsEscape(s) { return VaibifyUtilities.fnEscapeHtml(s); }")


def testTheFourEscapersDelegateToTheSharedOne():
    for sName in TUPLE_DELEGATING_MODULES:
        sSource = (PATH_STATIC / sName).read_text(encoding="utf-8")
        assert "VaibifyUtilities.fnEscapeHtml(sText)" in sSource, sName


def testTheSetupWizardPageLoadsTheSharedEscaperBeforeItsOwnScript():
    sPage = (PATH_STATIC / "setupWizard.html").read_text(encoding="utf-8")
    assert (
        sPage.index("scriptUtilities.js") < sPage.index("scriptSetupWizard.js")
    )
