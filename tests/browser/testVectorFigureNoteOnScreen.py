"""A changed vector figure shows vaibify's note beside the policy's reason.

The outcomes are graded by the REAL frozen policy and annotated by the
real note table in Python, then rendered by the page's shared outcomes
table, the one every reproduction card uses. The vector row must keep
the policy's own reason and add the note; an uncovered data file must
show its reason ("no published comparison covers it"), not a bare word.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.testComparisonPolicyNotes import flistGradedOutcomes
from vaibify.reproducibility import comparisonPolicyNotes
from vaibify.reproducibility import comparisonPolicyV1 as policyV1


pytestmark = pytest.mark.browser


@pytest.mark.falsification
def testTheVectorRowCarriesTheNoteAndTheDataRowItsReason(
        pageDashboard, serverHub):
    """Kills: dropping the note from the shared outcomes table."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictRecord = {"sComparisonPolicy": policyV1.S_COMPARISON_POLICY,
                  "listFileOutcomes": flistGradedOutcomes()}
    comparisonPolicyNotes.fnAttachPolicyNotes(dictRecord)
    sRendered = pageDashboard.evaluate(
        "(listOutcomes) => VaibifyFileOutcomes.fsRenderFileOutcomesTable("
        "listOutcomes)", dictRecord["listFileOutcomes"])
    pageDashboard.evaluate(
        "(sHtml) => { var el = document.createElement('div');"
        " el.id = 'outcomesUnderTest'; el.innerHTML = sHtml;"
        " document.body.appendChild(el); }", sRendered)
    sVectorRow = pageDashboard.inner_text(
        "#outcomesUnderTest li:has-text('Plot/figure.pdf')")
    sDataRow = pageDashboard.inner_text(
        "#outcomesUnderTest li:has-text('Step/table.bin')")
    assert "vaibify cannot assess a changed vector figure" in sVectorRow
    assert "cannot pass, whatever comparisons exist" in sVectorRow
    assert "no published comparison covers it" in sDataRow
    assert "cannot pass" not in sDataRow
    assert pageDashboard.listPageErrors == []
