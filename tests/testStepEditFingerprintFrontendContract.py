"""Every step-edit write must carry the compare-and-swap fingerprint.

fnPutStepEdit (via the exported fnSaveStepUpdate) attaches
sBaseFingerprint so a concurrent in-container agent edit is never
silently overwritten, and the backend treats a MISSING fingerprint as
a legacy unconditional overwrite. Three call sites bypassed the
choke-point with a raw fetch/fdictPut and no fingerprint: the step
editor, the dependency scanner, and the figure viewer's verification
reset. All three now route through fnSaveStepUpdate.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsRead(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_editor_edit_routes_through_the_shared_save():
    """The editor must not PUT the step with a raw fetch."""
    sSource = _fsRead("scriptStepEditor.js")
    assert "VaibifyApp.fnSaveStepUpdate(" in sSource, (
        "the editor must persist edits through the fingerprint-bearing "
        "shared save"
    )
    # The edit branch must not hand-roll a PUT to /api/steps.
    iEdit = sSource.find('sMode === "edit"')
    iInsert = sSource.find('sMode === "insert"')
    sEditBranch = sSource[iEdit:iInsert]
    assert 'method: "PUT"' not in sEditBranch, (
        "the edit branch must not issue its own PUT (which omitted the "
        "fingerprint)"
    )
    assert "if (!dictResult)" in sEditBranch, (
        "the editor must check the save result, not assume success"
    )


def test_shared_save_returns_its_result():
    """fnSaveStepUpdate must surface the save outcome to callers."""
    sSource = _fsRead("scriptApplication.js")
    iStart = sSource.find("async function fnSaveStepUpdate(")
    assert iStart != -1
    sBlock = sSource[iStart:iStart + 400]
    assert "return await fnPutStepEdit(" in sBlock


def test_dependency_scanner_routes_through_the_shared_save():
    sSource = _fsRead("scriptDependencyScanner.js")
    iStart = sSource.find("function fnSaveDependencies(")
    sBlock = sSource[iStart:iStart + 600]
    assert "VaibifyApp.fnSaveStepUpdate(" in sBlock
    assert "VaibifyApi.fdictPut(" not in sBlock, (
        "the dependency save must not bypass the fingerprint via a bare "
        "fdictPut"
    )


def test_figure_viewer_verification_reset_routes_through_shared_save():
    sSource = _fsRead("scriptFigureViewer.js")
    # The verification-reset must not raw-fetch a PUT to /api/steps.
    assert "VaibifyApp.fnSaveStepUpdate(i, {" in sSource
    iReset = sSource.find('sUnitTest = "untested"')
    assert iReset != -1
    sAround = sSource[iReset:iReset + 400]
    assert 'method: "PUT"' not in sAround, (
        "the figure viewer must not PUT verification without the "
        "fingerprint"
    )
