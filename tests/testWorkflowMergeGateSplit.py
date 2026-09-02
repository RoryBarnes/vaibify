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

import json
import re
from pathlib import Path

import pytest
import yaml

_PATH_REPO = Path(__file__).resolve().parent.parent
_PATH_WORKFLOWS = _PATH_REPO / ".github" / "workflows"

# Runs before a merge: these decide whether a change may land.
T_PRE_MERGE_WORKFLOWS = (
    "tests-linux.yml",
    "tests-macos.yml",
    "browser.yml",
    "falsification.yml",
    "security.yml",
    "agentDocsPathCheck.yml",
    "styleContract.yml",
    # The remote transport, over a real sshd. A pre-merge gate because
    # it is the ONLY thing that proves the client's argv and the
    # helper's record meet: everything else about the feature passes
    # with the two halves never having spoken. Its no-skip-green guard
    # is what makes that claim worth anything.
    "remoteSsh.yml",
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

    Anything not classified must be schedule- or dispatch-driven (the
    container lanes and the mutation gate), never event-driven on main.
    An earlier version flagged only workflows carrying *both* triggers,
    which let an unclassified lane with a lone ``push: [main]`` or a
    lone ``pull_request`` sail through — the exact regression the test
    claims to prevent. One exception is deliberate: a *path-filtered*
    ``pull_request`` trigger (``freshImageBuild.yml``) is a conditional
    lane that runs only when its named files change; an unfiltered one
    is an unregistered merge gate whose result nothing requires.
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
        if _fbPushesToMain(dictOn):
            listOffenders.append(f"{sWorkflow} (push to main)")
            continue
        dictPullRequest = dictOn.get("pull_request")
        bPathFiltered = (
            isinstance(dictPullRequest, dict)
            and bool(dictPullRequest.get("paths"))
        )
        if "pull_request" in dictOn and not bPathFiltered:
            listOffenders.append(f"{sWorkflow} (unfiltered pull_request)")
    assert listOffenders == [], (
        f"these workflows are event-driven but sit outside the "
        f"pre/post-merge split; classify them in the lists above or "
        f"make them schedule/dispatch-driven: {listOffenders}"
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
    "statusSecurity.json",
    "statusAgentDocs.json",
    "statusStyleContract.json",
    "statusRemoteSsh.json",
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


def _fsWorkflowDisplayName(sWorkflowName):
    """Return the ``name:`` GitHub reports a workflow's runs under."""
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text()
    )
    sName = dictWorkflow.get("name")
    assert sName, f"{sWorkflowName} declares no top-level name:"
    return sName


@pytest.mark.parametrize("sWorkflow", T_PRE_MERGE_WORKFLOWS)
def testEveryMergeGateReachesTheReadmeAsAStatusBadge(sWorkflow):
    """A gate nobody can see on the README is a gate nobody reads.

    The companion test above forbids GitHub's built-in badge for these
    lanes, and a lane with NO badge at all satisfies that trivially --
    which is how `style-contract` and `remote-ssh` came to gate every
    merge while appearing on the README nowhere, and in badges.yml's
    lane map nowhere. Absence looked exactly like compliance.

    The three places are pinned to each other rather than re-typed:
    the workflow's own `name:` must key badges.yml's merge-gate map,
    and the file that map names must be both published to the badges
    branch and rendered by the README. Nothing here hand-copies a
    badge filename, so a new gate cannot be half-registered.
    """
    sDisplayName = _fsWorkflowDisplayName(sWorkflow)
    sBadges = (_PATH_WORKFLOWS / "badges.yml").read_text()
    matchLane = re.search(
        rf'"{re.escape(sDisplayName)}":\s*"(status\w+\.json)"', sBadges,
    )
    assert matchLane, (
        f"{sWorkflow} gates every merge but is absent from badges.yml's "
        f"DICT_MERGE_GATE_LANES, so no status badge is ever computed "
        f"for it. Add a \"{sDisplayName}\" entry."
    )
    sBadgeFile = matchLane.group(1)
    assert sBadgeFile in _fsBadgesPublishStep(), (
        f"badges.yml computes {sBadgeFile} for {sWorkflow} but never "
        f"publishes it; the README would render a permanent 404."
    )
    sReadme = (_PATH_REPO / "README.md").read_text()
    assert sBadgeFile in sReadme, (
        f"{sBadgeFile} is published for {sWorkflow} but the README "
        f"does not display it, so the lane's result is invisible to "
        f"anyone reading the repository front page."
    )


# The README states each unit lane's support matrix as a static
# shields.io label beside its status badge, because the endpoint json
# carries only pass/fail. The label is therefore hand-typed, and a
# hand-typed fact about the matrix drifts the day the matrix moves --
# the same class that let a "scope: regression subset" label outlive
# the lane it described. These are the workflow -> label pairs, pinned
# to the matrix rather than to each other's spelling.
DICT_MATRIX_LABEL_WORKFLOWS = {
    "tests-linux.yml": "Ubuntu",
    "tests-macos.yml": "macOS",
}


def _ftMatrixExtremes(sWorkflowName):
    """Return (oldest os, newest os, oldest python, newest python).

    Versions are compared as tuples of integers, so `macos-9` would
    sort below `macos-15` where a string compare puts it above, and
    Python 3.9 stays below 3.14 for the same reason.
    """
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text()
    )
    for dictJob in dictWorkflow.get("jobs", {}).values():
        dictMatrix = (dictJob.get("strategy") or {}).get("matrix") or {}
        if "os" in dictMatrix and "python-version" in dictMatrix:
            break
    else:
        raise AssertionError(f"{sWorkflowName} has no os/python matrix")

    def _ftVersion(sValue):
        sTail = str(sValue).split("-")[-1]
        return tuple(int(sPart) for sPart in sTail.split(".") if sPart.isdigit())

    listOs = sorted(dictMatrix["os"], key=_ftVersion)
    listPython = sorted(dictMatrix["python-version"], key=_ftVersion)
    return listOs[0], listOs[-1], listPython[0], listPython[-1]


@pytest.mark.parametrize(
    "sWorkflow, sPlatform", sorted(DICT_MATRIX_LABEL_WORKFLOWS.items()),
)
def testTheReadmeMatrixLabelMatchesTheRealMatrix(sWorkflow, sPlatform):
    """The advertised support matrix must be the one CI runs.

    A badge saying "Python 3.9-3.14" beside a green check is read as a
    tested claim, and it is the sort of claim a colleague acts on by
    installing on 3.9. Nothing regenerates it, so dropping the oldest
    Python from the matrix leaves the README promising a version CI
    stopped exercising -- green, and wrong.
    """
    sOldestOs, sNewestOs, sOldestPython, sNewestPython = (
        _ftMatrixExtremes(sWorkflow)
    )
    sReadme = (_PATH_REPO / "README.md").read_text()
    listLabels = [
        sLine for sLine in sReadme.splitlines()
        if "img.shields.io/badge/" in sLine and sPlatform in sLine
    ]
    assert len(listLabels) == 1, (
        f"expected exactly one static {sPlatform} matrix label in the "
        f"README, found {len(listLabels)}: {listLabels}"
    )
    sLabel = listLabels[0]
    # The OS is matched on its MAJOR version only: the label reads
    # "Ubuntu 22-24" for `ubuntu-22.04`, and abbreviating a point
    # release is legitimate. Python is matched in full, because 3.9 and
    # 3.14 are the whole claim.
    def _fsOsMajor(sImage):
        return sImage.split("-")[-1].split(".")[0]

    for sVersion in (
        _fsOsMajor(sOldestOs), _fsOsMajor(sNewestOs),
        sOldestPython, sNewestPython,
    ):
        assert sVersion in sLabel, (
            f"{sWorkflow} runs {sVersion} but the README's {sPlatform} "
            f"label does not mention it, so the front page advertises a "
            f"support matrix CI does not run: {sLabel.strip()}"
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


# A job's name IS its status-check name, which is the string a human
# searches for when adding a required check and the string a ruleset
# matches on. Two lanes shipped names that broke both uses: `browser`'s
# job was called "frontend (chromium)", invisible to anyone searching
# "browser", and `falsification` reused the tests matrix template, so
# `ubuntu-24.04:python-3.14` was emitted by two workflows and could not
# be required independently. Both stayed out of the required set while
# appearing to gate every pull request.
_T_MATRIX_TOKENS = (
    ("${{ matrix.os }}", "os"),
    ("${{ matrix.python-version }}", "python-version"),
    # The shard dimension. This list and the tool's are edited
    # independently ON PURPOSE -- the agreement test below is only
    # worth something if the two are derived separately -- so a new
    # matrix key has to be added in both places. Missing here, the
    # falsification lane's name kept a literal ``${{ matrix.shard }}``,
    # which no job reports and which, required, blocks every merge.
    ("${{ matrix.shard }}", "shard"),
)


def _flistExpandJobNames(sWorkflowName):
    """Return the concrete check names a workflow's jobs produce."""
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text()
    )
    listNames = []
    for dictJob in dictWorkflow.get("jobs", {}).values():
        sName = dictJob.get("name")
        if not sName:
            continue
        dictMatrix = (dictJob.get("strategy") or {}).get("matrix") or {}
        listExpanded = [sName]
        for sToken, sKey in _T_MATRIX_TOKENS:
            listValues = dictMatrix.get(sKey)
            if not isinstance(listValues, list):
                continue
            listExpanded = [
                sCandidate.replace(sToken, str(sValue))
                for sCandidate in listExpanded
                for sValue in listValues
            ]
        listNames.extend(listExpanded)
    return listNames


def testNoTwoMergeGateLanesProduceTheSameCheckName():
    """A required check must name exactly one lane.

    When two workflows emit the same check name, requiring it is
    satisfied by whichever one reports, so the other is not actually
    gating anything — and the ruleset gives no hint that this is so.
    """
    dictOwners = {}
    for sWorkflow in T_PRE_MERGE_WORKFLOWS:
        for sCheck in _flistExpandJobNames(sWorkflow):
            dictOwners.setdefault(sCheck, []).append(sWorkflow)
    dictCollisions = {
        sCheck: listOwners
        for sCheck, listOwners in dictOwners.items()
        if len(listOwners) > 1
    }
    assert dictCollisions == {}, (
        f"these check names are produced by more than one merge-gate "
        f"workflow, so requiring them cannot gate both: {dictCollisions}"
    )


def _fmoduleLoadRequiredCheckTool():
    """Import tools/syncRequiredChecks.py by path."""
    import importlib.util

    pathTool = _PATH_REPO / "tools" / "syncRequiredChecks.py"
    specTool = importlib.util.spec_from_file_location(
        "syncRequiredChecks", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    specTool.loader.exec_module(moduleTool)
    return moduleTool


def testTheRequiredCheckToolCreatesTheRuleWhenItIsAbsent(monkeypatch):
    """A ruleset with no required-checks rule must be given one.

    The tool used to raise SystemExit here and tell the reader to add
    the rule in the GitHub UI. That is how `main` came to sit with NO
    required status checks on 2026-09-02 while `docs/testing.md` argued
    that branch protection is what makes the pre-merge-only split safe:
    the tool declined, nobody added it by hand, and a ruleset with zero
    required checks is indistinguishable on a pull request page from a
    healthy one -- every lane runs, every lane reports, and none of them
    gates.

    The rule must also be born non-empty: GitHub answers 422
    ("Expected at least 1 elements, got 0") for an empty list, so
    creating it and filling it cannot be two calls.
    """
    moduleTool = _fmoduleLoadRequiredCheckTool()

    dictRulesetWithoutTheRule = {
        "id": 1, "node_id": "x", "created_at": "", "updated_at": "",
        "_links": {}, "current_user_can_bypass": "never",
        "source": "o/r", "source_type": "Repository",
        "name": "main", "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                    "exclude": []}},
        "bypass_actors": [],
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }

    class _ResultStub:
        stdout = json.dumps(dictRulesetWithoutTheRule)

    monkeypatch.setattr(
        moduleTool.subprocess, "run",
        lambda *args, **kwargs: _ResultStub(),
    )

    dictPayload = moduleTool.fdictBuildRulesetPayload(
        ["unit:ubuntu-24.04:python-3.14", "browser"],
    )

    listRules = [
        dictRule for dictRule in dictPayload["rules"]
        if dictRule["type"] == "required_status_checks"
    ]
    assert len(listRules) == 1, (
        "the tool did not create a required_status_checks rule for a "
        "ruleset that had none, so running it leaves main unprotected."
    )
    dictParameters = listRules[0]["parameters"]
    assert dictParameters["strict_required_status_checks_policy"] is True, (
        "the created rule omits the up-to-date requirement, which is "
        "what stops two stale-green pull requests merging a break."
    )
    assert [
        dictCheck["context"]
        for dictCheck in dictParameters["required_status_checks"]
    ] == ["unit:ubuntu-24.04:python-3.14", "browser"], (
        "the created rule must carry the derived contexts; GitHub "
        "rejects an empty required_status_checks list with a 422."
    )
    # The pre-existing rules survive, and the read-only fields the API
    # rejects on a PUT are stripped.
    listTypes = [dictRule["type"] for dictRule in dictPayload["rules"]]
    assert "deletion" in listTypes and "non_fast_forward" in listTypes, (
        "creating the rule dropped rules the ruleset already had."
    )
    for sField in moduleTool.T_READ_ONLY_FIELDS:
        assert sField not in dictPayload, (
            f"{sField} is read-only and must not be sent back on a PUT."
        )


def testTheRequiredCheckToolAgreesWithThisSuite():
    """``tools/syncRequiredChecks.py`` must gate the same lanes as this file.

    The tool writes the ruleset; this suite decides which lanes are
    merge gates. Two independent lists of the same thing is how a lane
    ends up enforced in one place and forgotten in the other, so they
    are compared rather than trusted to stay in step.
    """
    import importlib.util

    pathTool = _PATH_REPO / "tools" / "syncRequiredChecks.py"
    specTool = importlib.util.spec_from_file_location(
        "syncRequiredChecks", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    specTool.loader.exec_module(moduleTool)

    assert set(moduleTool.T_GATE_WORKFLOWS) == set(T_PRE_MERGE_WORKFLOWS), (
        "syncRequiredChecks.py and this suite disagree about which "
        "workflows gate a merge."
    )
    setFromSuite = set()
    for sWorkflow in T_PRE_MERGE_WORKFLOWS:
        setFromSuite.update(_flistExpandJobNames(sWorkflow))
    assert set(moduleTool.flistRequiredContexts()) == setFromSuite, (
        "the tool and this suite expand the gate workflows to different "
        "check names; the ruleset would be written from the wrong set."
    )


def testNoDerivedCheckNameCarriesAnUnexpandedMatrixToken():
    """A required check name must be one a job can actually report.

    The sync tool builds required-check names by substituting matrix
    values into each job's ``name:``. A dimension it does not know about
    survives as a literal ``${{ matrix.something }}``, and that name is
    reported by nothing -- so requiring it blocks every merge on a check
    that cannot arrive. The tool's docstring describes this failure for
    matrix include/exclude, where it REFUSES; an unknown dimension
    reaches the same place by failing open into a name instead.

    It was not hypothetical: the falsification lane's shard dimension
    was missing, so the tool offered two such names. Nothing had applied
    them yet, which is the only reason it had not been noticed.
    """
    import importlib.util

    pathTool = _PATH_REPO / "tools" / "syncRequiredChecks.py"
    specTool = importlib.util.spec_from_file_location(
        "syncRequiredChecks", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    specTool.loader.exec_module(moduleTool)

    listOffenders = [
        sContext for sContext in moduleTool.flistRequiredContexts()
        if "${{" in sContext or "matrix." in sContext
    ]
    assert listOffenders == [], (
        "these derived check names still carry a matrix expression, so "
        "no job will ever report them and requiring one would block "
        f"every merge: {listOffenders}. Add the dimension to "
        "T_MATRIX_TOKENS in tools/syncRequiredChecks.py."
    )
