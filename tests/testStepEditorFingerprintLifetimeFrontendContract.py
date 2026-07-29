"""The step editor must submit the fingerprint captured when it opened.

The concurrency fix that only routed the editor through the shared save
still raced: the save attached whatever the TRACKED (global) fingerprint
was at save time, which the background poll advances to a concurrent
agent's new version — so a modal opened on version A could save stale A
fields under B's fingerprint and the backend's compare-and-swap would
accept the overwrite. The modal now snapshots the fingerprint at open
and submits that exact value, so a stale save is refused (409) instead.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern. The backend
CAS itself (stale fingerprint → 409) is covered by
testStepRoutes.testFingerprintMismatchConflictsRegardlessOfSortOrder,
and the browser lane proves the modules load and evaluate.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsRead(sName):
    with open(os.path.join(_sStaticDir, sName), "r",
              encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_app_exports_a_fingerprint_accessor():
    sApp = _fsRead("scriptApplication.js")
    assert "fsGetWorkflowFingerprint: function" in sApp


def test_put_step_edit_lets_caller_fingerprint_win():
    """The tracked default must be the FIRST Object.assign source.

    Object.assign(target-with-default, dictUpdate) lets a caller-provided
    sBaseFingerprint in dictUpdate override the default; the reverse
    order would clobber the captured value with the current tracked one.
    """
    sApp = _fsRead("scriptApplication.js")
    iStart = sApp.find("async function fnPutStepEdit(")
    iEnd = sApp.find("\n    async function ", iStart + 1)
    sBlock = sApp[iStart:iEnd]
    iAssign = sBlock.find("Object.assign(")
    iDefault = sBlock.find("sWorkflowFingerprint", iAssign)
    iUpdate = sBlock.find("dictUpdate)", iAssign)
    assert -1 < iAssign < iDefault < iUpdate, (
        "the tracked-fingerprint default must be the first assign "
        "source and dictUpdate second, so a captured fingerprint wins"
    )


def test_editor_captures_fingerprint_at_open():
    sEditor = _fsRead("scriptStepEditor.js")
    iOpen = sEditor.find("function fnOpenEditModal(")
    iNext = sEditor.find("\n    function ", iOpen + 1)
    sBlock = sEditor[iOpen:iNext]
    assert "sEditBaseFingerprint = VaibifyApp.fsGetWorkflowFingerprint()" \
        in sBlock, "the edit modal must snapshot the fingerprint at open"


def test_editor_submits_the_captured_fingerprint():
    sEditor = _fsRead("scriptStepEditor.js")
    iSave = sEditor.find("async function fnSave(")
    sBlock = sEditor[iSave:iSave + 2000]
    iSet = sBlock.find("dictData.sBaseFingerprint = sEditBaseFingerprint")
    iCall = sBlock.find("VaibifyApp.fnSaveStepUpdate(")
    assert -1 < iSet < iCall, (
        "the captured fingerprint must be attached before the save call"
    )
