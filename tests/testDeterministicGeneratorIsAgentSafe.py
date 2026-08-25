"""The agent-safe generator's safety is structural, not documented.

``generate-tests`` is two things behind one path: deterministic
introspection of the researcher's declared outputs, and an LLM
round-trip taken when neither ``bDeterministic`` nor ``bUseApi`` is
set. It is marked user-only for the second, which left an inversion --
an agent hand-writing assertions through ``save-and-run-test`` was
permitted, while vaibify DERIVING the same assertions mechanically
from the researcher's own files was not. The unreviewed path was open
and the mechanical one closed.

``generate-tests-deterministic`` is the mechanical half alone. Three
properties make that true rather than merely intended, and each is
asserted here by trying to break it:

1. The LLM branch is unreachable because the flags are ABSENT from the
   request type, not defaulted on it. A body carrying ``bUseApi`` is
   ignored, because there is nothing for it to bind to.
2. An edited test file is never force-overwritten. The generator
   answers ``bNeedsOverwriteConfirm`` and the agent must bring that
   back to the researcher.
3. Asking for one category leaves the other two alone -- absent from
   the result, so the apply step cannot rewrite their declarations.

Property 3 is the one a confirmatory test would miss: generating all
three and finding three results says nothing about whether asking for
one rewrites the others.
"""

import pytest

from vaibify.gui.pipelineServer import TestGenerateCategoryRequest
from vaibify.gui.routes.testRoutes import (
    _DeterministicGenerateRequest,
    _fsetValidateGenerateCategories,
)
from vaibify.gui.testGenerator import T_GENERATED_TEST_CATEGORIES


def test_the_request_type_cannot_carry_the_llm_flags():
    """A body that sets bUseApi must not produce a request that has it.

    Pydantic ignores unknown fields by default, so the absence of the
    field IS the refusal. If somebody later adds ``bUseApi`` to this
    model with a False default, an agent regains the LLM lane and
    nothing else in the system would notice.
    """
    requestBody = TestGenerateCategoryRequest(
        **{"sCategory": "qualitative", "bUseApi": True,
           "sApiKey": "sk-should-not-bind",
           "bForceOverwrite": True},
    )
    dictDump = requestBody.model_dump()
    assert dictDump == {"sCategory": "qualitative"}, (
        "the deterministic generator's request type gained a field "
        f"beyond sCategory: {sorted(dictDump)}. Every flag that could "
        "select the LLM branch or force an overwrite must be absent, "
        "not defaulted"
    )


def test_the_forced_request_pins_every_dangerous_flag():
    """The flags the handler hands the generator are fixed, not parsed."""
    requestForced = _DeterministicGenerateRequest()
    assert requestForced.bUseApi is False
    assert requestForced.sApiKey is None
    assert requestForced.bDeterministic is True
    assert requestForced.bForceOverwrite is False, (
        "an agent must not silently replace a test file the researcher "
        "edited; the generator answers bNeedsOverwriteConfirm instead"
    )


def test_an_absent_category_means_all_three():
    assert _fsetValidateGenerateCategories("") == set(
        T_GENERATED_TEST_CATEGORIES)
    assert _fsetValidateGenerateCategories(None) == set(
        T_GENERATED_TEST_CATEGORIES)


@pytest.mark.parametrize("sCategory", T_GENERATED_TEST_CATEGORIES)
def test_each_category_can_be_requested_alone(sCategory):
    assert _fsetValidateGenerateCategories(sCategory) == {sCategory}


def test_an_unknown_category_is_refused_by_name():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excInfo:
        _fsetValidateGenerateCategories("qualatative")
    assert excInfo.value.status_code == 400
    assert "qualatative" in excInfo.value.detail, (
        "the refusal must echo what was asked for; a typo answered "
        "with a generic message sends the caller guessing"
    )


def _fdictGenerateWithCategories(setCategories, dictWritten):
    """Drive the generator's write stage with the category gate."""
    from vaibify.gui import testGenerator

    def ffnRecord(sKey):
        def fdictWrite(*args, **kwargs):
            dictWritten[sKey] = True
            return {"sFilePath": f"tests/test_{sKey}.py"}
        return fdictWrite

    listPatched = [
        ("_fdictWriteIntegrityTests", "integrity"),
        ("_fdictWriteQualitativeTests", "qualitative"),
        ("_fdictWriteQuantitativeTests", "quantitative"),
    ]
    dictOriginals = {}
    for sName, sKey in listPatched:
        dictOriginals[sName] = getattr(testGenerator, sName)
        setattr(testGenerator, sName, ffnRecord(sKey))
    setattr(testGenerator, "fnWriteConftestMarker", lambda *a, **k: None)
    try:
        return testGenerator._fdictWriteAllDeterministicTests(
            None, "cid", "/step", [], 1e-6, False, "/repo",
            "deterministic", setCategories,
        )
    finally:
        for sName, _sKey in listPatched:
            setattr(testGenerator, sName, dictOriginals[sName])


def test_requesting_one_category_leaves_the_others_untouched():
    """The falsifying case: does asking for one rewrite the rest?

    Absent from the result, not empty in it -- ``_fnApplyGeneratedTests``
    copies only the keys it finds, so an absent category keeps whatever
    declaration the step already had.
    """
    dictWritten = {}
    dictResult = _fdictGenerateWithCategories(
        {"qualitative"}, dictWritten)
    assert dictWritten == {"qualitative": True}, (
        "asking for the qualitative tier wrote another tier's files "
        f"too: {sorted(dictWritten)}"
    )
    assert set(dictResult) == {"dictQualitative"}, (
        "an unrequested category must be ABSENT from the result; "
        f"present-but-empty would blank its declaration: {sorted(dictResult)}"
    )


def test_requesting_nothing_specific_still_writes_all_three():
    dictWritten = {}
    dictResult = _fdictGenerateWithCategories(None, dictWritten)
    assert set(dictWritten) == set(T_GENERATED_TEST_CATEGORIES)
    assert set(dictResult) == {
        "dictIntegrity", "dictQualitative", "dictQuantitative"}
