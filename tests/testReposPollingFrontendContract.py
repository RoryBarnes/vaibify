"""Frontend contract checks for Phase E repos polling & choice modal.

These are string-presence tests: the repository does not run JavaScript
unit tests, so we verify the expected public API names appear in the
modified static assets.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractReturnBlock(sSource):
    iStart = sSource.rfind("return {")
    assert iStart != -1, "IIFE return block not found"
    iEnd = sSource.find("};", iStart)
    assert iEnd != -1, "IIFE return block not terminated"
    return sSource[iStart:iEnd]


def test_repos_polling_api_exposed_in_return_block():
    sSource = _fsReadStaticFile("scriptPolling.js")
    sReturnBlock = _fsExtractReturnBlock(sSource)
    assert "fnStartReposPolling" in sReturnBlock
    assert "fnStopReposPolling" in sReturnBlock
    assert "fnSetReposHandler" in sReturnBlock
    assert "/api/repos/" in sSource


def test_choice_modal_exposed_in_return_block():
    sSource = _fsReadStaticFile("scriptModals.js")
    sReturnBlock = _fsExtractReturnBlock(sSource)
    assert "fnShowChoiceModal" in sReturnBlock
    assert "function fnShowChoiceModal(" in sSource
