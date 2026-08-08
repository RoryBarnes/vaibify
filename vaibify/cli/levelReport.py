"""Report a project's PROOF level and what blocks the next one.

The dashboard has always been able to answer "what level is this
project at, and what is stopping it?" — the poll computes it on every
tick — but nothing on the host could, so a script had to open a browser
to find out. This module answers the same question from the same gates
(``vaibify.reproducibility.levelGates``), so the CLI and the dashboard
can never disagree about a level.

One honest difference: the dashboard polls file modification times and
therefore also evaluates the ``script-stale`` L1 criterion. Reading the
container once, as the CLI does, has no mtime history, so that criterion
is not evaluated here — exactly as the boolean gate ``fbAtLeastLevel1``
itself behaves when called without a script-status map. The payload says
so in ``listUnevaluatedCriteria`` rather than letting a caller read the
silence as a pass.
"""

import click


LIST_UNEVALUATED_CRITERIA = ["script-stale"]

_DICT_LEVEL_NAMES = {
    0: "Sandbox",
    1: "Self-Consistent",
    2: "Published",
    3: "Reproducible",
}


def ftLoadContainerLevelInputs(connectionDocker, sContainerName):
    """Return ``(dictWorkflow, filesRepo)`` for the container's project.

    Raises ``LookupError`` when the container holds no vaibify project,
    or holds one that is not inside a git project repo — a workflow
    outside a repo has no level, and saying so beats reporting zero.
    """
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer, flistFindWorkflowsInContainer,
    )
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles
    listWorkflows = flistFindWorkflowsInContainer(
        connectionDocker, sContainerName,
    )
    if not listWorkflows:
        raise LookupError("No vaibify project found in the container.")
    dictFound = listWorkflows[0]
    dictWorkflow = fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerName, dictFound["sPath"],
    )
    dictWorkflow["sProjectRepoPath"] = dictFound["sProjectRepoPath"]
    return dictWorkflow, ContainerRepoFiles(
        connectionDocker, sContainerName, dictFound["sProjectRepoPath"],
    )


def _flistBuildStepReports(dictWorkflow, dictStepStates):
    """Return one per-step row carrying its three level cells."""
    from vaibify.gui.pipelineUtils import fsLabelFromStepIndex
    listReports = []
    for iStepIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []) or []
    ):
        dictCells = dictStepStates.get(iStepIndex, {})
        listReports.append({
            "iStepIndex": iStepIndex,
            "sLabel": dictStep.get("sLabel")
            or fsLabelFromStepIndex(dictWorkflow, iStepIndex),
            "sName": dictStep.get("sName", ""),
            "dictLevelStates": dictCells,
        })
    return listReports


def fdictBuildLevelReport(dictWorkflow, filesRepo):
    """Return the PROOF level, the three blocker lists, and per-step cells."""
    from vaibify.reproducibility.levelGates import (
        fdictComputeStepLevelStates, fiProofLevel, flistLevel1Blockers,
        flistLevel2Blockers, flistLevel3Blockers,
    )
    listLevel1Blockers = flistLevel1Blockers(dictWorkflow, {}, filesRepo)
    listLevel2Blockers = flistLevel2Blockers(dictWorkflow, filesRepo)
    listLevel3Blockers = flistLevel3Blockers(dictWorkflow, filesRepo)
    iLevel = fiProofLevel(dictWorkflow, filesRepo)
    return {
        "sWorkflowName": dictWorkflow.get("sWorkflowName", ""),
        "sProjectRepoPath": dictWorkflow.get("sProjectRepoPath", ""),
        "iProofLevel": iLevel,
        "sProofLevelName": _DICT_LEVEL_NAMES.get(iLevel, "unknown"),
        "listLevel1Blockers": listLevel1Blockers,
        "listLevel2Blockers": listLevel2Blockers,
        "listLevel3Blockers": listLevel3Blockers,
        "listSteps": _flistBuildStepReports(
            dictWorkflow,
            fdictComputeStepLevelStates(
                dictWorkflow, listLevel1Blockers,
                listLevel2Blockers, listLevel3Blockers,
            ),
        ),
        "listUnevaluatedCriteria": list(LIST_UNEVALUATED_CRITERIA),
    }


def _fsSummarizeBlocker(dictBlocker):
    """Return one human line for a blocker entry."""
    sScope = dictBlocker.get("sStepLabel") or dictBlocker.get("sScope", "")
    sHint = dictBlocker.get("sRemediationHint", "")
    return "    %-6s %-24s %s" % (
        sScope, dictBlocker.get("sCriterion", ""), sHint,
    )


def fnPrintLevelReport(dictReport):
    """Print the level, then every blocker grouped by level."""
    click.echo(
        "PROOF level: %d (%s) - %s"
        % (
            dictReport["iProofLevel"], dictReport["sProofLevelName"],
            dictReport["sWorkflowName"] or "(unnamed project)",
        )
    )
    for iLevel in (1, 2, 3):
        listBlockers = dictReport["listLevel%dBlockers" % iLevel]
        click.echo(
            "  Level %d blockers: %d" % (iLevel, len(listBlockers))
        )
        for dictBlocker in listBlockers:
            click.echo(_fsSummarizeBlocker(dictBlocker))
    click.echo(
        "  Not evaluated without the dashboard's file-modification "
        "poll: %s" % ", ".join(dictReport["listUnevaluatedCriteria"])
    )
