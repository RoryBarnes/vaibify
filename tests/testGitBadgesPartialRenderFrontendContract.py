"""Frontend contract checks for the git-badges partial-render path.

Asserts the perf-plan shape introduced by the badge half of change 8:
the badge-diff helper returns a dict carrying ``listAffectedFiles``,
``fnRefresh`` prefers the partial re-render entry point when it is
defined, and a full-render fallback survives for callers (and load
orderings) that pre-date the partial path. JavaScript is not executed
by the repository test suite; these are string-presence + structural
assertions in the established frontend-contract pattern.

Background
----------
Before this change, every 5-second polling tick that observed any
badge state change triggered ``PipeleyenApp.fnRenderStepList()`` — a
full rebuild of every step card. On large workflows this dominated
steady-state cost. The badge-cache module now publishes which files'
badges actually changed, so the application layer can re-render just
those rows. Until the application layer's partial entry point lands,
the badge module must still fall back to the full re-render so the
two halves can merge cleanly across worktrees.
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
# _fbBadgeMapChanged return-shape contract
# -----------------------------------------------------------------------


def test_badge_map_changed_returns_list_of_affected_files():
    """The diff helper must publish ``listAffectedFiles`` so callers
    can scope a partial re-render to just the changed step rows."""
    sSource = _fsReadStaticFile("scriptGitBadges.js")
    assert "listAffectedFiles" in sSource, (
        "_fbBadgeMapChanged must expose listAffectedFiles so the "
        "application layer can render only the affected step cards"
    )


def test_badge_map_changed_preserves_boolean_meaning():
    """Existing callers read the change flag via ``.bChanged``; the
    diff helper must populate it so the gate behavior is unchanged."""
    sSource = _fsReadStaticFile("scriptGitBadges.js")
    assert "bChanged:" in sSource or "bChanged :" in sSource, (
        "_fbBadgeMapChanged must populate bChanged on its return dict "
        "so the existing gating call site continues to short-circuit"
    )
    assert "dictDiff.bChanged" in sSource, (
        "fnRefresh must read the new return dict via .bChanged "
        "instead of treating the helper as a bare boolean"
    )


# -----------------------------------------------------------------------
# fnRefresh partial-render guard + fallback contract
# -----------------------------------------------------------------------


def test_refresh_prefers_partial_render_when_available():
    """fnRefresh must call PipeleyenApp.fnRenderStepListPartial when
    that entry point exists, passing the list of affected files."""
    sSource = _fsReadStaticFile("scriptGitBadges.js")
    assert (
        'typeof PipeleyenApp.fnRenderStepListPartial === "function"'
        in sSource
    ), (
        "Badge module must guard the partial-render call with a "
        "typeof check so it stays safe before the application layer "
        "exposes fnRenderStepListPartial"
    )
    assert "PipeleyenApp.fnRenderStepListPartial(" in sSource, (
        "Badge module must invoke fnRenderStepListPartial with the "
        "affected-files list when the entry point is defined"
    )


def test_refresh_falls_back_to_full_render_for_legacy_callers():
    """When the partial entry point is missing (older application
    layer, test stubs), fnRefresh must still call the existing full
    re-render so the dashboard stays in sync."""
    sSource = _fsReadStaticFile("scriptGitBadges.js")
    assert "PipeleyenApp.fnRenderStepList()" in sSource, (
        "Badge module must retain the full-render fallback path so "
        "the badge-side change is safe to land before the application "
        "layer's partial entry point exists"
    )
    assert (
        'typeof PipeleyenApp.fnRenderStepList !== "function"'
        in sSource
    ), (
        "Badge module must also guard the legacy full-render call so "
        "it tolerates missing PipeleyenApp.fnRenderStepList"
    )


def test_rerender_dispatcher_consumes_affected_files_argument():
    """The dispatcher between fnRefresh and PipeleyenApp must accept
    the listAffectedFiles argument so the partial path can scope its
    work; this prevents a silent regression to the full re-render."""
    sSource = _fsReadStaticFile("scriptGitBadges.js")
    assert (
        "function _fnRequestStepListRerender(listAffectedFiles)"
        in sSource
    ), (
        "_fnRequestStepListRerender must accept listAffectedFiles so "
        "the partial-render path receives the diff result"
    )
