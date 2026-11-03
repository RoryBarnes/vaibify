"""``vaibify bindings``: what a workflow's tokens may act on, and how to confirm it.

Read-only. It asks the hub the question the dashboard's binding notices
ask, with evidence, and prints, for every entry that applies or is
already confirmed (every entry with ``--all``): what ``project.json``
names, what you confirmed, what the host can see with your own token,
and the exact ``vaibify do confirm-remote-binding`` command that
confirms what it showed. Nothing is confirmed here; the confirmation is
yours to run, and the hub refuses it if the workflow or its target has
moved since.
"""

__all__ = ["fnBindingsCommand", "fsConfirmCommandLine", "fsRenderBindings"]

import json
import shlex
import sys

import click

from . import hubSession

_T_TARGET_FIELDS = ("sInstance", "sConceptDoi", "sDepositionId", "sProjectId")


def fsConfirmCommandLine(dictReview, sProjectName, sWorkflowPath=""):
    """Return the shell line that confirms exactly the review body ``dictReview``."""
    listWords = ["vaibify", "do", "confirm-remote-binding", "-p", sProjectName]
    if sWorkflowPath:
        listWords += ["--workflow", sWorkflowPath]
    listWords += [
        f"sService={dictReview['sService']}",
        f"sRecordKind={dictReview['sRecordKind']}",
        f"sWorkflowKey={dictReview['sWorkflowKey']}",
        "dictTarget=" + json.dumps(dictReview["dictTarget"], sort_keys=True),
    ]
    return " ".join(shlex.quote(sWord) for sWord in listWords)


def _fsDescribeTarget(dictTarget):
    if not dictTarget:
        return "nothing"
    listParts = [
        f"{sField[1:]} {dictTarget[sField]}" for sField in _T_TARGET_FIELDS
        if dictTarget.get(sField)
    ]
    return ", ".join(listParts) or "nothing"


def _fsDescribeEvidence(dictEvidence):
    if not dictEvidence:
        return "not fetched"
    if not dictEvidence.get("bSucceeded"):
        return f"couldn't check: {dictEvidence.get('sMessage', '')}"
    if dictEvidence.get("sState") == "readable":
        return (
            f"your Overleaf token can read this project "
            f"({dictEvidence['iFileCount']} files, latest commit "
            f"{dictEvidence['sHeadSha'][:7]})"
        )
    if dictEvidence.get("sTitle") or dictEvidence.get("sOwner"):
        return f"Zenodo reports {dictEvidence.get('sTitle')!r}, owner {dictEvidence.get('sOwner')}"
    return "Zenodo accepts your token for this instance"


def _flistRenderEntry(dictEntry, sProjectName, sWorkflowPath):
    sConfirmed = _fsDescribeTarget((dictEntry["dictConfirmed"] or {}).get("dictTarget"))
    return [
        "",
        dictEntry["sLabel"][0].upper() + dictEntry["sLabel"][1:],
        f"  project.json names: {_fsDescribeTarget(dictEntry['dictProjectTarget'])}",
        f"  you confirmed:      {sConfirmed}",
        f"  evidence:           {_fsDescribeEvidence(dictEntry.get('dictEvidence'))}",
        "  to confirm what is shown here:",
        "    " + fsConfirmCommandLine(dictEntry["dictReview"], sProjectName, sWorkflowPath),
    ]


def fsRenderBindings(dictDescribed, sProjectName, sWorkflowPath="", bAll=False):
    """Return the human-readable review of one workflow's bindings."""
    listLines = [
        f"Workflow {dictDescribed['sWorkflowName']!r} "
        f"(binding key {dictDescribed['sWorkflowKey']})",
    ]
    dictRecordState = dictDescribed["dictRecordState"]
    if not dictRecordState["bValid"]:
        return "\n".join(listLines + ["", dictRecordState["sMessage"]])
    for dictEntry in dictDescribed["listEntries"]:
        if bAll or dictEntry["bApplies"] or dictEntry["dictConfirmed"]:
            listLines.extend(_flistRenderEntry(dictEntry, sProjectName, sWorkflowPath))
    return "\n".join(listLines)


def _ftFetchBindings(sContainerName, iPort, sWorkflowPath):
    """Return ``(dictDescribed, sWorkflowPath)`` from the hub, releasing the claim."""
    dictSession = hubSession.fdictOpenResearcherSession(sContainerName, iPort, sWorkflowPath)
    try:
        tResponse = hubSession.ftSendSessionRequest(
            dictSession, "GET", f"/api/bindings/{dictSession['sContainerId']}",
            {"bFetchEvidence": "true"},
        )
    finally:
        hubSession.fnReleaseContainer(dictSession)
    iStatusCode, jsonBody = tResponse
    if not 200 <= iStatusCode < 300:
        sDetail = jsonBody.get("detail", jsonBody) if isinstance(jsonBody, dict) else jsonBody
        raise hubSession.HubSessionError(
            f"reading the bindings failed (HTTP {iStatusCode}): {sDetail}"
        )
    return jsonBody, dictSession["sWorkflowPath"]


@click.command("bindings")
@click.option("--project", "-p", "sProjectName", default=None,
              help="Project name (omit inside a project directory).")
@click.option("--workflow", "sWorkflowPath", default=None,
              help="Container path of the project.json to review.")
@click.option("--all", "bAll", is_flag=True, default=False,
              help="List every entry, not only those that apply or are confirmed.")
@click.option("--port", "iPort", default=None, type=int,
              help="Port of the vaibify session to ask.")
@click.option("--json", "bJson", is_flag=True, default=False,
              help="Print the hub's answer as JSON.")
def fnBindingsCommand(sProjectName, sWorkflowPath, bAll, iPort, bJson):
    """Show what this workflow's tokens may act on, and the command that confirms it."""
    from .configLoader import fconfigResolveProject
    sContainerName = fconfigResolveProject(sProjectName).sProjectName
    try:
        dictDescribed, sConnectedPath = _ftFetchBindings(
            sContainerName, iPort, sWorkflowPath,
        )
    except hubSession.HubSessionError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(4)
    if bJson:
        click.echo(json.dumps(dictDescribed, indent=2, sort_keys=True))
        return
    click.echo(fsRenderBindings(dictDescribed, sContainerName, sConnectedPath, bAll))
