"""Every workflow runs before a merge or after it, never both.

Until 2026-07-28 six workflows triggered on `pull_request` *and* on
`push: [main]`, so the whole suite ran twice for every change: once to
decide whether it could merge, and again on the merge commit, where the
answer could no longer change anything. Roughly half of CI's cost was
being spent re-asking a question that had already gated the merge.

The split is: the test suites gate the merge; documentation, badges and
distributions are built from `main` once the merge has happened. Branch
protection is what makes the pre-merge half sufficient -- it is why
dropping `push: [main]` from the test workflows does not leave `main`
unverified.

Prose could not hold this. The duplication is one line of YAML to
reintroduce and is invisible in review, so the rule lives here.
"""

from pathlib import Path

import pytest
import yaml

_PATH_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# Runs before a merge: these decide whether a change may land.
T_PRE_MERGE_WORKFLOWS = (
    "tests-linux.yml",
    "tests-macos.yml",
    "browser.yml",
    "falsification.yml",
    "agentDocsPathCheck.yml",
)

# Runs after a merge: these publish or package what `main` now is.
T_POST_MERGE_WORKFLOWS = (
    "docs.yml",
    "badges.yml",
    "pip-install.yml",
)


def _fdictTriggers(sWorkflowName):
    """Return a workflow's ``on:`` mapping.

    ``on`` is a YAML 1.1 boolean, so ``safe_load`` yields the key
    ``True``; both spellings are accepted so this does not depend on
    the loader's dialect.
    """
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text()
    )
    dictOn = dictWorkflow.get("on", dictWorkflow.get(True))
    return dictOn if isinstance(dictOn, dict) else {}


def _fbPushesToMain(dictOn):
    """Return True when a workflow triggers on a push to main."""
    dictPush = dictOn.get("push")
    if not isinstance(dictPush, dict):
        return "push" in dictOn
    return "main" in (dictPush.get("branches") or [])


@pytest.mark.parametrize("sWorkflow", T_PRE_MERGE_WORKFLOWS)
def testPreMergeWorkflowsDoNotAlsoRunOnMain(sWorkflow):
    """A merge gate must not re-run itself on the merge commit."""
    dictOn = _fdictTriggers(sWorkflow)
    assert "pull_request" in dictOn, (
        f"{sWorkflow} is a merge gate but does not trigger on "
        f"pull_request; nothing would block a bad merge."
    )
    assert not _fbPushesToMain(dictOn), (
        f"{sWorkflow} runs on both pull_request and push to main. The "
        f"second run cannot change whether the merge happened, so it "
        f"is duplicated cost, not coverage."
    )


@pytest.mark.parametrize("sWorkflow", T_POST_MERGE_WORKFLOWS)
def testPostMergeWorkflowsDoNotRunOnPullRequests(sWorkflow):
    """Publishing and packaging happen from main, not from a PR."""
    dictOn = _fdictTriggers(sWorkflow)
    assert _fbPushesToMain(dictOn) or "release" in dictOn, (
        f"{sWorkflow} publishes from main but triggers on neither a "
        f"push to main nor a release."
    )
    assert "pull_request" not in dictOn, (
        f"{sWorkflow} runs on pull requests as well as after a merge. "
        f"Building the same artifact twice per change is the "
        f"duplication this split removed."
    )


def testEveryWorkflowIsClassifiedOrDeliberatelyScheduled():
    """No workflow may quietly sit outside the split.

    A new workflow added with both triggers is exactly the regression
    these tests exist to prevent, and it would go unnoticed if the
    lists above were the only thing checked. Anything not classified
    must be schedule- or dispatch-driven (the container lanes and the
    mutation gate), never event-driven on main.
    """
    setClassified = set(T_PRE_MERGE_WORKFLOWS) | set(T_POST_MERGE_WORKFLOWS)
    listUnclassified = sorted(
        pathFile.name for pathFile in _PATH_WORKFLOWS.glob("*.yml")
        if pathFile.name not in setClassified
    )
    listOffenders = []
    for sWorkflow in listUnclassified:
        dictOn = _fdictTriggers(sWorkflow)
        if "pull_request" in dictOn and _fbPushesToMain(dictOn):
            listOffenders.append(sWorkflow)
    assert listOffenders == [], (
        f"these workflows run both before and after a merge and belong "
        f"in one of the two lists above: {listOffenders}"
    )
