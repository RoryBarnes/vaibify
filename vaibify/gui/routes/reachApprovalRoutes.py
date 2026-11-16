"""Read and record the researcher's approval of what a container may reach.

The approval is the researcher's alone, so the recording route is
absent from the agent catalog (``SET_INTENTIONALLY_EXCLUDED_PATHS``):
an in-container agent approving the reach of the container it runs in
is the inversion of the question. Reading the prompt is harmless and
stays open. Both read :mod:`vaibify.config.reachApproval`, as the
command line does.
"""

__all__ = ["fnRegisterAll"]

from fastapi import HTTPException
from pydantic import BaseModel

from vaibify.config import reachApproval
from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.config.registryManager import (
    S_REACH_APPROVAL_KEY, fdictGetProject, fnRecordReachApproval,
)


class ReachApprovalRequest(BaseModel):
    """The researcher's approval, naming the fingerprint that was shown."""

    sReachFingerprint: str


def _fdictRequireRegisteredProject(sName):
    """Return the registry entry, or raise 404."""
    dictProject = fdictGetProject(sName)
    if dictProject is None:
        raise HTTPException(404, f"Project '{sName}' is not registered")
    return dictProject


def _fconfigRequireLoadable(dictProject):
    """Return the project's configuration as the file is now, or raise 409."""
    try:
        return fconfigLoadFromFile(dictProject["sConfigPath"])
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(409, (
            f"vaibify.yml for '{dictProject.get('sName')}' could not be "
            f"read: {error}"))


def _fdictPromptForProject(dictProject):
    """Return the prompt for the file as it is now."""
    return reachApproval.fdictBuildReachPrompt(
        _fconfigRequireLoadable(dictProject),
        dictProject.get(S_REACH_APPROVAL_KEY), bRegistered=True)


def _fnRegisterReadPrompt(app, dictCtx):
    """Register GET /api/registry/{sName}/reach-approval."""

    @app.get("/api/registry/{sName}/reach-approval")
    async def fdictReadReachPrompt(sName: str):
        return _fdictPromptForProject(_fdictRequireRegisteredProject(sName))


def _fnRegisterRecordApproval(app, dictCtx):
    """Register POST /api/registry/{sName}/reach-approval."""

    @app.post("/api/registry/{sName}/reach-approval")
    async def fdictRecordReachApproval(sName: str, request: ReachApprovalRequest):
        dictProject = _fdictRequireRegisteredProject(sName)
        config = _fconfigRequireLoadable(dictProject)
        if not reachApproval.fbConfigurationWidensReach(config):
            raise HTTPException(409, (
                "vaibify.yml asks for nothing that widens what the container "
                "reaches; there is nothing to approve."))
        if request.sReachFingerprint != reachApproval.fsComputeReachFingerprint(config):
            raise HTTPException(409, (
                "vaibify.yml changed since this question was shown; reload "
                "and read it again."))
        dictRecord = reachApproval.fdictBuildReachApprovalRecord(config)
        fnRecordReachApproval(sName, dictRecord)
        return {"bSuccess": True, "dictReachApproval": dictRecord}


def fnRegisterAll(app, dictCtx):
    """Register the reach-approval routes."""
    _fnRegisterReadPrompt(app, dictCtx)
    _fnRegisterRecordApproval(app, dictCtx)
