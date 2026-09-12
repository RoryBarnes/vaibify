"""Did a rebuild change the environment a project's results rest on?

A researcher who rebuilds — after upgrading vaibify, after editing
``vaibify.yml``, or simply a month later — can get a different image
than the one their published numbers were produced in. Nothing warns
them today: the dashboard notices eventually, at verify time, which is
hours or weeks after the moment they caused it.

**The interesting part is that the cause is diagnosable.** The builder
already stamps a *recipe fingerprint* onto every image it makes
(``dockerfileComposer.S_RECIPE_IMAGE_LABEL``), a hash over the build
inputs vaibify controls. Comparing the fingerprint alongside the digest
separates two very different events:

- **The recipe changed.** You upgraded vaibify or edited the config.
  The environment moved because you moved it.
- **The recipe did NOT change and the image did anyway.** Nothing under
  vaibify's control moved, so the difference came from outside it —
  Ubuntu rotated a package out of the archive pool and the rebuild
  resolved a different one. This is the case a researcher has no other
  way to see, and the one most likely to be mistaken for a bug in their
  own science.

This module is PURE: it compares values and composes sentences. Reading
a digest off the daemon belongs to ``environmentSnapshot``, and the
three-state verdict convention is that module's — True, False, and
``None`` for *nothing determined*, which no surface may paint as drift.
"""

__all__ = [
    "fdictCompareRebuiltEnvironment",
    "flistDescribeEnvironmentDrift",
    "S_DRIFT_UNCHANGED",
    "S_DRIFT_OUTSIDE_THE_RECIPE",
    "S_DRIFT_RECIPE_CHANGED",
    "S_DRIFT_UNDETERMINED",
]


S_DRIFT_UNCHANGED = "unchanged"
S_DRIFT_OUTSIDE_THE_RECIPE = "outside-the-recipe"
S_DRIFT_RECIPE_CHANGED = "recipe-changed"
S_DRIFT_UNDETERMINED = "undetermined"


def fdictCompareRebuiltEnvironment(
    sPinnedDigest, sPinnedRecipe, sBuiltDigest, sBuiltRecipe,
):
    """Compare a project's pinned environment against a fresh build.

    ``bEnvironmentChanged`` is THREE-state and must stay that way.
    False is the same image; True is a genuinely different one; ``None``
    means nothing was determined — no pin, no recorded identity, or an
    unreachable daemon — and no surface may warn from it.

    ``sCause`` refines a True answer. It is
    ``S_DRIFT_OUTSIDE_THE_RECIPE`` only when both fingerprints are
    KNOWN and EQUAL: an absent fingerprint on either side is an image
    that predates the label, which is not evidence that the recipe
    held.
    """
    dictAnswer = {
        "sPinnedDigest": sPinnedDigest or "",
        "sBuiltDigest": sBuiltDigest or "",
        "sPinnedRecipe": sPinnedRecipe or "",
        "sBuiltRecipe": sBuiltRecipe or "",
        "bEnvironmentChanged": None,
        "sCause": S_DRIFT_UNDETERMINED,
    }
    if not sPinnedDigest or not sBuiltDigest:
        return dictAnswer
    if sPinnedDigest == sBuiltDigest:
        dictAnswer["bEnvironmentChanged"] = False
        dictAnswer["sCause"] = S_DRIFT_UNCHANGED
        return dictAnswer
    dictAnswer["bEnvironmentChanged"] = True
    if sPinnedRecipe and sBuiltRecipe:
        dictAnswer["sCause"] = (
            S_DRIFT_OUTSIDE_THE_RECIPE
            if sPinnedRecipe == sBuiltRecipe
            else S_DRIFT_RECIPE_CHANGED
        )
    return dictAnswer


def _flistDescribeTheCause(sCause):
    """Return the explanation lines for a changed environment."""
    if sCause == S_DRIFT_OUTSIDE_THE_RECIPE:
        return [
            "The build recipe did NOT change, so this difference came "
            "from outside vaibify: the Linux distribution rotated a "
            "package out of its archive and the rebuild resolved a "
            "different one.",
        ]
    if sCause == S_DRIFT_RECIPE_CHANGED:
        return [
            "The build recipe changed too — a vaibify upgrade or an "
            "edit to vaibify.yml. The environment moved because you "
            "moved it.",
        ]
    return [
        "Whether the build recipe also changed could not be "
        "determined; one of the images predates recipe fingerprinting.",
    ]


def flistDescribeEnvironmentDrift(dictComparison):
    """Return researcher-facing lines, or [] when there is nothing to say.

    Empty for an unchanged environment AND for an undetermined one. A
    warning composed from ``None`` would be a claim about an image
    nobody compared.
    """
    if not dictComparison.get("bEnvironmentChanged"):
        return []
    listLines = [
        "The environment changed. The image you just built is not the "
        "one this project's recorded results were produced in.",
        "",
        f"  recorded: {dictComparison.get('sPinnedDigest') or '(none)'}",
        f"  built now: {dictComparison.get('sBuiltDigest') or '(none)'}",
        "",
    ]
    listLines.extend(_flistDescribeTheCause(dictComparison.get("sCause")))
    listLines.extend([
        "",
        "Results already committed are unaffected — they are pinned to "
        "the recorded image by digest, and reproducing published work "
        "pulls that image rather than rebuilding.",
        "New results produced from here on were computed in a different "
        "environment than the old ones. Re-run and re-verify before "
        "comparing them, or the manifest will report the difference as "
        "a divergence.",
    ])
    return listLines
