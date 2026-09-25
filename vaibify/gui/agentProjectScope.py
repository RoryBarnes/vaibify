"""Which project an in-container agent's action is aimed at.

A container can host several projects, and the hub serves one of them
at a time: every agent action acts on the project open in the
dashboard. An agent working in another project's directory therefore
had its edits land in the open project -- a step inserted into the
wrong ``project.json``, whose only error named the step twice and the
project not at all (researcher-reported, 2026-09-22).

``vaibify-do`` declares the project directory it was run from, and the
hub refuses a declared project that is not the open one, before the
action runs. An UNDECLARED project -- an agent outside every project
directory, or a ``vaibify-do`` baked into an image older than the
declaration -- is served as before, because refusing it would strand
every existing container until a rebuild. Either way, every agent
response names the project it was served against, so a mismatch
cannot pass unseen.

A project's RUNS are the exception, because a container may run
several projects at once. ``vaibify-do`` declares its project when it
opens the pipeline socket, and a socket declaring a project other than
the open one binds to that project's own workflow, read from disk, so
the agent runs its own pipeline whichever project the dashboard shows.
Reading that run's state follows it
(:data:`SET_ROUTES_SERVED_IN_THE_AGENTS_PROJECT`; stopping a run is a
user-only action on every project), resolved only through records the
hub made itself -- the declared directory is a lookup key,
never a path the hub opens. Everything that CHANGES a project's
definition still requires it to be open, where the researcher sees it.

A project's directory is the one holding ``.vaibify/projects/``,
derived from the workflow path by the same rule ``vaibify-do`` walks
up the tree with; the git work-tree root is not used, because a repo
may nest its project one level down and the two would then disagree.
"""

import posixpath
import urllib.parse

from . import workflowManager

__all__ = [
    "S_AGENT_PROJECT_HEADER",
    "S_SERVED_PROJECT_HEADER",
    "S_SERVED_PROJECT_NAME_HEADER",
    "S_AGENT_PROJECT_FIELD",
    "S_AGENT_PROJECT_QUERY",
    "SET_ROUTES_SERVED_IN_THE_AGENTS_PROJECT",
    "fbServesAnotherProject",
    "fsAgentProjectBesideTheOpenOne",
    "fsResolveAgentProjectWorkflowPath",
    "S_REFUSAL_PROJECT_MISMATCH",
    "fsProjectDirectoryOfWorkflow",
    "fbAgentProjectDiffers",
    "fdictBuildMismatchRefusal",
    "fdictBuildMisdirectedRunRefusal",
    "fdictServedProjectForContainer",
    "fdictServedProjectHeaders",
]

S_AGENT_PROJECT_HEADER = "X-Vaibify-Agent-Project"
S_SERVED_PROJECT_HEADER = "X-Vaibify-Project"
S_SERVED_PROJECT_NAME_HEADER = "X-Vaibify-Project-Name"
S_AGENT_PROJECT_FIELD = "sAgentProjectDirectory"
S_AGENT_PROJECT_QUERY = "sAgentProject"

SET_ROUTES_SERVED_IN_THE_AGENTS_PROJECT = frozenset({
    ("GET", "/api/pipeline/{sContainerId}/state"),
})
S_REFUSAL_PROJECT_MISMATCH = "project-mismatch"


def fsProjectDirectoryOfWorkflow(sWorkflowPath):
    """Return the directory holding a workflow's ``.vaibify/projects/``."""
    return workflowManager.fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath)


def fbAgentProjectDiffers(sAgentProjectDirectory, sOpenWorkflowPath):
    """Return True when the agent declared a project other than the open one.

    False when nothing can be compared: no declaration, no open project
    (the route's own ``no-project-open`` refusal answers that), or an
    open workflow whose path names no project directory.
    """
    sOpenDirectory = fsProjectDirectoryOfWorkflow(sOpenWorkflowPath)
    if not sAgentProjectDirectory or not sOpenDirectory:
        return False
    return (
        posixpath.normpath(sAgentProjectDirectory)
        != posixpath.normpath(sOpenDirectory)
    )


def fdictBuildMismatchRefusal(
    sAgentProjectDirectory, sOpenWorkflowPath, sOpenProjectName,
):
    """Return the refusal an agent reads when it addressed the wrong project.

    It names both projects and both remedies, because either may be the
    right one: the researcher opens the agent's project, or the agent
    meant the open project and should work from its directory.
    """
    sOpenDirectory = fsProjectDirectoryOfWorkflow(sOpenWorkflowPath)
    return {
        "sRefusal": S_REFUSAL_PROJECT_MISMATCH,
        "sAgentProjectDirectory": sAgentProjectDirectory,
        "sOpenProjectDirectory": sOpenDirectory,
        "sOpenProjectName": sOpenProjectName,
        "sMessage": (
            f"Refused: you are working in {sAgentProjectDirectory}, but "
            f"the project open in the dashboard is '{sOpenProjectName}' "
            f"({sOpenDirectory}). This action would have acted on that "
            "project instead of yours, so nothing was done. Ask the "
            "researcher to open your project in the dashboard, or work "
            f"from {sOpenDirectory} if that is the project you meant."
        ),
    }


def fdictBuildMisdirectedRunRefusal(
    sAction, dictRequest, dictWorkflowBound, sOpenWorkflowPath,
):
    """Return a runRefused event when a run frame names another project, else None.

    The pipeline socket's twin of the HTTP refusal: runs never pass the
    HTTP middleware, and a run frame declaring a project other than the
    open one would run the open project's steps. A frame declaring no
    project -- the dashboard's, or an older ``vaibify-do``'s -- is
    served.
    """
    sAgentProjectDirectory = dictRequest.get(S_AGENT_PROJECT_FIELD, "")
    if not fbAgentProjectDiffers(sAgentProjectDirectory, sOpenWorkflowPath):
        return None
    return {
        **fdictBuildMismatchRefusal(
            sAgentProjectDirectory, sOpenWorkflowPath,
            (dictWorkflowBound or {}).get("sWorkflowName", ""),
        ),
        "sType": "runRefused",
        "sReason": "projectMismatch",
        "sAction": sAction,
        "listStepIndices": dictRequest.get("listStepIndices", []),
    }


def fdictServedProjectForContainer(dictCtx, sContainerId):
    """Return ``{sWorkflowPath, sProjectName}`` of the open project, or {}."""
    if dictCtx is None or not sContainerId:
        return {}
    sWorkflowPath = (dictCtx.get("paths") or {}).get(sContainerId, "")
    dictWorkflow = (dictCtx.get("workflows") or {}).get(sContainerId)
    if not sWorkflowPath or not dictWorkflow:
        return {}
    return {
        "sWorkflowPath": sWorkflowPath,
        "sProjectName": dictWorkflow.get("sWorkflowName", ""),
    }


def fdictServedProjectHeaders(dictServedProject):
    """Return the response headers naming the project a request was served against.

    Percent-encoded, because a header is Latin-1 and a project name is
    whatever the researcher typed.
    """
    if not dictServedProject:
        return {}
    return {
        S_SERVED_PROJECT_HEADER: urllib.parse.quote(
            fsProjectDirectoryOfWorkflow(dictServedProject["sWorkflowPath"]),
        ),
        S_SERVED_PROJECT_NAME_HEADER: urllib.parse.quote(
            dictServedProject["sProjectName"],
        ),
    }


def fbServesAnotherProject(sAgentProjectDirectory, sOpenWorkflowPath):
    """Return True when a declared project should be served as its own.

    True when the agent declared a project and it is not the open one
    -- including when no project is open at all.
    """
    if not sAgentProjectDirectory:
        return False
    if not sOpenWorkflowPath:
        return True
    return fbAgentProjectDiffers(sAgentProjectDirectory, sOpenWorkflowPath)


def fsAgentProjectBesideTheOpenOne(dictHeaders, dictCtx, sContainerId):
    """Return the request's declared project when it is not the open one, else "".

    Normalized for use as a lookup key into the hub's own run records;
    never opened as a path.
    """
    sDeclared = (dictHeaders or {}).get(S_AGENT_PROJECT_HEADER, "")
    sOpenPath = ((dictCtx or {}).get("paths") or {}).get(sContainerId, "")
    if not fbServesAnotherProject(sDeclared, sOpenPath):
        return ""
    return posixpath.normpath(sDeclared)


def fsResolveAgentProjectWorkflowPath(
    connectionDocker, sContainerId, sAgentProjectDirectory, sSearchRoot,
):
    """Return the one workflow file of the declared project, or raise ValueError.

    Found by discovery, never by composing a path from the declaration:
    only a file discovery lists can be bound. A project holding several
    workflows is refused with their names, because which one the agent
    means is the researcher's to say by opening it.
    """
    sDeclared = posixpath.normpath(sAgentProjectDirectory)
    listPaths = sorted(
        dictFound["sPath"] for dictFound in (
            workflowManager.flistFindWorkflowsInContainer(
                connectionDocker, sContainerId, sSearchRoot,
            )
        )
        if posixpath.normpath(
            fsProjectDirectoryOfWorkflow(dictFound["sPath"]) or "/",
        ) == sDeclared
    )
    if len(listPaths) == 1:
        return listPaths[0]
    if not listPaths:
        raise ValueError(
            f"No vaibify project was found at {sDeclared} in this "
            "container, so there is nothing to run there. Work from a "
            "project directory, or ask the researcher to open yours."
        )
    sNames = ", ".join(posixpath.basename(sPath) for sPath in listPaths)
    raise ValueError(
        f"{sDeclared} holds {len(listPaths)} workflows ({sNames}); ask "
        "the researcher to open the one you mean in the dashboard."
    )
