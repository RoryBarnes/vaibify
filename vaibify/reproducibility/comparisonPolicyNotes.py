"""Vaibify's notes beside a frozen comparison policy's own reasons.

A comparison policy is frozen at its pinned bytes: a project grades
under the policy it recorded, so a sentence in it can never be edited
in place, even when it overstates the options. Corrections therefore
live here, outside the frozen file, keyed by the policy that wrote a
reason and the reason itself, and travel beside the record rather than
inside it. The reason a researcher sees stays the policy's own; the
note says what vaibify knows about it now.

Policy v1's remedy for a changed vector figure suggests a raster
companion or a quantitative standard over the plotted data, but
``ftGradeChangedBytes`` returns "insufficient evidence" for ANY changed
vector output before it considers comparisons, so neither changes the
verdict on that file.
"""

from vaibify.reproducibility import comparisonPolicyV1


__all__ = [
    "S_VECTOR_POLICY_V1_NOTE",
    "fnAttachPolicyNotes",
]

S_VECTOR_POLICY_V1_NOTE = (
    "Under comparison policy v1 a changed vector figure cannot pass, "
    "whatever comparisons exist: it reaches Level 3 only if its bytes "
    "match. Answering \"Repeatable across computers\" makes matching "
    "bytes more likely, not certain. A raster companion or a "
    "quantitative standard does not change the verdict on this file; "
    "publishing the figure as a raster puts it under vaibify's image "
    "check.")

_DICT_NOTES_BY_POLICY_AND_REASON = {
    (comparisonPolicyV1.S_COMPARISON_POLICY,
     comparisonPolicyV1.S_VECTOR_REMEDY): S_VECTOR_POLICY_V1_NOTE,
}


def fnAttachPolicyNotes(dictRecord):
    """Add ``sVaibifyNote`` to each outcome whose policy reason has one."""
    if not isinstance(dictRecord, dict):
        return
    sPolicy = dictRecord.get("sComparisonPolicy")
    for dictOutcome in dictRecord.get("listFileOutcomes") or []:
        if not isinstance(dictOutcome, dict):
            continue
        sNote = _DICT_NOTES_BY_POLICY_AND_REASON.get(
            (sPolicy, dictOutcome.get("sOutcomeReason")))
        if sNote:
            dictOutcome["sVaibifyNote"] = sNote
