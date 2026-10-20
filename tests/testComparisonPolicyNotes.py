"""Vaibify's note beside policy v1's vector-figure remedy reaches the PROOF tab.

The frozen policy grades ANY changed vector figure "insufficient
evidence" before it considers comparisons, yet its remedy suggests a
raster companion or a quantitative standard, neither of which changes
that verdict. The policy cannot be edited, so the attestation response
carries a vaibify-side note beside the policy's own reason. The reasons
here come from grading through the REAL frozen policy, never from a
copied string.
"""

import pytest

from vaibify.gui.routes import reproducibilityRoutes
from vaibify.reproducibility import comparisonPolicyNotes
from vaibify.reproducibility import comparisonPolicyV1 as policyV1


def fdictOutcomeGradedByPolicyV1(sPath, sKind, listAgreement):
    """Grade one output whose bytes changed, through the frozen policy."""
    sOutcome, sReason = policyV1.ftGradeChangedBytes(
        {"sKind": sKind, "dictImageCheck": None}, listAgreement)
    return {"sPath": sPath, "sRole": "output", "sStatus": "diverged",
            "sOutcome": sOutcome, "sOutcomeReason": sReason,
            "sExpected": "a" * 64, "sObserved": "b" * 64}


def flistGradedOutcomes():
    """A vector figure with a passing comparison, and an uncovered data file."""
    return [
        fdictOutcomeGradedByPolicyV1(
            "Plot/figure.pdf", policyV1.S_KIND_VECTOR, [{"bPassed": True}]),
        fdictOutcomeGradedByPolicyV1(
            "Step/table.bin", policyV1.S_KIND_DATA, []),
    ]


def testAChangedVectorFigureIsInsufficientWhateverItsComparisons():
    """The fact the note rests on, read from the frozen policy itself."""
    dictVector, dictData = flistGradedOutcomes()
    assert dictVector["sOutcome"] == policyV1.S_OUTPUT_INSUFFICIENT
    assert dictData["sOutcome"] == policyV1.S_OUTPUT_INSUFFICIENT
    assert "no published comparison covers it" in dictData["sOutcomeReason"]


@pytest.mark.falsification
def testTheAttestationResponseCarriesTheNoteBesideTheVectorReason(
        monkeypatch, tmp_path):
    """Kills: dropping the note from the attestation response."""
    dictRecord = {"sComparisonPolicy": policyV1.S_COMPARISON_POLICY,
                  "listFileOutcomes": flistGradedOutcomes()}
    monkeypatch.setattr(reproducibilityRoutes, "fdictReadAttestation",
                        lambda filesRepo: dictRecord)
    monkeypatch.setattr(reproducibilityRoutes,
                        "flistReadAttestationHistory", lambda filesRepo: [])
    dictResponse = reproducibilityRoutes._fdictBuildAttestationResponse(
        "cid", str(tmp_path))
    dictVector, dictData = dictResponse["dictCurrentAttestation"][
        "listFileOutcomes"]
    assert dictVector["sVaibifyNote"] == (
        comparisonPolicyNotes.S_VECTOR_POLICY_V1_NOTE)
    assert dictVector["sOutcomeReason"] == policyV1.S_VECTOR_REMEDY
    assert "sVaibifyNote" not in dictData


def testANoteBelongsToThePolicyThatWroteTheReason():
    dictRecord = {"sComparisonPolicy": "comparison-v2",
                  "listFileOutcomes": flistGradedOutcomes()}
    comparisonPolicyNotes.fnAttachPolicyNotes(dictRecord)
    assert all("sVaibifyNote" not in dictOutcome
               for dictOutcome in dictRecord["listFileOutcomes"])


def testTheNoteStatesWhatTheFrozenPolicyCannotDo():
    sNote = comparisonPolicyNotes.S_VECTOR_POLICY_V1_NOTE
    assert "cannot pass" in sNote
    assert "only if its bytes match" in sNote
    assert "more likely, not certain" in sNote
    assert "raster" in sNote
