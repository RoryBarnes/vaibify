"""Tests for the step name <-> directory slug contract (2026-07-18).

The contract: a step's directory basename IS a function of its name —
split on whitespace, uppercase each word's first letter, preserve the
rest, concatenate; hyphens pass through. Names allow only letters,
digits, spaces, and hyphens. Parent path components are free. Slugs
are unique per project, case-insensitively (macOS clones sit on
case-insensitive filesystems).

Adversarial per the repo's epistemics rules: the formula is pinned
with acronym, digit, and hyphen vectors that would each break a naive
implementation; the route guards are asserted with the input that
would corrupt state if the guard were missing.
"""

import os

import pytest

from vaibify.gui.pipelineUtils import (
    fbStepDirectoryConforms,
    fnRequireUniqueStepSlug,
    fsSlugFromStepName,
    fsValidateStepName,
)
from vaibify.gui.pipelineServer import fsDeriveStepDirectory


# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


@pytest.mark.parametrize("sName,sExpected", [
    ("Step Name", "StepName"),
    ("step name", "StepName"),
    ("GJ 1132 XUV", "GJ1132XUV"),          # interior case preserved
    ("TESS Flare Candidates", "TESSFlareCandidates"),
    ("TOI-540 XUV", "TOI-540XUV"),         # hyphens pass through
    ("L 98-59 XUV", "L98-59XUV"),
    ("1132 evolution", "1132Evolution"),   # digit-leading word
    ("  padded   name  ", "PaddedName"),   # whitespace collapsed
    ("single", "Single"),
])
def test_slug_formula_vectors(sName, sExpected):
    assert fsSlugFromStepName(sName) == sExpected


@pytest.mark.parametrize("sBadName", [
    "", "   ", "a/b", "a\\b", "..", ".hidden", "x" * 101,
    "a\x00b", "Barnard's Star", "why?", "a_b", "dot.name", "---",
])
def test_name_alphabet_rejects_special_characters(sBadName):
    with pytest.raises(ValueError):
        fsValidateStepName(sBadName)


@pytest.mark.parametrize("sGoodName", [
    "TOI-540 XUV", "GJ 1132", "Mid-Late Joint Refit", "a", "A 1",
])
def test_name_alphabet_accepts_letters_digits_spaces_hyphens(sGoodName):
    assert fsValidateStepName(sGoodName) == sGoodName


# --------------------------------------------------------------------------
# Conformance predicate
# --------------------------------------------------------------------------


def test_conformance_governs_only_the_final_component():
    assert fbStepDirectoryConforms(
        {"sName": "GJ 1132 XUV", "sDirectory": "systems/GJ1132XUV"},
    ) is True
    assert fbStepDirectoryConforms(
        {"sName": "GJ 1132 XUV", "sDirectory": "systems/GJ1132"},
    ) is False


def test_conformance_exempts_empty_and_templated_directories():
    assert fbStepDirectoryConforms(
        {"sName": "AI Declaration", "sDirectory": ""},
    ) is True
    assert fbStepDirectoryConforms(
        {"sName": "Plots", "sDirectory": "{sPlotDirectory}/x"},
    ) is True


# --------------------------------------------------------------------------
# Slug uniqueness (case-insensitive)
# --------------------------------------------------------------------------


def test_unique_slug_rejects_a_case_variant():
    dictWorkflow = {"listSteps": [
        {"sName": "New Step"}, {"sName": "Other"},
    ]}
    with pytest.raises(ValueError):
        fnRequireUniqueStepSlug(dictWorkflow, -1, "NEW STEP")


def test_unique_slug_ignores_the_step_being_renamed():
    dictWorkflow = {"listSteps": [{"sName": "New Step"}]}
    fnRequireUniqueStepSlug(dictWorkflow, 0, "New Step")


# --------------------------------------------------------------------------
# Directory derivation at creation
# --------------------------------------------------------------------------


def test_derive_uses_the_slug_and_keeps_a_provided_parent():
    assert fsDeriveStepDirectory("GJ 1132 XUV", "") == "GJ1132XUV"
    assert fsDeriveStepDirectory(
        "GJ 1132 XUV", "systems/anything",
    ) == "systems/GJ1132XUV"


def test_derive_ignores_a_nonconforming_provided_basename():
    """The formula, not the typist, is the law: a hand-typed basename
    is auto-corrected to the slug."""
    assert fsDeriveStepDirectory(
        "Step Name", "wrongBasename",
    ) == "StepName"


def test_derive_passes_templated_directories_through():
    assert fsDeriveStepDirectory(
        "Plots", "{sPlotDirectory}/x",
    ) == "{sPlotDirectory}/x"


def test_derive_rejects_a_forbidden_name():
    with pytest.raises(ValueError):
        fsDeriveStepDirectory("Barnard's Star", "")


# --------------------------------------------------------------------------
# Frontend mirror: the JS formula must track the Python one. The
# string pins below assert the load-bearing pieces of the mirror; the
# backend remains the enforcement authority.
# --------------------------------------------------------------------------


_S_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStatic(sName):
    with open(os.path.join(_S_STATIC_DIR, sName),
              encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_javascript_mirror_exists_and_is_exported():
    sUtilities = _fsReadStatic("scriptUtilities.js")
    assert "function fsSlugFromStepName" in sUtilities
    assert "function fbStepDirectoryConforms" in sUtilities
    assert "fsSlugFromStepName: fsSlugFromStepName" in sUtilities
    iAt = sUtilities.find("function fsSlugFromStepName")
    sBlock = sUtilities[iAt:iAt + 400]
    assert "toUpperCase" in sBlock and "slice(1)" in sBlock, (
        "the mirror must uppercase word starts and preserve the rest"
    )


def test_nonconforming_step_paints_a_red_warning():
    sApplication = _fsReadStatic("scriptApplication.js")
    iReasons = sApplication.find(
        "function _flistStepWarningReasons")
    sReasons = sApplication[iReasons:sApplication.find(
        "\n    function ", iReasons + 1)]
    assert "fbStepDirectoryConforms" in sReasons, (
        "the mismatch must appear in the ⚠ reasons"
    )
    iRed = sApplication.find("function _fbStepWarningIsRed")
    sRed = sApplication[iRed:sApplication.find(
        "\n    function ", iRed + 1)]
    assert "fbStepDirectoryConforms" in sRed, (
        "a broken contract is an ERROR — red, not orange"
    )


def test_align_button_and_action_are_wired():
    sApplication = _fsReadStatic("scriptApplication.js")
    assert "wf-align-directories" in sApplication
    assert "align-directories" in sApplication, (
        "the Align button must call the align route"
    )
    sBindings = _fsReadStatic("scriptEventBindings.js")
    iAlign = sBindings.find('".wf-align-directories"')
    iHeader = sBindings.find('".steps-block-header"')
    assert -1 < iAlign < iHeader, (
        "the Align entry must precede the Steps-banner toggle or "
        "first-match dispatch collapses the block instead"
    )
    assert "fnShowConfirmModal" in sBindings, (
        "bulk alignment is confirmed before it runs"
    )


def test_edit_modal_name_field_is_read_only_in_edit_mode():
    sEditor = _fsReadStatic("scriptStepEditor.js")
    iEdit = sEditor.find("function fnOpenEditModal")
    sEdit = sEditor[iEdit:sEditor.find("\n    function ", iEdit + 1)]
    assert "readOnly = true" in sEdit, (
        "the generic edit path must not offer a divergent rename"
    )
    iClear = sEditor.find("function fnClearForm")
    sClear = sEditor[iClear:sEditor.find(
        "\n    function ", iClear + 1)]
    assert "readOnly = false" in sClear, (
        "create mode must re-enable the name field"
    )
