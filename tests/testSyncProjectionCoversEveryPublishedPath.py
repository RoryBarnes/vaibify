"""A recorded remote divergence must reach the step row that owns it.

A researcher looked at an AI Declaration step whose declaration file
was out of sync with GitHub and saw a green check on "Outputs match
the GitHub mirror". Nothing was stale and nothing had failed: the
reverify HAD compared ``AI_USAGE.md`` against raw.githubusercontent
.com, HAD found it diverged, and HAD written that into
``syncStatus.json``. The projection from that recorded divergence onto
the per-step rows then dropped it, because
``_flistStepDivergedFiles`` intersected the divergence set with
``_flistStepOutputFiles`` alone — ``saOutputDataFiles`` plus
``saPlotFiles``.

The declaration step made the defect unmissable because it has NO
outputs at all: its GitHub row could not render anything but a check,
whatever the remote held. But the hole was never about declarations.
The reverify compares every canonical path — inputs, scripts, test
standards, generated test files and the declaration — and each of them
was equally invisible to the per-step rows. A diverged SCRIPT produced
the same false pass, one file at a time, on ordinary data steps.

The direction of the error is what makes it serious. This is a gate
that exists to avoid overstating publication, and it was reporting
"matches the published copy" about a file it had just proved did not.

The projection now runs over every path a step declares. Over-
inclusion is inert — ``setDiverged`` holds only paths the reverify
actually compared, so a path that was never compared cannot intersect
it — and that asymmetry is why the sync projection has its own
collector rather than reusing ``_flistStepDeclaredPaths``, whose L3
manifest check is allowed to be wrong in the OPPOSITE direction.

Kills (confirmed, not assumed): restoring ``_flistStepOutputFiles`` in
``_flistStepDivergedFiles`` -> every category test below fails with an
empty blocker list, and the declaration test fails naming the check
the researcher saw.
"""

import pytest

from vaibify.reproducibility.levelGates import (
    _flistPerStepSyncBlockers,
    _flistStepPublishedPaths,
    fdictComputeStepLevelStates,
)


S_DECLARATION = "AI_USAGE.md"


def _fdictDeclarationStep():
    """An ai-declaration step: no outputs, one declaration file."""
    return {
        "sName": "AI Declaration",
        "sDirectory": "AIDeclaration",
        "sStepKind": "ai-declaration",
        "sDeclarationFile": S_DECLARATION,
        "bInteractive": True,
        "saOutputDataFiles": [],
        "saPlotFiles": [],
        "dictVerification": {"sUser": "passed"},
    }


def _fdictWorkflowAround(dictStep):
    return {"sProjectRepoPath": "/repo", "listSteps": [dictStep]}


def _fdictDivergedStatus(sPath):
    """The syncStatus.json a reverify writes when one file differs."""
    return {
        "sService": "github",
        "sLastVerified": "2026-08-25T00:00:00Z",
        "iTotalFiles": 4,
        "iMatching": 3,
        "listDiverged": [
            {"sPath": sPath, "sExpected": "aaa", "sActual": "bbb"},
        ],
    }


def _flistBlockersFor(dictStep, sDivergedPath):
    return _flistPerStepSyncBlockers(
        _fdictWorkflowAround(dictStep),
        _fdictDivergedStatus(sDivergedPath),
        "not-in-github-mirror",
        "hint",
    )


def test_a_diverged_declaration_reaches_its_step_row():
    """The bug, in the shape the researcher hit it."""
    listBlockers = _flistBlockersFor(
        _fdictDeclarationStep(), S_DECLARATION,
    )
    assert listBlockers, (
        "the reverify recorded AI_USAGE.md as diverged from GitHub "
        "and the declaration step's row emitted no blocker at all — "
        "the row renders a check while the file does not match"
    )
    assert listBlockers[0]["listOffendingFiles"] == [S_DECLARATION]


def test_the_requirement_row_reads_unmet_end_to_end():
    """Through the cell projection, not just the blocker producer.

    The blocker list and the requirement rows are separate stages and
    only the first was broken, so a test that stopped at the blocker
    would not show the researcher what changed on screen.
    """
    dictStates = fdictComputeStepLevelStates(
        _fdictWorkflowAround(_fdictDeclarationStep()),
        [],
        _flistBlockersFor(_fdictDeclarationStep(), S_DECLARATION),
        [],
    )
    dictByName = {
        dictReq["sName"]: dictReq["bMet"]
        for dictReq in dictStates[0]["s2"]["listRequirements"]
    }
    assert dictByName["github-mirror"] is False, (
        f"the GitHub row still reads satisfied: {dictByName}"
    )


@pytest.mark.parametrize("sKey,sDeclared,sResolved", [
    # OUTPUTS and scripts are declared step-relative and the collector
    # prefixes sDirectory; INPUTS are already repo-relative. Declaring
    # an output repo-relative resolves to "MakeData/Step/out.json",
    # which is a path nothing publishes.
    ("saOutputDataFiles", "out.json", "MakeData/out.json"),
    ("saInputDataFiles", "Step/input.csv", "Step/input.csv"),
    ("saPlotFiles", "figure.png", "MakeData/figure.png"),
])
def test_every_declared_path_array_reaches_the_row(
    sKey, sDeclared, sResolved,
):
    """Outputs kept working, and the other arrays started to.

    Outputs are included so a regression that swapped one subset for
    another — rather than widening — still fails here.

    The diverged path is the RESOLVED spelling, because that is what a
    remote verify records: it compares repo-relative paths, so a
    projection that emitted the raw declaration could never intersect
    the divergence list and would report a diverged file as published.
    """
    dictStep = {
        "sName": "Make Data", "sDirectory": "MakeData",
        "saOutputDataFiles": [], "saPlotFiles": [],
        sKey: [sDeclared],
    }
    assert _flistBlockersFor(dictStep, sResolved), (
        f"a diverged {sKey} entry produced no blocker"
    )


def test_a_diverged_script_reaches_the_row():
    """The same false pass on an ordinary data step.

    Scripts are extracted from the step's commands rather than
    declared in an array, so this exercises a different collector than
    the parametrized case above. The command names the script
    relative to the STEP DIRECTORY, which is how a workflow really
    spells it — the collector prefixes ``sDirectory`` itself.
    """
    dictStep = {
        "sName": "Make Data", "sDirectory": "MakeData",
        "saDataCommands": ["python3 makeData.py --out x.json"],
        "saOutputDataFiles": [], "saPlotFiles": [],
    }
    listPaths = _flistStepPublishedPaths(dictStep, {})
    assert "MakeData/makeData.py" in listPaths, listPaths
    assert _flistBlockersFor(dictStep, "MakeData/makeData.py"), (
        "a step whose SCRIPT differs from the published copy still "
        "reports its published files as matching"
    )


def test_an_uncompared_path_cannot_manufacture_a_blocker():
    """Over-inclusion is inert, and that is load-bearing.

    The widened projection is only safe because ``setDiverged``
    contains just the paths the reverify compared. If a path the
    projection lists but the verify never examined could raise a
    blocker, widening would trade a false pass for a false failure.
    """
    dictStep = _fdictDeclarationStep()
    listBlockers = _flistBlockersFor(dictStep, "SomeOther/file.json")
    assert listBlockers == [], (
        f"a path this step does not declare raised a blocker on it: "
        f"{listBlockers}"
    )


def test_a_clean_verify_still_leaves_every_row_green():
    """No divergence recorded -> no blocker, for the widened set too."""
    assert _flistPerStepSyncBlockers(
        _fdictWorkflowAround(_fdictDeclarationStep()),
        {"listDiverged": []}, "not-in-github-mirror", "hint",
    ) == []
