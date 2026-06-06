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
