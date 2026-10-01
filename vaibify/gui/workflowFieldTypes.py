"""Typed checks for the project fields the dashboard writes into HTML.

``project.json`` and ``state.json`` are writable from inside the
container, and the dashboard writes a handful of their fields into HTML
attributes and form values: the verification state into a CSS class, the
core count, tolerance and runtime limits into ``value=`` attributes. A
string where a number belongs is therefore markup the page would
interpolate. The loader refuses a wrong type by name; the renderer
escapes again.
"""

import re

__all__ = [
    "REGEX_VERIFICATION_STATE_TOKEN",
    "fsDescribeUntypedField",
]

# The fields checked, and the kind of value each must hold.
T_WHOLE_NUMBER_WORKFLOW_FIELDS = ("iNumberOfCores",)
T_POSITIVE_NUMBER_WORKFLOW_FIELDS = ("fTolerance",)
T_NON_NEGATIVE_NUMBER_WORKFLOW_FIELDS = ("fDefaultWallClockBudgetSeconds",)
T_NON_NEGATIVE_NUMBER_STEP_FIELDS = ("fWallClockBudgetSeconds",)
T_VERIFICATION_STATE_FIELDS = (
    "sUser", "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
)
REGEX_VERIFICATION_STATE_TOKEN = re.compile(r"^[a-z][a-z-]{0,31}$")


def fsDescribeUntypedField(dictWorkflow):
    """Name the first numeric or state field holding the wrong kind of value.

    Empty when every such field is absent or well formed. Run at load
    on project.json and again once state.json has been merged in, since
    both are writable from inside the container.
    """
    for sField in T_WHOLE_NUMBER_WORKFLOW_FIELDS:
        if not _fbIsWholeNumberOrAbsent(dictWorkflow.get(sField)):
            return f"'{sField}' must be a whole number"
    for sField in T_POSITIVE_NUMBER_WORKFLOW_FIELDS:
        if not _fbIsNumberAtLeastOrAbsent(dictWorkflow.get(sField), 0, False):
            return f"'{sField}' must be a number greater than zero"
    for sField in T_NON_NEGATIVE_NUMBER_WORKFLOW_FIELDS:
        if not _fbIsNumberAtLeastOrAbsent(dictWorkflow.get(sField), 0, True):
            return f"'{sField}' must be a number, zero or more"
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sProblem = _fsDescribeUntypedStepField(
            f"Step{iIndex + 1:02d}", dictStep,
        )
        if sProblem:
            return sProblem
    return ""


def _fsDescribeUntypedStepField(sLabel, dictStep):
    """Name a step field of the wrong kind, or return an empty string."""
    for sField in T_NON_NEGATIVE_NUMBER_STEP_FIELDS:
        if not _fbIsNumberAtLeastOrAbsent(dictStep.get(sField), 0, True):
            return f"{sLabel} '{sField}' must be a number, zero or more"
    dictVerification = dictStep.get("dictVerification") or {}
    if not isinstance(dictVerification, dict):
        return f"{sLabel} 'dictVerification' must be an object"
    for sField in T_VERIFICATION_STATE_FIELDS:
        sState = dictVerification.get(sField)
        if sState is not None and not (
            isinstance(sState, str)
            and REGEX_VERIFICATION_STATE_TOKEN.match(sState)
        ):
            return (
                f"{sLabel} 'dictVerification.{sField}' must be a "
                "lowercase state word such as 'passed'"
            )
    return ""


def _fbIsWholeNumberOrAbsent(genericValue):
    """True for None or an integer that is not a boolean."""
    return genericValue is None or (
        isinstance(genericValue, int) and not isinstance(genericValue, bool)
    )


def _fbIsNumberAtLeastOrAbsent(genericValue, fMinimum, bInclusive):
    """True for None or a finite real number above (or at) a minimum."""
    if genericValue is None:
        return True
    if isinstance(genericValue, bool):
        return False
    if not isinstance(genericValue, (int, float)):
        return False
    if genericValue != genericValue or abs(genericValue) == float("inf"):
        return False
    return (
        genericValue >= fMinimum if bInclusive else genericValue > fMinimum
    )
