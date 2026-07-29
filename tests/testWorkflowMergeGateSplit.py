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
)

# Runs when a version is cut, matching vspace / bigplanet /
# multi-planet. Not a merge gate and not a per-merge publisher: the
# distribution is built for the release that will carry it.
T_RELEASE_ONLY_WORKFLOWS = (
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


@pytest.mark.parametrize("sWorkflow", T_RELEASE_ONLY_WORKFLOWS)
def testReleaseOnlyWorkflowsRunOnNeitherSideOfAMerge(sWorkflow):
    """A release build must not become a per-merge or per-PR job.

    Requiring one of these as a merge check is the specific failure to
    avoid: it cannot report on a pull request, so the PR waits on a
    status that will never arrive. That happened the day this split
    landed — two `pip-install` job names were left in the branch
    ruleset and blocked an otherwise fully green PR.
    """
    dictOn = _fdictTriggers(sWorkflow)
    assert "release" in dictOn, (
        f"{sWorkflow} is the release build but does not trigger on a "
        f"release."
    )
    assert "pull_request" not in dictOn, (
        f"{sWorkflow} triggers on pull_request; a release build is not "
        f"a merge gate."
    )
    assert not _fbPushesToMain(dictOn), (
        f"{sWorkflow} triggers on a push to main; distributions are "
        f"built when a version is cut, not on every merge."
    )


@pytest.mark.parametrize("sWorkflow", T_PRE_MERGE_WORKFLOWS)
def testReadmeDoesNotUseGitHubStatusBadgesForMergeGates(sWorkflow):
    """A merge-gate badge must describe main, not the newest branch run.

    GitHub's workflow badge shows the latest run on ANY branch, which
    for a pull-request-gated lane means a contributor's failing PR
    reddens the README while main is fine. Verified behaviour, not
    theory: on a workflow with only pull-request runs, `?branch=main`
    renders "no status" while the unqualified badge renders the PR run.

    These lanes therefore read from the endpoint json badges.yml
    computes from the merge commit's pull request.
    """
    sReadme = (_PATH_WORKFLOWS.parent.parent / "README.md").read_text()
    sBuiltInBadge = f"actions/workflows/{sWorkflow}/badge.svg"
    assert sBuiltInBadge not in sReadme, (
        f"README uses GitHub's built-in status badge for {sWorkflow}, "
        f"which reports the newest run on any branch. Merge-gate lanes "
        f"must use the badges/status*.json endpoints instead."
    )


def testEveryWorkflowIsClassifiedOrDeliberatelyScheduled():
    """No workflow may quietly sit outside the split.

    A new workflow added with both triggers is exactly the regression
    these tests exist to prevent, and it would go unnoticed if the
    lists above were the only thing checked. Anything not classified
    must be schedule- or dispatch-driven (the container lanes and the
    mutation gate), never event-driven on main.
    """
    setClassified = (
        set(T_PRE_MERGE_WORKFLOWS)
        | set(T_POST_MERGE_WORKFLOWS)
        | set(T_RELEASE_ONLY_WORKFLOWS)
    )
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


# The badges branch is machine-generated and its whole contract is that
# it holds nothing but badge json. The first run of the merge-status
# step broke that: the step wrote its API response into the checkout and
# the publish step added `./*.json`, so a 70 kB dump of workflow-run
# metadata was committed as if it were a badge.
T_PUBLISHED_BADGES = (
    "tests.json",
    "falsification.json",
    "invariants.json",
    "browser.json",
    "statusTestsLinux.json",
    "statusTestsMacos.json",
    "statusFalsification.json",
    "statusBrowser.json",
    "statusAgentDocs.json",
)


def _fsBadgesPublishStep():
    """Return the shell body of badges.yml's publish step."""
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / "badges.yml").read_text()
    )
    for dictStep in dictWorkflow["jobs"]["publish-badges"]["steps"]:
        if "orphan badges branch" in (dictStep.get("name") or ""):
            return dictStep.get("run", "")
    raise AssertionError("badges.yml has no publish step")


def testBadgesArePublishedByNameNotByGlob():
    """Only named badge files may reach the badges branch.

    A glob cannot distinguish a badge from whatever else a previous step
    happened to leave in the working directory, and the failure is
    invisible in review — `git add ./*.json` looks like housekeeping.
    """
    sStep = _fsBadgesPublishStep()
    for sGlob in ("./*.json", "*.json ."):
        assert sGlob not in sStep, (
            f"badges.yml publishes with the glob {sGlob!r}; publish by "
            f"name so only badges can ever land on the branch."
        )
    for sBadge in T_PUBLISHED_BADGES:
        assert sBadge in sStep, (
            f"{sBadge} is written by badges.yml but never published."
        )


def testTheMergeStatusDumpIsWrittenOutsideTheCheckout():
    """The resolved API response must not sit in the worktree.

    Keeping it in RUNNER_TEMP means no future glob, however careless,
    can publish it.
    """
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / "badges.yml").read_text()
    )
    listSteps = dictWorkflow["jobs"]["publish-badges"]["steps"]
    listWriters = [
        dictStep.get("run", "") for dictStep in listSteps
        if "gateRuns.json" in (dictStep.get("run") or "")
        and "git rm" not in (dictStep.get("run") or "")
    ]
    assert listWriters, "no step references the merge-status dump"
    for sRun in listWriters:
        assert "RUNNER_TEMP" in sRun, (
            "gateRuns.json is read or written without RUNNER_TEMP, so "
            "it lands in the checkout where a glob can publish it."
        )
