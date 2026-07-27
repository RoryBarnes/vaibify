"""Documentation must not describe a CI trigger the workflow lacks.

`docs/testing.md` and `docs/developers.md` both stated that the
cosmic-ray mutation gate ran on every pull request. It has been
`workflow_dispatch`-only since 94abe35, so for months the docs promised
per-PR mutation feedback that never happened. A reader budgeting trust
on "new code is checked for undefended guards" was reading a promise
nothing kept -- the same prose-drifts-from-code failure that the
falsification suite exists to catch in source.

Prose alone cannot hold this: the fix is a test that reads the actual
`on:` block and fails when the table disagrees with it.
"""

from pathlib import Path

import pytest
import yaml


_PATH_REPO = Path(__file__).resolve().parent.parent
_PATH_WORKFLOWS = _PATH_REPO / ".github" / "workflows"
_PATH_TESTING_DOC = _PATH_REPO / "docs" / "testing.md"


def _fsetWorkflowTriggers(sWorkflowName):
    """Return the set of event names a workflow triggers on.

    ``on`` is a YAML 1.1 boolean, so ``safe_load`` yields the key
    ``True`` rather than the string. Both spellings are accepted so
    this does not depend on the loader's dialect.
    """
    dictWorkflow = yaml.safe_load(
        (_PATH_WORKFLOWS / sWorkflowName).read_text()
    )
    dictOn = dictWorkflow.get("on", dictWorkflow.get(True))
    if isinstance(dictOn, dict):
        return set(dictOn.keys())
    if isinstance(dictOn, list):
        return set(dictOn)
    return {dictOn}


def _fsWorkflowTableRow(sWorkflowName):
    """Return the CI-table row in testing.md describing this workflow."""
    sPrefix = f"| `{sWorkflowName}`"
    for sLine in _PATH_TESTING_DOC.read_text().splitlines():
        if sLine.startswith(sPrefix):
            return sLine
    raise AssertionError(
        f"docs/testing.md has no CI-table row starting {sPrefix!r}; "
        "the table is the documented record of what CI runs."
    )


@pytest.mark.falsification
def test_documented_mutation_trigger_matches_the_workflow():
    """Kills: the docs claiming per-PR mutation coverage that CI drops.

    Mutation: restore the old ``on pull requests`` cell in the CI table
    while `mutation.yml` remains `workflow_dispatch`-only.
    """
    setTriggers = _fsetWorkflowTriggers("mutation.yml")
    sRow = _fsWorkflowTableRow("mutation.yml")
    if "pull_request" in setTriggers:
        assert "pull request" in sRow.lower(), (
            "mutation.yml triggers on pull_request but the CI table "
            "does not say so."
        )
        return
    assert "pull request" not in sRow.lower(), (
        "The CI table says the mutation gate runs on pull requests, "
        f"but mutation.yml triggers only on {sorted(setTriggers)}. "
        "Python would merge with no mutation feedback while the docs "
        "promised otherwise."
    )
    assert "workflow_dispatch" in sRow, (
        "A manual-only gate must say so in the CI table, or readers "
        "will assume it is automatic."
    )


def test_documented_test_workflow_triggers_match():
    """The per-PR suites really are per-PR, as the table claims."""
    for sWorkflow in ("tests-linux.yml", "falsification.yml"):
        setTriggers = _fsetWorkflowTriggers(sWorkflow)
        assert "pull_request" in setTriggers, (
            f"{sWorkflow} is documented as running on every pull "
            "request; its triggers are "
            f"{sorted(setTriggers)}."
        )


def test_no_stale_falsification_count_in_testing_doc():
    """Counts belong on the generated badges, not in hand-typed prose.

    The table used to carry '~146' long after the real number passed
    290. Removing the number removes the drift class outright.
    """
    sDoc = _PATH_TESTING_DOC.read_text()
    iMarker = sDoc.find("| **Falsification tests**")
    assert iMarker != -1, "The kinds-of-test table lost its row."
    sRow = sDoc[iMarker:sDoc.index("\n", iMarker)]
    assert "~" not in sRow, (
        "The falsification row carries a hand-typed approximate count "
        f"again: {sRow!r}. Counts live on the README badges, which "
        "badges.yml regenerates."
    )
