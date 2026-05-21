"""Per-step L1 predicates — pure functions over ``dictStep``.

These three predicates read only ``dictStep["dictVerification"]``
fields and have no dependency on GUI state, so they live in the
reproducibility package as a neutral leaf module. ``levelGates``
composes them into ``fbStepIsAtLeastLevel1``; the GUI re-exports
them from ``fileStatusManager`` for backward compatibility with
external callers and tests.

Module is intentionally leaf-free — no intra-package imports — so
it can be referenced from anywhere in the package graph without
introducing a cycle.
"""


_T_TEST_VERIF_KEYS = (
    "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
)


_T_GREEN_VERIF_VALUES = ("passed", "passed-from-marker", "unnecessary")


__all__ = [
    "fbStepTestsPassing",
    "fbStepTimingClean",
    "fbStepUserApproved",
]


def fbStepUserApproved(dictStep):
    """Return True iff the step's ``sUser`` verification is ``passed``.

    The researcher attestation is the only field that requires a
    human click; every other gate field is derived. A corrupt step
    (non-dict, missing ``dictVerification``) reads as not-approved
    so callers do not crash with ``AttributeError``.
    """
    if not isinstance(dictStep, dict):
        return False
    dictV = dictStep.get("dictVerification", {})
    if not isinstance(dictV, dict):
        return False
    return dictV.get("sUser") == "passed"


def fbStepTimingClean(dictStep):
    """Return True iff the step has no upstream-modified flag or stale outputs.

    ``bUpstreamModified`` and a non-empty ``listModifiedFiles`` both
    signal that something changed since the step was last attested.
    Either one disqualifies the step from contributing to L1.
    """
    if not isinstance(dictStep, dict):
        return False
    dictV = dictStep.get("dictVerification", {})
    if not isinstance(dictV, dict):
        return False
    if dictV.get("bUpstreamModified") is True:
        return False
    if dictV.get("listModifiedFiles"):
        return False
    return True


def fbStepTestsPassing(dictStep):
    """Return True iff every defined test category on a step is green.

    "Defined" means the verification field is present. Three values
    count as green: ``passed`` (tests ran cleanly through the
    dashboard), ``passed-from-marker`` (sUnitTest aggregate value
    bootstrapped from a marker file on disk, indicating a prior
    successful run), and ``unnecessary`` (derivation-hook value for
    categories whose ``saCommands`` list is empty). Any other
    present value blocks the gate.
    """
    if not isinstance(dictStep, dict):
        return False
    dictV = dictStep.get("dictVerification", {})
    if not isinstance(dictV, dict):
        return False
    for sKey in _T_TEST_VERIF_KEYS:
        if sKey in dictV and dictV[sKey] not in _T_GREEN_VERIF_VALUES:
            return False
    return True
