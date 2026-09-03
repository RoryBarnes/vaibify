"""Print the pytest selection a CI lane runs, read from its workflow.

The count badges need to know how many tests a lane like `security`
contains. Copying its file list into `badges.yml` would work on the day
it was written and silently lie afterwards: the badge would keep
reporting a number for a selection the lane had stopped running, and
nothing compares the two. So the list is DERIVED here instead --
`badges.yml` asks the workflow what it runs, and the answer cannot
disagree with itself.

Usage::

    python tools/deriveLaneSelection.py .github/workflows/security.yml
"""

import re
import sys

import yaml

__all__ = ["flistSelectLaneArguments"]

S_TEST_PATH_PATTERN = r"tests/[A-Za-z0-9_/]+\.py"
S_MARKER_PATTERN = r'-m\s+"([^"]+)"'


def flistSelectLaneArguments(sWorkflowPath):
    """Return the test paths and marker expression a workflow's steps run."""
    try:
        with open(sWorkflowPath, encoding="utf-8") as fileWorkflow:
            dictWorkflow = yaml.safe_load(fileWorkflow)
    except OSError as errorRead:
        raise SystemExit(f"cannot read {sWorkflowPath}: {errorRead}")
    except yaml.YAMLError as errorParse:
        raise SystemExit(f"{sWorkflowPath} is not valid YAML: {errorParse}")

    listArguments = []
    for dictJob in (dictWorkflow or {}).get("jobs", {}).values():
        for dictStep in dictJob.get("steps", []) or []:
            sRun = dictStep.get("run") or ""
            if "pytest" not in sRun:
                continue
            listFiles = sorted(set(re.findall(S_TEST_PATH_PATTERN, sRun)))
            if not listFiles:
                continue
            listArguments.extend(listFiles)
            matchMarker = re.search(S_MARKER_PATTERN, sRun)
            if matchMarker:
                listArguments.extend(["-m", matchMarker.group(1)])

    if not listArguments:
        raise SystemExit(
            f"{sWorkflowPath} names no pytest test files; a count badge "
            f"derived from it would be a confident zero."
        )
    return listArguments


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: deriveLaneSelection.py <workflow.yml>")
    # ONE ARGUMENT PER LINE, never a shell-quoted string. A marker
    # expression contains spaces ("not docker and not browser"), and
    # quotes emitted here do NOT survive command substitution -- the
    # shell splits on whitespace and pytest receives `not` and `docker`
    # as separate arguments, selecting nothing. That shipped a security
    # count of 0 in development; the caller reads these with `mapfile`
    # so a space inside an argument stays inside it.
    for sArgument in flistSelectLaneArguments(sys.argv[1]):
        print(sArgument)


if __name__ == "__main__":
    main()
