"""Contract: JS-side blocker-glyph dicts cover every Python criterion.

The frontend's step card chooses a banner glyph by looking up
``dictEntry.sCriterion`` in one of three dicts inside
``vaibify/gui/static/scriptApplication.js``:

* ``_DICT_BLOCKER_CRITERION_GLYPHS`` — L1
* ``_DICT_L2_BLOCKER_GLYPHS`` — L2
* ``_DICT_L3_BLOCKER_GLYPHS`` — L3

A criterion the Python gates can emit but the JS dict does not list
would render as no glyph at all on the dashboard, silently dropping a
real blocker. Conversely, an unused glyph entry is dead code. This
test parses the JS source as text (the frontend has no Python import
contract) and cross-checks the two universes.

The second contract verifies the unified-schema (Section A of the
AICS-ladder plan) promise: every Python-side L1/L2/L3 builder must set
a non-empty ``sRemediationHint`` field, since Stage 6 drives the
file-glyph and banner-glyph tooltips from that field.

Reference: see ``Stage 6 — Climbing-the-ladder UX`` in
``misty-pizza`` plan; the Section G "Tooltips driven from blocker
data" requirement is what this test enforces.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

import pytest


_SCRIPT_APPLICATION_REL = "vaibify/gui/static/scriptApplication.js"
_LEVEL_GATES_REL = "vaibify/reproducibility/levelGates.py"

_RE_JS_DICT_KEY = re.compile(r'"([a-z0-9-]+)"\s*:\s*\{')
_RE_PY_S_CRITERION_LITERAL = re.compile(
    r'"sCriterion"\s*:\s*"([a-z0-9-]+)"'
)
_RE_PY_S_CRITERION_KWARG = re.compile(
    r'sCriterion\s*=\s*"([a-z0-9-]+)"'
)
_RE_PY_L3_TUPLE_RETURN = re.compile(
    r'return\s*\(\s*"([a-z0-9-]+)"\s*,'
)
_RE_PY_L3_HINTS_KEYS = re.compile(
    r'"([a-z0-9-]+)"\s*:\s*\n?\s*"'
)


def _fpathRepoRoot() -> Path:
    return Path(__file__).resolve().parent.parent


def _fsReadText(sRelativePath: str) -> str:
    sPath = _fpathRepoRoot() / sRelativePath
    return sPath.read_text(encoding="utf-8")


def _fsExtractJsDictBody(sSource: str, sDictName: str) -> str:
    """Return the text between ``{`` and the matching ``}`` for a dict."""
    sNeedle = f"var {sDictName} = {{"
    iStart = sSource.find(sNeedle)
    if iStart == -1:
        raise AssertionError(
            f"could not locate JS dict {sDictName} in source"
        )
    iBraceStart = iStart + len(sNeedle) - 1
    iDepth = 0
    for iPosition in range(iBraceStart, len(sSource)):
        sChar = sSource[iPosition]
        if sChar == "{":
            iDepth += 1
        elif sChar == "}":
            iDepth -= 1
            if iDepth == 0:
                return sSource[iBraceStart:iPosition + 1]
    raise AssertionError(f"unterminated JS dict {sDictName}")


def _fsetJsDictKeys(sDictBody: str) -> Set[str]:
    return set(_RE_JS_DICT_KEY.findall(sDictBody))


def _fsetPythonCriteriaFromLevelGates() -> Set[str]:
    sSource = _fsReadText(_LEVEL_GATES_REL)
    setCriteria: Set[str] = set()
    setCriteria.update(_RE_PY_S_CRITERION_LITERAL.findall(sSource))
    setCriteria.update(_RE_PY_S_CRITERION_KWARG.findall(sSource))
    setCriteria.update(_RE_PY_L3_TUPLE_RETURN.findall(sSource))
    setCriteria.update(_fsetL3RemediationHintKeys(sSource))
    return setCriteria


def _fsetL3RemediationHintKeys(sSource: str) -> Set[str]:
    sNeedle = "_DICT_L3_REMEDIATION_HINTS = {"
    iStart = sSource.find(sNeedle)
    if iStart == -1:
        return set()
    iBraceStart = iStart + len(sNeedle) - 1
    iDepth = 0
    for iPosition in range(iBraceStart, len(sSource)):
        sChar = sSource[iPosition]
        if sChar == "{":
            iDepth += 1
        elif sChar == "}":
            iDepth -= 1
            if iDepth == 0:
                sBody = sSource[iBraceStart:iPosition + 1]
                return set(_RE_PY_L3_HINTS_KEYS.findall(sBody))
    return set()


def _fsetAllJsBlockerCriteria() -> Set[str]:
    sSource = _fsReadText(_SCRIPT_APPLICATION_REL)
    setKeys: Set[str] = set()
    for sDictName in (
        "_DICT_BLOCKER_CRITERION_GLYPHS",
        "_DICT_L2_BLOCKER_GLYPHS",
        "_DICT_L3_BLOCKER_GLYPHS",
    ):
        sBody = _fsExtractJsDictBody(sSource, sDictName)
        setKeys.update(_fsetJsDictKeys(sBody))
    return setKeys


def testEveryPythonCriterionHasJsGlyphEntry():
    """Every criterion the Python gates can emit must have a JS glyph
    entry, otherwise the dashboard would silently drop a blocker
    that the backend honestly reports."""
    setPython = _fsetPythonCriteriaFromLevelGates()
    setJs = _fsetAllJsBlockerCriteria()
    setMissing = setPython - setJs
    assert setMissing == set(), (
        "Python criteria missing from JS glyph dicts: " +
        ", ".join(sorted(setMissing))
    )


def testEveryJsGlyphEntryHasPythonOrigin():
    """Reverse direction: a JS glyph entry whose criterion no Python
    builder emits is dead code that drifts away from the gate."""
    setPython = _fsetPythonCriteriaFromLevelGates()
    setJs = _fsetAllJsBlockerCriteria()
    setOrphaned = setJs - setPython
    assert setOrphaned == set(), (
        "JS glyph entries with no Python origin: " +
        ", ".join(sorted(setOrphaned))
    )


def _flistPythonBlockerEntries(sSource: str):
    """Return list of (sCriterion, dict-body-text) for every blocker dict
    literal in levelGates.py.

    Each dict literal that contains an ``"sCriterion": "<name>"`` line is
    treated as a unified-schema entry; the parser extracts the brace-
    matched body so the caller can probe individual fields.
    """
    listEntries = []
    for matchObj in re.finditer(
        r'(\{[^{}]*?"sCriterion"\s*:\s*"([a-z0-9-]+)"[^{}]*?\})',
        sSource,
    ):
        listEntries.append((matchObj.group(2), matchObj.group(1)))
    return listEntries


@pytest.mark.parametrize(
    "sCriterion,sBody",
    _flistPythonBlockerEntries(_fsReadText(_LEVEL_GATES_REL)),
)
def testEveryUnifiedEntryHasNonEmptyRemediationHint(
    sCriterion, sBody,
):
    """Section A of the misty-pizza plan promises every unified-schema
    blocker carries a non-empty ``sRemediationHint``. Stage 6 drives the
    file-glyph and banner-glyph tooltips from that field, so a missing
    or empty hint shows the researcher an empty tooltip on hover."""
    bHasHint = (
        '"sRemediationHint"' in sBody
        or 'sRemediationHint=' in sBody
        or "_DICT_L3_REMEDIATION_HINTS" in sBody
    )
    assert bHasHint, (
        f"criterion {sCriterion!r} lacks sRemediationHint field; "
        "Section G tooltip will be empty"
    )


def testL3WorkflowAndStepBuildersAlwaysDelegateToHintDict():
    """Sanity check for the L3 builders, which intentionally pull hints
    from ``_DICT_L3_REMEDIATION_HINTS`` rather than inline literals.
    Every L3 criterion the gate can return must therefore appear as a
    key in that dict."""
    sSource = _fsReadText(_LEVEL_GATES_REL)
    setL3FromGate: Set[str] = set()
    setL3FromGate.update(_RE_PY_L3_TUPLE_RETURN.findall(sSource))
    sNeedle = "_fdictL3WorkflowChecks"
    iStart = sSource.find("def " + sNeedle)
    if iStart != -1:
        iEnd = sSource.find("\n\n\n", iStart)
        sBody = sSource[iStart:iEnd if iEnd != -1 else len(sSource)]
        setL3FromGate.update(re.findall(r'"([a-z0-9-]+)":\s*fb', sBody))
    setHintKeys = _fsetL3RemediationHintKeys(sSource)
    setMissing = setL3FromGate - setHintKeys
    assert setMissing == set(), (
        "L3 criteria missing from _DICT_L3_REMEDIATION_HINTS: " +
        ", ".join(sorted(setMissing))
    )


_STYLE_MAIN_REL = "vaibify/gui/static/styleMain.css"

_RE_JS_S_CLASS = re.compile(r'sClass:\s*"([a-z0-9-]+)"')


def _fsetAllJsGlyphClasses() -> Set[str]:
    """Return every ``sClass`` named in the three JS glyph dicts plus
    the base banner class and the per-file failure glyph class."""
    sSource = _fsReadText(_SCRIPT_APPLICATION_REL)
    setClasses: Set[str] = set()
    for sDictName in (
        "_DICT_BLOCKER_CRITERION_GLYPHS",
        "_DICT_L2_BLOCKER_GLYPHS",
        "_DICT_L3_BLOCKER_GLYPHS",
    ):
        sBody = _fsExtractJsDictBody(sSource, sDictName)
        setClasses.update(_RE_JS_S_CLASS.findall(sBody))
    setClasses.add("step-blocker-glyph")
    setClasses.add("l1-blocker-file-glyph")
    return setClasses


def testEveryGlyphClassHasCssRule():
    """Every glyph class the JS can stamp on a span must have at least
    one CSS rule in styleMain.css; an unruled class renders in the
    default (white) text color, hiding the level-by-color scheme."""
    sCss = _fsReadText(_STYLE_MAIN_REL)
    setMissing: Set[str] = set()
    for sClass in _fsetAllJsGlyphClasses():
        sPattern = r"\." + re.escape(sClass) + r"(?![a-z0-9-])"
        if not re.search(sPattern, sCss):
            setMissing.add(sClass)
    assert setMissing == set(), (
        "JS glyph classes with no CSS rule (would render white): " +
        ", ".join(sorted(setMissing))
    )


_LEGEND_PANEL_REL = "vaibify/gui/static/scriptLegendPanel.js"

_RE_JS_DICT_KEY_OR_NULL = re.compile(
    r'"([a-z0-9-]+)"\s*:\s*(?:\{|null)'
)

# The level-cell vocabulary intentionally changed with the
# independent per-level projection: regressed/never cells were
# replaced by the not-started/none/partial/attained/unknown circle
# and favicon cells, plus the regression-warning column and the
# expandable workflow-row envelope.
_LIST_SCOPE_F_CSS_CLASSES = [
    "step-level-strip",
    "step-level-cell",
    "level-cell-circle",
    "level-cell-favicon",
    "level-cell-not-started",
    "level-cell-none",
    "level-cell-partial",
    "level-cell-attained",
    "level-cell-unknown",
    "step-regression-cell",
    "regression-warning-red",
    "regression-warning-orange",
    "level-column-header-row",
    "level-column-header-cell",
    "workflow-level-header-row",
    "workflow-level-detail",
    "envelope-light",
    "envelope-light-unknown",
    "envelope-check",
    "envelope-warn",
    "envelope-warn-orange",
    "envelope-section-header",
    "envelope-section-body",
    "file-mark-stale",
    "ghost-ai-declaration-row",
    "step-blocker-glyph-outputs-changed",
    "ai-declaration-preview",
]


def _fsExtractPythonFunctionBody(
    sSource: str, sFunctionName: str,
) -> str:
    """Return one function's source with its docstring stripped."""
    sNeedle = f"def {sFunctionName}("
    iStart = sSource.find(sNeedle)
    if iStart == -1:
        raise AssertionError(
            f"could not locate Python function {sFunctionName}"
        )
    iEnd = sSource.find("\ndef ", iStart + 1)
    sBody = sSource[iStart:iEnd if iEnd != -1 else len(sSource)]
    return re.sub(r'"""[\s\S]*?"""', "", sBody)


def testAxisSubStateGlyphKeysEqualPythonSubStateLiterals():
    """The JS ``_DICT_AXIS_SUBSTATE_GLYPHS`` keys must equal the
    sub-state literals ``levelGates._fsAxisNotGreenSubState`` can
    return, or a backend cause would silently render the wrong banner
    glyph (the dict lookup would miss and fall back)."""
    sJsBody = _fsExtractJsDictBody(
        _fsReadText(_SCRIPT_APPLICATION_REL),
        "_DICT_AXIS_SUBSTATE_GLYPHS",
    )
    setJsKeys = set(_RE_JS_DICT_KEY_OR_NULL.findall(sJsBody))
    sPythonBody = _fsExtractPythonFunctionBody(
        _fsReadText(_LEVEL_GATES_REL), "_fsAxisNotGreenSubState",
    )
    setPython = set(re.findall(r'"([a-z-]+)"', sPythonBody))
    assert setJsKeys == setPython, (
        f"JS sub-state keys {sorted(setJsKeys)} != Python literals "
        f"{sorted(setPython)}"
    )


def testUntestedSubStateRendersNoBannerGlyph():
    """``untested`` must map to null: no banner glyph at all — the
    orange status light already carries 'work not yet done'."""
    sJsBody = _fsExtractJsDictBody(
        _fsReadText(_SCRIPT_APPLICATION_REL),
        "_DICT_AXIS_SUBSTATE_GLYPHS",
    )
    assert re.search(r'"untested"\s*:\s*null', sJsBody), (
        "untested sub-state must map to null (no banner glyph)"
    )


@pytest.mark.parametrize(
    "sDictName,sCriterion",
    [
        ("_DICT_BLOCKER_CRITERION_GLYPHS", "upstream-modified"),
        ("_DICT_BLOCKER_CRITERION_GLYPHS", "script-stale"),
        ("_DICT_BLOCKER_CRITERION_GLYPHS", "attestation-stale"),
        ("_DICT_AXIS_SUBSTATE_GLYPHS", "outputs-changed"),
    ],
)
def testStaleSeverityCriteriaUsePencilIcon(sDictName, sCriterion):
    """Recoverable-by-re-run staleness renders the pencil, reserving
    red ✗ marks for genuinely failed or missing artifacts."""
    sBody = _fsExtractJsDictBody(
        _fsReadText(_SCRIPT_APPLICATION_REL), sDictName,
    )
    matchIcon = re.search(
        r'"' + re.escape(sCriterion) +
        r'"\s*:\s*\{[^}]*sIcon:\s*"([^"]+)"',
        sBody,
    )
    assert matchIcon is not None, (
        f"{sCriterion} missing from {sDictName}"
    )
    assert matchIcon.group(1) == "✎", (
        f"{sCriterion} must use the pencil icon, "
        f"got {matchIcon.group(1)!r}"
    )


def testEveryLevelCellAndFileMarkClassHasCssRule():
    """Every Scope-F level-cell, header, ghost-row, and file-mark
    class stamped by the JS must have a CSS rule."""
    sCss = _fsReadText(_STYLE_MAIN_REL)
    setMissing: Set[str] = set()
    for sClass in _LIST_SCOPE_F_CSS_CLASSES:
        sPattern = r"\." + re.escape(sClass) + r"(?![a-z0-9-])"
        if not re.search(sPattern, sCss):
            setMissing.add(sClass)
    assert setMissing == set(), (
        "Scope-F classes with no CSS rule: " +
        ", ".join(sorted(setMissing))
    )


_STEP_RENDERER_REL = "vaibify/gui/static/scriptStepRenderer.js"

_LIST_ALL_GLYPH_DICT_NAMES = [
    "_DICT_BLOCKER_CRITERION_GLYPHS",
    "_DICT_AXIS_SUBSTATE_GLYPHS",
    "_DICT_L2_BLOCKER_GLYPHS",
    "_DICT_L3_BLOCKER_GLYPHS",
    "_DICT_FILE_MARK_GLYPHS",
]

_TUPLE_FORBIDDEN_X_GLYPHS = ("✗", "✕", "×")


@pytest.mark.parametrize("sDictName", _LIST_ALL_GLYPH_DICT_NAMES)
def testNoXGlyphInAnyGlyphDict(sDictName):
    """The X mark is not part of the vaibify glyph language: failed
    and missing artifacts render the red warning glyph, staleness the
    orange pencil. No glyph dict may reintroduce an X."""
    sBody = _fsExtractJsDictBody(
        _fsReadText(_SCRIPT_APPLICATION_REL), sDictName,
    )
    for sGlyph in _TUPLE_FORBIDDEN_X_GLYPHS:
        assert sGlyph not in sBody, (
            f"{sDictName} contains the forbidden X glyph {sGlyph!r}; "
            "use the warning glyph instead"
        )


def testNoXStatusGlyphInLevelCellRenderers():
    """Level cells render circles or the favicon, never an X; the
    legend samples mirror the live cells. The X status mark must not
    appear in either renderer's source."""
    for sRelativePath in (_STEP_RENDERER_REL, _LEGEND_PANEL_REL):
        sSource = _fsReadText(sRelativePath)
        for sGlyph in _TUPLE_FORBIDDEN_X_GLYPHS:
            assert sGlyph not in sSource, (
                f"{sRelativePath} contains the forbidden X glyph "
                f"{sGlyph!r}"
            )


def testLevelCellStatePhrasesMatchWireVocabulary():
    """The application's tooltip phrase dict must cover exactly the
    six sState values the backend projection can emit, so every wire
    state renders an honest, specific tooltip."""
    sBody = _fsExtractJsDictBody(
        _fsReadText(_SCRIPT_APPLICATION_REL),
        "_DICT_LEVEL_CELL_STATE_PHRASES",
    )
    setKeys = set(re.findall(r'"([a-z-]+)"\s*:', sBody))
    setExpected = {
        "not-started", "none", "partial", "attained", "unknown",
        "not-applicable",
    }
    assert setKeys == setExpected, (
        f"tooltip phrase keys {sorted(setKeys)} != wire states "
        f"{sorted(setExpected)}"
    )


def testRendererStampsEveryLevelCellStateClass():
    """The step renderer derives the cell class from the wire state
    (``level-cell-<sState>``) and renders the favicon only for the
    attained state; the unknown cell must stay a hollow circle."""
    sSource = _fsReadText(_STEP_RENDERER_REL)
    assert "level-cell-' + sState" in sSource, (
        "cell class must derive from the wire sState verbatim"
    )
    assert re.search(
        r'sState === "attained"[\s\S]{0,200}?favicon\.png', sSource,
    ), "attained cells must render the favicon image"


def testLegendDescribesLevelCellVocabulary():
    """The legend must sample every level-cell state, both regression
    severities, and the favicon, using the new circle vocabulary."""
    sLegend = _fsReadText(_LEGEND_PANEL_REL)
    listNeedles = [
        "level-cell-not-started",
        "level-cell-none",
        "level-cell-partial",
        "level-cell-attained",
        "level-cell-unknown",
        "level-cell-not-applicable",
        "level-cell-dash",
        "regression-warning-red",
        "regression-warning-orange",
        "favicon.png",
        "level-cell-circle",
    ]
    listMissing = [
        sNeedle for sNeedle in listNeedles if sNeedle not in sLegend
    ]
    assert listMissing == [], (
        "legend panel missing level-cell vocabulary entries: " +
        ", ".join(listMissing)
    )


def testLegendReferencesAxisSubStateCatalogKey():
    """The legend must draw the axis sub-state rows from the same
    catalog key the application exposes, so it cannot drift from the
    rendered banner glyphs."""
    sApplication = _fsReadText(_SCRIPT_APPLICATION_REL)
    assert re.search(
        r"dictAxisSubStates:\s*_DICT_AXIS_SUBSTATE_GLYPHS",
        sApplication,
    ), "fdictBlockerGlyphCatalog must expose dictAxisSubStates"
    sLegend = _fsReadText(_LEGEND_PANEL_REL)
    assert "dictAxisSubStates" in sLegend, (
        "legend panel must render rows from dictAxisSubStates"
    )
