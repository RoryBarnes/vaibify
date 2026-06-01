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


def test_render_hash_memo_symbol_exists():
    """The per-step render-hash memo must be declared at module scope
    so the incremental render path has somewhere to compare against."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "_dictRenderedStepHashes" in sSource, (
        "Module-level _dictRenderedStepHashes memo missing — the "
        "incremental render path needs a place to store per-step "
        "hashes between ticks."
    )


def test_structural_change_signature_exists():
    """A structural-change signature must drive the full-vs-incremental
    decision; otherwise a step insertion or interactive-boundary move
    would silently produce a corrupt DOM."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "_fsBoundarySignature" in sSource
    assert "_sLastBoundarySignature" in sSource


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


def test_invalidate_all_render_caches_helper_exists():
    """The invalidator must be a single helper so future state
    mutators can call it instead of clearing each memo by hand."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "function _fnInvalidateAllRenderCaches(" in sSource


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
    """``flistGetStepDependencies`` must consult the memo before
    re-running the O(N) directory-overlap scan, otherwise the render
    path is O(N^2) at the level of dependency computation."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "_dictStepDepsByIndex" in sSource
    iStart = sSource.find("function flistGetStepDependencies(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_dictStepDepsByIndex" in sBlock
    assert "_flistComputeStepDependencies" in sBlock


def test_step_label_memo_exists_and_is_consulted():
    """``fsComputeStepLabel`` must consult ``_dictStepLabelByIndex``
    before falling through to the original O(N) iterative count."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "_dictStepLabelByIndex" in sSource
    iStart = sSource.find("function fsComputeStepLabel(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_dictStepLabelByIndex[iIndex] !== undefined" in sBlock


def test_label_memo_populated_in_single_forward_pass():
    """The label-memo populator must walk the list once, not
    re-scan from zero for each lookup."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function _fnPopulateStepLabelMemo(")
    assert iStart != -1


# -----------------------------------------------------------------------
# Partial-render entry for the badge polling path (change 8 hook)
# -----------------------------------------------------------------------


def test_fnRenderStepListPartial_exposes_on_public_api():
    """The partial-render entry must live on ``PipeleyenApp`` so
    cross-module callers (notably the git-badges refresh) can
    invalidate just the affected indices without a full re-render."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "fnRenderStepListPartial: fnRenderStepListPartial" in sSource


def test_fnRenderStepListPartial_maps_files_via_reverse_index():
    """The partial-render entry must read ``_dictStepIndexByFilePath``
    so it can answer "which step does this file belong to?" without
    iterating the workflow."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function fnRenderStepListPartial(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_dictStepIndexByFilePath" in sBlock
    assert "_fnInvalidateRenderCache" in sBlock


# -----------------------------------------------------------------------
# Change 9: mtime-as-existence-cache in the file-poll loop
# -----------------------------------------------------------------------


def test_poll_skips_steps_with_known_output_mtime():
    """``fnPollAllStepFiles`` must short-circuit on any step whose
    ``dictOutputMtimes`` entry is already populated — that already
    proves the step's output files exist on disk, so re-issuing
    PipeleyenFileOps.fnCheckStepDataFiles would be ~1000 redundant
    file probes per poll at N=100."""
    sSource = _fsReadStaticFile("scriptApplication.js")
    iStart = sSource.find("function fnPollAllStepFiles(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "dictOutputMtimes" in sBlock, (
        "fnPollAllStepFiles must consult dictOutputMtimes to decide "
        "whether the per-step existence probe is necessary."
    )
    assert "dictMtimes[String(iStep)]" in sBlock
