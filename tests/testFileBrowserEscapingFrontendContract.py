"""The file browser must escape container filenames before innerHTML.

scriptFiles.js concatenated raw ``entry.sName`` / ``entry.sPath`` and
breadcrumb components into innerHTML. A container filename is
attacker-influenceable — a cloned repo, a downloaded dataset, an
extracted tarball — so a hostile name injected markup (a <base> tag,
spoofed UI). The sibling scriptDirectoryBrowser.js escapes every such
value; scriptFiles.js was the omission, not the convention.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern, paired with
the CSP tests and the browser lane that proves the module evaluates.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadFiles():
    sPath = os.path.join(_sStaticDir, "scriptFiles.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsFunctionBlock(sSource, sName):
    iStart = sSource.find("function " + sName)
    assert iStart != -1, sName + " missing"
    iNext = sSource.find("\n    function ", iStart + 1)
    return sSource[iStart:iNext if iNext != -1 else len(sSource)]


def test_file_list_escapes_name_and_path():
    sBlock = _fsFunctionBlock(_fsReadFiles(), "fnRenderFileList")
    assert "VaibifyUtilities.fnEscapeHtml(entry.sName)" in sBlock, (
        "the file name must be escaped before innerHTML"
    )
    assert "VaibifyUtilities.fnEscapeHtml(entry.sPath)" in sBlock, (
        "the data-path attribute must be escaped before innerHTML"
    )


def test_file_list_does_not_concat_raw_entry_name():
    sBlock = _fsFunctionBlock(_fsReadFiles(), "fnRenderFileList")
    assert "'>' + entry.sName" not in sBlock
    assert '" + entry.sName + "' not in sBlock, (
        "no raw entry.sName may reach innerHTML"
    )
    assert 'data-path="' + "' + entry.sPath" not in sBlock


def test_breadcrumb_escapes_each_component():
    sBlock = _fsFunctionBlock(_fsReadFiles(), "fnRenderBreadcrumb")
    assert "VaibifyUtilities.fnEscapeHtml(sPart)" in sBlock
    assert "VaibifyUtilities.fnEscapeHtml(sPathCopy)" in sBlock
    assert "'>' + sPart +" not in sBlock, (
        "the breadcrumb label must be escaped, not raw"
    )
