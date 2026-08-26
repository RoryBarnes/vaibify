"""A category with no commands reads N/A, never "Untested".

``unnecessary`` is the backend's value for a test category that defines
no commands, and it is a GREEN value there: ``stepPredicates`` and
``truthDerivation`` both count it toward Level 1. The frontend had no
case for it, so it fell through ``dictLabels[sState] || "Untested"``
and the dashboard stated the opposite of what the backend had derived.

That is a state-honesty failure in both directions. A researcher
reading "Untested" cannot tell a real gap from a category that is
legitimately N/A, and it manufactures wrong explanations: a container
agent that read ``"sQualitative": "unnecessary"`` in state.json,
compared it against "Untested" on the dashboard, and reconciled the two
by inventing a permission rule that does not exist in the action
catalog.

Grey, not green, is the deliberate choice. Nothing was proven here --
there was simply nothing to prove -- which is the same distinction the
falsification rows draw for "not applicable".

Kills (confirmed, not assumed): removing the ``unnecessary`` entry from
``fsVerificationStateLabel`` -> the badge reads "Untested" and the
assertion below fails naming the state it was given.
"""

import pytest

from tests.browser.conftest import (
    fnOpenTheSeededHostWorkflow,
    S_HOST_STEP_NAME,
)


pytestmark = pytest.mark.browser

# The seeded host workflow declares no dictTests at all, so every
# category derives to "unnecessary" on load -- the exact state the
# researcher's own project was in.
T_CATEGORIES = ("qualitative", "quantitative", "integrity")




def _fnExpandTheStepsTestCategories(pageDashboard):
    """Expand the step, then its Tests row, so the sub-rows render."""
    pageDashboard.click(f'.step-item:has-text("{S_HOST_STEP_NAME}")')
    pageDashboard.wait_for_selector(
        '.verification-row[data-approver="unitTest"]',
        state="visible", timeout=15000,
    )
    pageDashboard.click(
        '.verification-row[data-approver="unitTest"][data-step="0"]',
    )
    pageDashboard.wait_for_selector(
        '.sub-test-row[data-approver="qualitative"]',
        state="visible", timeout=15000,
    )


def test_a_category_with_no_commands_reads_not_applicable(
    pageDashboard, serverHub,
):
    """All three categories, because the bug was in the shared mapper.

    Asserting only the qualitative row would pass just as well against
    a fix that special-cased one category, which is not what was
    wrong.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnExpandTheStepsTestCategories(pageDashboard)

    for sCategory in T_CATEGORIES:
        elBadge = pageDashboard.locator(
            f'.sub-test-row[data-approver="{sCategory}"] '
            '.verification-badge',
        ).first
        sBadge = elBadge.text_content().strip()
        assert "N/A" in sBadge, (
            f"the {sCategory} category declares no commands, which the "
            "backend derives as 'unnecessary' and counts toward Level "
            f"1, but the dashboard reported {sBadge!r} -- a researcher "
            "cannot tell that from a real untested gap"
        )
        assert "Untested" not in sBadge, (
            f"the {sCategory} badge still says Untested: {sBadge!r}"
        )

    # Grey, not green: N/A must never be dressed as a passing test.
    sClass = pageDashboard.locator(
        '.sub-test-row[data-approver="qualitative"] .verification-badge',
    ).first.get_attribute("class")
    assert "state-unnecessary" in sClass, (
        "the N/A badge must carry its own class so it can be styled "
        f"grey rather than green; class was {sClass!r}"
    )
    assert pageDashboard.listPageErrors == []
