"""A step vaibify itself builds must satisfy vaibify's own contracts.

A researcher opened a project whose AI Declaration step wore a red ⚠
reading "Step name and directory disagree — the directory should be
'AIDeclaration'". Nothing they had done caused it. The step was built
by vaibify's own ``fdictBuildAiDeclarationStep``, whose default name
was "AI Declaration" and whose default directory was the
independently-typed constant ``"aiDeclaration"`` — and the slug
contract (2026-07-18) derives ``"AIDeclaration"`` from that name. The
two constants were free to disagree and did, so the product shipped a
step that violated the product's own rule and then told the researcher
to fix it.

The instance fix is the spelling. The CLASS fix is that a name and a
directory were validated independently at creation: the generic
update-step path 400s a rename precisely so directory, marker, and
manifest cannot drift from the name, but the add-step route had no
such guard. So this file pins two things:

1. Every step the builder produces conforms, for the default name and
   for an override — pinned through ``fbStepDirectoryConforms``, the
   single authority, so a future change to the slug formula moves this
   test with it rather than stranding a hard-coded string.
2. The add-step route refuses a name/directory pair that disagrees,
   and DERIVES the directory when the caller omits it.

Kills (confirmed): restoring ``S_DEFAULT_DECLARATION_DIRECTORY`` to
"aiDeclaration" fails the two builder tests; deleting the
``_fnRejectDirectoryDisagreeingWithName`` call fails the refusal test;
reverting the derivation to the constant fails the override test.
"""

import pytest

from vaibify.gui.pipelineUtils import (
    fbStepDirectoryConforms,
    fsSlugFromStepName,
)
from vaibify.gui.routes.levelRoutes import (
    _fdictBuildStepFromAddRequest,
    AiDeclarationAddStepRequest,
)
from vaibify.reproducibility.aiDeclarationStep import (
    S_DEFAULT_DECLARATION_DIRECTORY,
    S_DEFAULT_DECLARATION_STEP_NAME,
    fdictBuildAiDeclarationStep,
)

from fastapi import HTTPException


def test_the_shipped_default_step_conforms_to_the_slug_contract():
    """The bug, in the shape the researcher hit it."""
    dictStep = fdictBuildAiDeclarationStep()
    assert fbStepDirectoryConforms(dictStep), (
        f"the declaration step vaibify builds by default has "
        f"sName={dictStep['sName']!r} and "
        f"sDirectory={dictStep['sDirectory']!r}, which the slug "
        f"contract rejects — it derives "
        f"{fsSlugFromStepName(dictStep['sName'])!r}"
    )


def test_the_two_defaults_are_not_free_to_disagree():
    """Pin the RELATIONSHIP, not the spelling.

    Asserting the literal "AIDeclaration" would go stale silently if
    the slug formula ever changed; asserting the derivation cannot.
    """
    assert S_DEFAULT_DECLARATION_DIRECTORY == fsSlugFromStepName(
        S_DEFAULT_DECLARATION_STEP_NAME,
    )


def test_an_omitted_directory_is_derived_from_a_custom_name():
    """Deriving, rather than defaulting to a constant.

    A caller overriding only the name previously got the default
    directory, which disagrees with their name — the same defect one
    step removed from the shipped one.
    """
    dictStep = _fdictBuildStepFromAddRequest(
        {"listSteps": []},
        AiDeclarationAddStepRequest(sName="Model Disclosure"),
    )
    assert dictStep["sDirectory"] == "ModelDisclosure"
    assert fbStepDirectoryConforms(dictStep)


def test_a_directory_disagreeing_with_the_name_is_refused():
    """Creation gains the guard the rename path already had."""
    with pytest.raises(HTTPException) as excInfo:
        _fdictBuildStepFromAddRequest(
            {"listSteps": []},
            AiDeclarationAddStepRequest(
                sName="AI Declaration", sDirectory="somewhereElse",
            ),
        )
    assert excInfo.value.status_code == 400
    assert "AIDeclaration" in excInfo.value.detail, (
        f"the refusal must name the directory the contract derives: "
        f"{excInfo.value.detail}"
    )


def test_a_conforming_explicit_directory_is_still_accepted():
    """The guard must refuse disagreement, not every explicit value."""
    dictStep = _fdictBuildStepFromAddRequest(
        {"listSteps": []},
        AiDeclarationAddStepRequest(
            sName="AI Declaration", sDirectory="docs/AIDeclaration",
        ),
    )
    assert dictStep["sDirectory"] == "docs/AIDeclaration"
