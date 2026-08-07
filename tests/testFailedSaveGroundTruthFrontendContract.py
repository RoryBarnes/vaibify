"""A failed step save must never leave an un-persisted edit on screen.

Callers across many modules mutate local step state optimistically
(for input stickiness) and route the persist through fnPutStepEdit,
which returns null on failure. The callers did not roll back, and two
of them (add-item, undo) showed a success toast regardless — so a
failed save could display a green "Item added" beside the red "Save
failed", with the un-persisted change left in the dashboard forever
(the out-of-band reload only fires when the disk actually changed).

The fix is systemic: fnPutStepEdit re-syncs the workflow from the
server on ANY failure, not only on 409, so every caller's optimistic
edit is corrected from ground truth; and the two success toasts are
gated on the save result.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadApp():
    sPath = os.path.join(_sStaticDir, "scriptApplication.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractFunctionBlock(sSource, sSignature):
    iStart = sSource.find(sSignature)
    assert iStart != -1, sSignature + " missing from source"
    iNext = sSource.find("\n    function ", iStart + 1)
    iNextAsync = sSource.find("\n    async function ", iStart + 1)
    iEnd = min(x for x in (iNext, iNextAsync, len(sSource)) if x != -1)
    return sSource[iStart:iEnd]


def test_failed_save_resyncs_on_every_failure_not_only_409():
    """The re-sync must sit outside the 409-only branch."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadApp(), "async function fnPutStepEdit(",
    )
    iRefresh = sBlock.find("fnRefreshWorkflow()")
    assert iRefresh != -1, "fnPutStepEdit must re-sync on failure"
    # The refresh must not be nested inside the `if (409)` block; the
    # else/non-409 path must reach it. It is placed after the toast
    # branch, at the catch body's top level, so it runs for every error.
    iElse = sBlock.find("} else {")
    iElseClose = sBlock.find("}", sBlock.find("Save failed"))
    assert iRefresh > iElseClose, (
        "the re-sync must run after the if/else toast split, i.e. on "
        "every failure, not only the 409 branch"
    )


def test_add_item_success_toast_is_gated_on_the_result():
    """"Item added" must not fire after a failed save."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadApp(), "async function fnCommitNewItem(",
    )
    iSave = sBlock.find("fnSaveStepArray(")
    iGuard = sBlock.find("if (!dictResult) return;")
    iToast = sBlock.find('fnShowToast("Item added"')
    assert -1 < iSave < iGuard < iToast, (
        "fnCommitNewItem must check the save result before claiming "
        "the item was added"
    )


def test_undo_success_toast_is_gated_and_reverts_the_stack():
    """"Undone" must not fire after a failed save, and the action returns."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadApp(), "async function fnUndo(",
    )
    assert "if (!dictResult)" in sBlock
    assert "listUndoStack.push(dictAction)" in sBlock, (
        "a failed undo must return the action to the stack for retry"
    )
    iGuard = sBlock.find("if (!dictResult)")
    iToast = sBlock.find('fnShowToast("Undone"')
    assert -1 < iGuard < iToast


def test_save_step_array_returns_the_result():
    """The shared array-save primitive must surface the save result."""
    sBlock = _fsExtractFunctionBlock(
        _fsReadApp(), "async function fnSaveStepArray(",
    )
    assert "return dictResult" in sBlock


# --- success-only mutation (outage-proof): mutate the workflow dict
#     only AFTER the save lands, so a failed save (even when the re-sync
#     also fails on a total outage) leaves no un-persisted state. ---

def _fsBlock(sName):
    sSource = _fsReadApp()
    iStart = sSource.find(sName)
    assert iStart != -1, sName + " missing"
    iNext = sSource.find("\n    async function ", iStart + 1)
    iPlain = sSource.find("\n    function ", iStart + 1)
    iEnd = min(x for x in (iNext, iPlain, len(sSource)) if x != -1)
    return sSource[iStart:iEnd]


def test_set_step_budget_is_success_only():
    sBlock = _fsBlock("async function fnSetStepBudget(")
    iPut = sBlock.find("fnPutStepEdit(")
    iGuard = sBlock.find("if (!dictResult) return;")
    iMutate = sBlock.find(".fWallClockBudgetSeconds = fBudget;")
    assert -1 < iPut < iGuard < iMutate, (
        "budget must persist before mutating the workflow dict"
    )


def test_toggle_plot_only_is_success_only():
    sBlock = _fsBlock("async function fnTogglePlotOnly(")
    iPut = sBlock.find("fnPutStepEdit(")
    iMutate = sBlock.find(".bPlotOnly =\n")
    if iMutate == -1:
        iMutate = sBlock.find(".bPlotOnly =")
    assert -1 < iPut < iMutate
    assert "if (!dictResult)" in sBlock


def test_cycle_user_verification_is_success_only():
    sBlock = _fsBlock("async function fnCycleUserVerification(")
    iPut = sBlock.find("fnPutStepEdit(")
    iGuard = sBlock.find("if (!dictResult) return;")
    iMutate = sBlock.find("dictStep.dictVerification = dictNext;")
    assert -1 < iPut < iGuard < iMutate, (
        "a 'passed' badge must never be shown for a save that did not land"
    )


def test_add_discovered_output_is_success_only():
    sBlock = _fsBlock("async function fnAddDiscoveredOutput(")
    iSave = sBlock.find("fnSaveStepUpdate(")
    iGuard = sBlock.find("if (!dictResult) return;")
    iMutate = sBlock.find("dictStep[sTargetArray] = listProposed;")
    assert -1 < iSave < iGuard < iMutate


def test_description_save_is_success_only():
    sSource = _fsReadApp()
    # Anchor on the mutation and look back: the persist and its guard
    # must both precede the local mutation of the description.
    iMutate = sSource.find("step.sDescription = sText;")
    assert iMutate != -1
    sBefore = sSource[iMutate - 400:iMutate]
    assert "fnPutStepEdit(" in sBefore
    assert "if (!dictResult) return;" in sBefore
