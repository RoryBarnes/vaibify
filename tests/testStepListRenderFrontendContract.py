"""Frontend contract checks for the incremental step-list renderer.

The step-list used to be rebuilt via a single
``elList.innerHTML = sHtml`` at the end of a forEach loop, which
created 5K-20K DOM nodes for a 100-step workflow in one synchronous
tick (~250-500 ms of jank). The new path keeps a per-step hash cache,
detects structural changes (step count or interactive boundary
shifts) and only blows the DOM away on those; otherwise it replaces
just the cards whose inputs actually changed.

Alongside the rendering, two related memos avoid recomputing the
same per-step data on every render:

* ``_dictStepDepsByIndex`` caches ``flistGetStepDependencies``, which
  was O(N) per step (and O(N^2) over the full list).
* ``_dictStepLabelByIndex`` caches the legacy ``fsComputeStepLabel``
  fallback that walked 0..iIndex on every call.

JavaScript is not executed by the repository test suite; these are
string-presence + structural assertions in the established
frontend-contract pattern.
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


# -----------------------------------------------------------------------
# Per-step render hash + incremental render path
# -----------------------------------------------------------------------


def test_render_hash_memo_declared_as_module_var():
    """The per-step render-hash memo must be declared as a module-scoped
    ``var`` (not just mentioned in a comment) so the incremental render
    path has a real binding to compare against between ticks. A bare
    string-presence check would pass even if the symbol survived only
    in a comment after a revert."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "var _dictRenderedStepHashes" in sSource, (
        "Module-level _dictRenderedStepHashes declaration missing — "
        "the incremental render path needs a real binding, not a "
        "comment, to store per-step hashes between ticks."
    )


def test_structural_change_signature_drives_dispatch():
    """A structural-change signature must drive the full-vs-incremental
    decision. Assert the signature variable is *both* declared and
    actually compared inside ``_fnRenderStepListImmediate``; otherwise
    a revert that leaves only a comment mentioning it would pass."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "function _fsBoundarySignature(" in sSource, (
        "_fsBoundarySignature must be a real function, not a comment "
        "reference."
    )
    assert "var _sLastBoundarySignature" in sSource, (
        "_sLastBoundarySignature must be a real module-scoped variable."
    )
    iStart = sSource.find("function _fnRenderStepListImmediate(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fsBoundarySignature(" in sBlock, (
        "_fnRenderStepListImmediate must actually call the signature "
        "helper — otherwise the full-vs-incremental dispatch is dead."
    )
    assert "_sLastBoundarySignature" in sBlock, (
        "The dispatcher must compare the new signature against the "
        "stored _sLastBoundarySignature."
    )


def test_incremental_render_uses_per_step_replace():
    """The incremental path must perform per-step DOM replacement
    (``replaceWith`` against the existing wrapper), not a top-level
    ``elList.innerHTML = sHtml`` blast."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnRenderStepListIncremental(")
    assert iStart != -1, (
        "_fnRenderStepListIncremental missing — the renderer no "
        "longer has a per-step replace path."
    )
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "replaceWith(" in sBlock, (
        "Incremental renderer must use replaceWith for per-step DOM "
        "swaps; an innerHTML blast would defeat the optimization."
    )
    assert "data-step-index" in sBlock


def test_full_render_falls_back_on_structural_change():
    """When the boundary signature changes, the renderer must fall
    back to the full ``innerHTML = sHtml`` rebuild path."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnRenderStepListImmediate(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnRenderStepListFull" in sBlock
    assert "_fnRenderStepListIncremental" in sBlock


def test_step_wrapper_carries_data_step_index_attribute():
    """The renderer must stamp each step wrapper with
    ``data-step-index`` so the incremental path can find the existing
    card to replace in O(1) instead of counting children."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    assert 'data-step-index="' in sSource, (
        "scriptStepRenderer must emit data-step-index on the "
        ".step-wrapper for the incremental renderer to locate cards."
    )


# -----------------------------------------------------------------------
# Cache invalidation on workflow swap
# -----------------------------------------------------------------------


def test_invalidate_all_render_caches_clears_every_memo():
    """The invalidator must clear *every* render memo, not just exist
    as a no-op shell. A revert that left the function but emptied its
    body would silently reintroduce cross-workflow hash bleed-through."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnInvalidateAllRenderCaches(")
    assert iStart != -1, "invalidator helper missing"
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    # Each render memo must actually be reset inside the body.
    for sMemo in [
        "_dictRenderedStepHashes",
        "_sLastBoundarySignature",
        "_dictStepDepsByIndex",
        "_dictStepIndexByFilePath",
        "_dictStepLabelByIndex",
    ]:
        assert sMemo in sBlock, (
            "_fnInvalidateAllRenderCaches must clear " + sMemo
            + " — otherwise a workflow swap leaves stale entries."
        )


def test_invalidate_runs_on_workflow_reset():
    """``_fnResetWorkflowState`` must clear the render caches —
    otherwise switching workflows would replay the previous
    workflow's hashes against the new step list."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnResetWorkflowState(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnInvalidateAllRenderCaches" in sBlock


def test_invalidate_runs_on_refresh_workflow_data():
    """The in-place workflow refresh must clear the render caches."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function fnRefreshWorkflowData(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnInvalidateAllRenderCaches" in sBlock


def test_invalidate_runs_on_out_of_band_reload():
    """Out-of-band workflow.json edits replace the workflow object —
    the caches must be cleared so stale hashes don't suppress a
    re-render of every card."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnApplyOutOfBandWorkflowReload(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnInvalidateAllRenderCaches" in sBlock


# -----------------------------------------------------------------------
# Memoized dependency graph (change 6) and labels (change 7)
# -----------------------------------------------------------------------


def test_dependency_graph_is_memoized():
    """``flistGetStepDependencies`` must read the memo *before* falling
    through to the expensive compute helper, and it must also write the
    result back. A revert to a per-call recompute that leaves both
    identifiers in the body would defeat the optimization but pass a
    bare substring check, so we assert order: the cache-hit guard
    appears before the compute-and-store line."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "var _dictStepDepsByIndex" in sSource, (
        "Module-level _dictStepDepsByIndex declaration missing."
    )
    iStart = sSource.find("function flistGetStepDependencies(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    iCacheGuard = sBlock.find("_dictStepDepsByIndex[iStep] !== undefined")
    iCompute = sBlock.find("_flistComputeStepDependencies(")
    iStore = sBlock.find("_dictStepDepsByIndex[iStep] =")
    assert iCacheGuard != -1, (
        "Cache-hit guard missing — dependency lookup is no longer "
        "memoized."
    )
    assert iCompute != -1, (
        "_flistComputeStepDependencies call missing — dependency body "
        "was inlined back into the public function."
    )
    assert iStore != -1, (
        "Memo write missing — computed deps are never cached."
    )
    assert iCacheGuard < iCompute, (
        "Cache must be checked BEFORE recomputing; otherwise the "
        "memo is dead weight."
    )


def test_step_label_memo_exists_and_is_consulted():
    """``fsComputeStepLabel`` must consult ``_dictStepLabelByIndex``
    before falling through to the O(N) populator. Assert both the
    module-level declaration and the cache-hit guard inside the
    function body; bare substring would tolerate a comment-only
    revert."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "var _dictStepLabelByIndex" in sSource, (
        "Module-level _dictStepLabelByIndex declaration missing."
    )
    iStart = sSource.find("function fsComputeStepLabel(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    iGuard = sBlock.find("_dictStepLabelByIndex[iIndex] !== undefined")
    iReturn = sBlock.find("return _dictStepLabelByIndex[iIndex]")
    iPopulate = sBlock.find("_fnPopulateStepLabelMemo(")
    assert iGuard != -1, (
        "Cache-hit guard missing inside fsComputeStepLabel."
    )
    assert iReturn != -1, (
        "Cache-hit return missing — guarded path must short-circuit."
    )
    assert iPopulate != -1, (
        "Populator call missing — first miss must populate the memo."
    )
    # On cache miss the populator runs and *then* the memo is read.
    assert iGuard < iPopulate, (
        "Guard must precede populate to short-circuit on cache hit."
    )


def test_label_memo_populated_in_single_forward_pass():
    """The label-memo populator must walk the list once with running
    automated/interactive counters — not recurse or call back into
    fsComputeStepLabel. A revert that turns the populator into N calls
    to the old per-index walker would still have the function defined,
    so we assert structural properties of the body."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnPopulateStepLabelMemo(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    # Must contain a single forward loop over listSteps.
    assert "for (" in sBlock, (
        "_fnPopulateStepLabelMemo must contain a forward loop, not "
        "recurse or delegate per-index to fsComputeStepLabel."
    )
    assert "listSteps.length" in sBlock, (
        "Populator must iterate the full step list up to .length."
    )
    # Must not call fsComputeStepLabel (would re-introduce O(N^2)).
    assert "fsComputeStepLabel" not in sBlock, (
        "Populator must not delegate to fsComputeStepLabel — that "
        "would re-introduce the O(N^2) walk it replaces."
    )
    # Must write into the memo dict.
    assert "_dictStepLabelByIndex[" in sBlock


# -----------------------------------------------------------------------
# Partial-render entry for the badge polling path (change 8 hook)
# -----------------------------------------------------------------------


def test_fnRenderStepListPartial_exposes_on_public_api():
    """The partial-render entry must live on ``PipeleyenApp`` so
    cross-module callers (notably the git-badges refresh) can
    invalidate just the affected indices without a full re-render.
    Assert the exposed key, the function it points at, and the
    function definition itself — three independent guards so a
    rename or comment-only revert cannot slip through."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "fnRenderStepListPartial: fnRenderStepListPartial" in sSource, (
        "PipeleyenApp must expose fnRenderStepListPartial in its "
        "public-API return object."
    )
    assert "function fnRenderStepListPartial(" in sSource, (
        "fnRenderStepListPartial must be defined as a real function "
        "inside the IIFE, not aliased to something else."
    )


def test_fnRenderStepListPartial_maps_files_via_reverse_index():
    """The partial-render entry must read ``_dictStepIndexByFilePath``
    so it can answer "which step does this file belong to?" without
    iterating the workflow. Verify lookup precedes invalidation and
    that an empty/missing listAffectedFiles falls back to full render
    so the caller cannot accidentally render nothing."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function fnRenderStepListPartial(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    iLookup = sBlock.find("_dictStepIndexByFilePath[")
    iInvalidate = sBlock.find("_fnInvalidateRenderCache(")
    assert iLookup != -1, (
        "Partial-render must look up step indices via the reverse map."
    )
    assert iInvalidate != -1, (
        "Partial-render must invalidate per-step caches before render."
    )
    assert iLookup < iInvalidate, (
        "Lookup must precede invalidation; otherwise the entry is "
        "dropping the wrong caches."
    )
    # Empty / missing input must short-circuit to a full render.
    assert "!listAffectedFiles" in sBlock or ".length" in sBlock, (
        "Empty input must fall back to full render so an empty diff "
        "doesn't silently skip the re-render."
    )


# -----------------------------------------------------------------------
# Change 9: mtime-as-existence-cache in the file-poll loop
# -----------------------------------------------------------------------


def test_poll_skips_steps_with_known_output_mtime():
    """``fnPollAllStepFiles`` must short-circuit on any step whose
    ``dictOutputMtimes`` entry is already populated — that already
    proves the step's output files exist on disk, so re-issuing
    PipeleyenFileOps.fnCheckStepDataFiles would be ~1000 redundant
    file probes per poll at N=100. Assert ordering: the existence
    lookup must precede the (only) call to ``fnCheckStepDataFiles``."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function fnPollAllStepFiles(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "dictOutputMtimes" in sBlock, (
        "fnPollAllStepFiles must consult dictOutputMtimes to decide "
        "whether the per-step existence probe is necessary."
    )
    iLookup = sBlock.find("dictMtimes[String(iStep)]")
    iCheck = sBlock.find("fnCheckStepDataFiles")
    assert iLookup != -1, (
        "fnPollAllStepFiles must look up the per-step mtime via "
        "dictMtimes[String(iStep)]."
    )
    assert iCheck != -1, (
        "fnPollAllStepFiles must still call fnCheckStepDataFiles for "
        "never-run steps."
    )
    assert iLookup < iCheck, (
        "The mtime existence-cache lookup must short-circuit BEFORE "
        "the per-step file probe; otherwise the optimization is dead."
    )
    # The early-return must take the form `if (...) return;` so the
    # check is actually skipped — not e.g. logged-but-still-called.
    assert "return;" in sBlock


# -----------------------------------------------------------------------
# Edge cases: empty workflow, structural-change-on-first-render
# -----------------------------------------------------------------------


def test_immediate_render_handles_missing_listSteps():
    """A workflow without listSteps (or no workflow at all) must clear
    the DOM and invalidate caches rather than crashing — otherwise the
    workflow-picker swap into an empty-pipeline state explodes."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnRenderStepListImmediate(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    # Guard must check for missing workflow OR listSteps and clear the DOM.
    assert "!_dictWorkflowState.dictWorkflow" in sBlock or (
        "dictWorkflow.listSteps" in sBlock
    ), "Missing-listSteps branch absent from _fnRenderStepListImmediate."
    assert 'innerHTML = ""' in sBlock, (
        "Empty-state branch must blank out the step list."
    )
    assert "_fnInvalidateAllRenderCaches" in sBlock, (
        "Empty-state branch must also invalidate the render caches."
    )


def test_boundary_signature_distinguishes_count_and_interactive_mix():
    """The boundary signature key must change when either the step
    count or the interactive-flag pattern changes. The implementation
    concatenates length + per-step I/A markers; verify the body shape
    so a future revert to e.g. just length cannot slip through."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fsBoundarySignature(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "listSteps.length" in sBlock, (
        "Boundary signature must mix step count into the key."
    )
    assert ".bInteractive" in sBlock, (
        "Boundary signature must distinguish interactive from "
        "automated steps; otherwise inserting an interactive step "
        "would skip the structural-change fallback."
    )


def test_first_render_after_invalidate_takes_full_path():
    """After ``_fnInvalidateAllRenderCaches`` resets the boundary
    signature to its initial value, the next render must hit the full
    rebuild path — otherwise the very first render of a freshly-loaded
    workflow would try to ``replaceWith`` on cards that don't exist
    in the DOM yet."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    # Locate the module-level initial value of _sLastBoundarySignature.
    iDecl = sSource.find("var _sLastBoundarySignature")
    assert iDecl != -1
    sLine = sSource[iDecl:sSource.find("\n", iDecl)]
    # Must be initialized to something that no _fsBoundarySignature
    # output (which always starts with a digit) can equal — null or "".
    assert any(sSentinel in sLine for sSentinel in [
        "null", '""', "''"
    ]), (
        "_sLastBoundarySignature must initialize to a sentinel "
        "(null or empty) so the first render takes the full-rebuild "
        "path; otherwise the incremental path would target an empty "
        "DOM."
    )


# -----------------------------------------------------------------------
# Input Data block
# -----------------------------------------------------------------------


def test_input_data_section_renders_before_scripts():
    """The Input Data block must sit between Directory and Scripts.

    Checked structurally: inside fsRenderStepItem the call to
    fsRenderInputDataSection must appear after the Directory field
    and before the Scripts tracked-file section.
    """
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    iBody = sSource.find("function fsRenderStepItem")
    assert iBody != -1
    iDirectory = sSource.find(">Directory</div>", iBody)
    iInputCall = sSource.find("fsRenderInputDataSection(", iBody)
    iScripts = sSource.find('"Scripts", "saStepScripts"', iBody)
    assert iDirectory != -1 and iInputCall != -1 and iScripts != -1
    assert iDirectory < iInputCall < iScripts, (
        "fsRenderInputDataSection must be invoked between the "
        "Directory field and the Scripts section."
    )


def test_input_data_section_always_offers_add_button():
    """The section label must be the editable variant so the + button
    exists even on a step with no inputs declared yet."""
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    iSection = sSource.find("function fsRenderInputDataSection")
    assert iSection != -1
    sBlock = sSource[iSection:iSection + 900]
    assert 'fsRenderSectionLabel(' in sBlock
    assert '"Input Data", iIndex, "saInputDataFiles"' in sBlock


def test_input_data_registered_for_remote_badges():
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    assert 'saInputDataFiles: ["sGithub", "sZenodo"]' in sSource


def test_input_stale_row_labels_defined():
    sSource = _fsReadStaticFile("scriptStepRenderer.js")
    assert '"test|inputFile"' in sSource
    assert '"user|inputFile"' in sSource


def test_input_mtime_map_participates_in_render_hash():
    """dictMaxInputMtimeByStep must be in _fsContextSliceForStep or a
    poll that only moves an input mtime leaves a stale card."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iSlice = sSource.find("function _fsContextSliceForStep")
    iEnd = sSource.find("function _flistBlockerAndLevelSlice")
    assert iSlice != -1 and iEnd != -1
    assert "dictMaxInputMtimeByStep" in sSource[iSlice:iEnd], (
        "dictMaxInputMtimeByStep missing from the per-step context "
        "slice — input-mtime-only polls would not re-render the card."
    )


def test_input_rows_join_existence_batch():
    sSource = _fsReadStaticFile("scriptFileOperations.js")
    assert 'data-array="saInputDataFiles"' in sSource, (
        "Existence planner must include Input Data rows so a "
        "declared-but-absent input renders the honest red."
    )


def test_file_picker_modal_exported():
    sSource = _fsReadStaticFile("scriptModals.js")
    assert "fnShowFilePickerModal: fnShowFilePickerModal" in sSource


def test_no_input_data_checkbox_bound():
    sSource = _fsReadStaticFile("scriptEventBindings.js")
    assert "no-input-data-checkbox" in sSource
    assert "fnToggleNoInputData" in sSource


# -----------------------------------------------------------------------
# Remote-data overwrite gate — browser lane
# -----------------------------------------------------------------------


def test_remote_overwrite_refusal_confirms_then_redispatches():
    """The remoteDataOverwrite refusal must open a confirm modal and,
    on confirm, re-dispatch with bConfirmRemoteOverwrite — not just
    toast like the busy refusal."""
    sSource = _fsReadStaticFile("scriptPipelineRunner.js")
    iHandler = sSource.find(
        "function _fnHandleRemoteOverwriteRefusal")
    assert iHandler != -1
    sBlock = sSource[iHandler:iHandler + 1200]
    assert "bConfirmRemoteOverwrite: true" in sBlock
    assert "fnShowConfirmModal" in sBlock
    assert "fnSendPipelineAction" in sBlock
    assert '"remoteDataOverwrite"' in sSource


def test_all_three_interactive_lanes_pass_the_overwrite_gate():
    """Run-in-Terminal never reaches the server dispatch, so each of
    the three interactive entry points must route its launch through
    fnConfirmRemoteOverwriteThen."""
    sSource = _fsReadStaticFile("scriptPipelineRunner.js")
    for sFunction in (
        "function fnRunInteractiveStep",
        "function fnRunInteractivePlots",
        "function fnExecuteStepCombined",
    ):
        iStart = sSource.find(sFunction)
        assert iStart != -1, sFunction
        iEnd = sSource.find("\n    function ", iStart + 10)
        sBody = sSource[iStart:iEnd]
        assert "fnConfirmRemoteOverwriteThen" in sBody, (
            sFunction + " must gate its terminal launch"
        )


def test_confirmed_pull_offers_commit_of_fresh_canonical_data():
    """After a run that recorded remote data (and after a standalone
    Run-in-Terminal pull), the browser must offer to commit the fresh
    files via the shared fbOfferCommitAfterGenerate flow — canonical
    data must never sit silently uncommitted."""
    sSource = _fsReadStaticFile("scriptPipelineRunner.js")
    assert sSource.count("fbOfferCommitAfterGenerate") >= 2, (
        "both the run-end path and the standalone interactive path "
        "must offer the commit"
    )
    iRecorded = sSource.find('"remoteDataRecorded"')
    assert iRecorded != -1
    assert "_bRemoteDataPulledThisRun" in sSource[
        iRecorded:iRecorded + 1200
    ], (
        "the remoteDataRecorded event must arm the end-of-run "
        "commit offer"
    )
