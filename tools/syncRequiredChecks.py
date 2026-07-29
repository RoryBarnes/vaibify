"""Set main's required status checks to what the merge gate reports.

Required checks are matched by *name*, so renaming a job silently
invalidates the ruleset entry that referenced it: the old name stops
being reported, the requirement is never satisfied, and every pull
request waits forever on a check that no longer exists. That is not
hypothetical -- it happened twice in one day, once when ``pip-install``
moved off pull requests and once when the unit jobs were renamed.

Rather than re-picking names by hand in the GitHub UI, derive them from
the workflows themselves. Run this after any change to a gate
workflow's job names, and before merging that change.

Deliberately excluded:

* ``results:<os>:python-<version>`` -- published by the test-results
  action, which reports the same run the matching ``unit:`` job already
  gates. Requiring both doubles the wait and adds no signal.
* every workflow outside the merge gate. ``pip-install`` and ``docs``
  do not run on pull requests at all, so requiring them would block
  every merge on a check that cannot report.

Usage::

    python tools/syncRequiredChecks.py            # print, change nothing
    python tools/syncRequiredChecks.py --apply    # write the ruleset
"""

import argparse
import json
import pathlib
import subprocess
import sys

import yaml

S_REPO = "RoryBarnes/vaibify"
S_RULESET_ID = "19934515"

_PATH_WORKFLOWS = (
    pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
)

# The lanes that gate a merge. Kept in step with T_PRE_MERGE_WORKFLOWS in
# tests/testWorkflowMergeGateSplit.py, which fails if one of these ever
# starts running after a merge instead of before it.
T_GATE_WORKFLOWS = (
    "tests-linux.yml",
    "tests-macos.yml",
    "browser.yml",
    "falsification.yml",
    "agentDocsPathCheck.yml",
)

T_MATRIX_TOKENS = (
    ("${{ matrix.os }}", "os"),
    ("${{ matrix.python-version }}", "python-version"),
)

# Fields the GET returns that the PUT will not accept.
T_READ_ONLY_FIELDS = (
    "id", "node_id", "created_at", "updated_at", "_links",
    "current_user_can_bypass", "source", "source_type",
)


def flistExpandJobNames(sWorkflowName):
    """Return the concrete check names one workflow's jobs produce.

    Refuses rather than guesses on the two shapes it cannot expand. A
    job without a ``name:`` would be reported to GitHub under its job
    key, so skipping it silently drops that lane from the required set
    — the lane runs, fails, and blocks nothing. A matrix carrying
    ``include``/``exclude`` expands to a different cell set than the
    plain product computed here, so the derived names would require
    checks that never report (blocking every merge) or miss cells that
    do.
    """
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text(encoding="utf-8")
    )
    listNames = []
    for sJobKey, dictJob in dictWorkflow.get("jobs", {}).items():
        sName = dictJob.get("name")
        if not sName:
            raise SystemExit(
                f"{sWorkflowName}: job {sJobKey!r} has no `name:`; give "
                f"it one so its check name is explicit, then re-run."
            )
        dictMatrix = (dictJob.get("strategy") or {}).get("matrix") or {}
        if "include" in dictMatrix or "exclude" in dictMatrix:
            raise SystemExit(
                f"{sWorkflowName}: job {sJobKey!r} uses matrix "
                f"include/exclude, which this tool cannot expand; "
                f"extend flistExpandJobNames before re-running."
            )
        listExpanded = [sName]
        for sToken, sKey in T_MATRIX_TOKENS:
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


def flistRequiredContexts():
    """Return every check name the merge gate reports, sorted."""
    setContexts = set()
    for sWorkflow in T_GATE_WORKFLOWS:
        setContexts.update(flistExpandJobNames(sWorkflow))
    return sorted(setContexts)


def fdictBuildRulesetPayload(listContexts):
    """Return the ruleset object with its required checks replaced."""
    sCurrent = subprocess.run(
        ["gh", "api", f"repos/{S_REPO}/rulesets/{S_RULESET_ID}"],
        capture_output=True, text=True, check=True,
    ).stdout
    dictPayload = {
        sKey: objValue
        for sKey, objValue in json.loads(sCurrent).items()
        if sKey not in T_READ_ONLY_FIELDS
    }
    bFound = False
    for dictRule in dictPayload.get("rules", []):
        if dictRule.get("type") != "required_status_checks":
            continue
        dictRule["parameters"]["required_status_checks"] = [
            {"context": sContext} for sContext in listContexts
        ]
        # The split removed every push-to-main test trigger, so branch
        # protection is the ONLY thing standing between a stale-green
        # pull request and an untested merge commit. Without the strict
        # up-to-date requirement, two individually-green pull requests
        # that conflict semantically can both merge and break main with
        # zero CI runs anywhere to notice. Re-assert it on every apply
        # so a UI change cannot silently reopen that window.
        dictRule["parameters"]["strict_required_status_checks_policy"] = True
        bFound = True
    if not bFound:
        raise SystemExit(
            "the ruleset has no required_status_checks rule; add one in "
            "the GitHub UI first, then re-run this."
        )
    return dictPayload


def fnApplyRuleset(dictPayload):
    """PUT the ruleset back."""
    subprocess.run(
        ["gh", "api", "-X", "PUT",
         f"repos/{S_REPO}/rulesets/{S_RULESET_ID}", "--input", "-"],
        input=json.dumps(dictPayload), text=True, check=True,
        stdout=subprocess.DEVNULL,
    )


def main():
    """Print the derived required-check set, and optionally apply it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="write the ruleset instead of only printing it",
    )
    dictArgs = parser.parse_args()

    listContexts = flistRequiredContexts()
    print(f"{len(listContexts)} checks the merge gate reports:")
    for sContext in listContexts:
        print("   ", sContext)
    if not dictArgs.apply:
        print("\nnothing written; re-run with --apply to update the ruleset")
        return 0
    fnApplyRuleset(fdictBuildRulesetPayload(listContexts))
    print("\nruleset updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
