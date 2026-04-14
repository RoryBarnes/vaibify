"""Frontend contract checks for Phase F repos panel module.

These string-presence tests verify that the expected public API names,
DOM identifiers, and application-level wiring all remain in place for
the scriptReposPanel.js module and its integration points. JavaScript
is not executed by the repository test suite; this mirrors the pattern
used in testReposPollingFrontendContract.py.
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


def test_repos_panel_module_exists():
    sPath = os.path.join(_sStaticDir, "scriptReposPanel.js")
    assert os.path.isfile(sPath), "scriptReposPanel.js missing"


def test_repos_panel_exports_public_api():
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    sReturnBlock = _fsExtractReturnBlock(sSource)
    for sName in ("fnInit", "fnTeardown",
                  "fnHandleStatusUpdate", "fnRender"):
        assert sName in sReturnBlock, (
            sName + " missing from return block"
        )


def test_repos_panel_registers_polling_handler():
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    assert "VaibifyPolling.fnSetReposHandler" in sSource
    assert "VaibifyPolling.fnStartReposPolling" in sSource
    assert "VaibifyPolling.fnStopReposPolling" in sSource


def test_repos_panel_iife_module_name():
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    assert "var PipeleyenReposPanel" in sSource


def test_application_wires_repos_panel_lifecycle():
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "PipeleyenReposPanel.fnInit" in sSource
    assert "PipeleyenReposPanel.fnTeardown" in sSource


def test_no_workflow_mode_includes_repos_tab():
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("DICT_MODE_NO_WORKFLOW")
    assert iStart != -1
    iEnd = sSource.find("};", iStart)
    sBlock = sSource[iStart:iEnd]
    assert '"repos"' in sBlock, (
        "DICT_MODE_NO_WORKFLOW.listLeftTabs must include 'repos'"
    )


def test_index_html_has_repos_panel_dom_nodes():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="reposPanelContainer"' in sSource
    assert 'id="tabContentRepos"' in sSource
    assert 'data-panel="repos"' in sSource


def test_index_html_script_order_for_repos_panel():
    sSource = _fsReadStaticFile("index.html")
    iModals = sSource.find("scriptModals.js")
    iPolling = sSource.find("scriptPolling.js")
    iApi = sSource.find("scriptApiClient.js")
    iRepos = sSource.find("scriptReposPanel.js")
    iApp = sSource.find("scriptApplication.js")
    assert -1 not in (iModals, iPolling, iApi, iRepos, iApp)
    assert iModals < iRepos, "scriptModals must load before repos"
    assert iPolling < iRepos, "scriptPolling must load before repos"
    assert iApi < iRepos, "scriptApiClient must load before repos"
    assert iRepos < iApp, "repos must load before scriptApplication"


def test_repos_panel_functions_under_twenty_lines():
    import re
    sSource = _fsReadStaticFile("scriptReposPanel.js")
    listLines = sSource.split("\n")
    patFunc = re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(")
    iIdx = 0
    listOver = []
    while iIdx < len(listLines):
        match = patFunc.match(listLines[iIdx])
        if match:
            sName = match.group(2)
            iDepth = 0
            iStart = iIdx
            bFoundOpen = False
            while iIdx < len(listLines):
                iDepth += listLines[iIdx].count("{")
                iDepth -= listLines[iIdx].count("}")
                if "{" in listLines[iIdx]:
                    bFoundOpen = True
                if bFoundOpen and iDepth == 0:
                    break
                iIdx += 1
            iLength = iIdx - iStart + 1
            if iLength > 20:
                listOver.append((sName, iLength))
        iIdx += 1
    assert not listOver, (
        "Functions over 20 lines: " + str(listOver)
    )
