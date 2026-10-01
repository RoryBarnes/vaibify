"""Read and record how a project may run an image vaibify did not build.

The answer is the researcher's alone, so the recording route is absent
from the agent catalog (``SET_INTENTIONALLY_EXCLUDED_PATHS``): the
in-container agent must never grant itself the trust the image it runs
in would then enjoy. Reading the prompt is harmless and stays open.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel

from vaibify.config import imageTrust
from vaibify.config.registryManager import fdictGetProject, fnRecordImageTrust
from vaibify.docker.containerManager import fdictBuildImageTrustPromptForProject


class ImageTrustRequest(BaseModel):
    """The researcher's answer for one image digest."""

    sImageDigest: str
    sChoice: str
    bWithCredentials: bool = False


def _fnRequireRegisteredProject(sName):
    """Raise 404 unless the registry knows the project."""
    if fdictGetProject(sName) is None:
        raise HTTPException(404, f"Project '{sName}' is not registered")


async def _fdictRequireCurrentPrompt(sName):
    """Return the prompt for the image now under the project's tag, or 409."""
    dictPrompt = await asyncio.to_thread(
        fdictBuildImageTrustPromptForProject, sName)
    if dictPrompt is None:
        raise HTTPException(409, (
            f"vaibify could not read the image for '{sName}'. Check that "
            "Docker is running and that the image exists locally."))
    return dictPrompt


def _fnRequireAnswerableRequest(dictPrompt, request):
    """Raise 400/409 unless the answer names the current image validly."""
    try:
        imageTrust.fnRequireValidTrustChoice(request.sChoice)
    except ValueError as error:
        raise HTTPException(400, str(error))
    if dictPrompt["bBuiltByVaibify"]:
        raise HTTPException(409, "vaibify built this image; there is "
                            "nothing to confirm.")
    if request.sImageDigest != dictPrompt["sImageDigest"]:
        raise HTTPException(409, (
            "The image changed since this question was shown. Reload "
            "and choose again for the image that is there now."))


def _fnRegisterReadPrompt(app, dictCtx):
    """Register GET /api/registry/{sName}/image-trust."""

    @app.get("/api/registry/{sName}/image-trust")
    async def fdictReadImageTrustPrompt(sName: str):
        _fnRequireRegisteredProject(sName)
        return await _fdictRequireCurrentPrompt(sName)


def _fnRegisterRecordAnswer(app, dictCtx):
    """Register POST /api/registry/{sName}/image-trust."""

    @app.post("/api/registry/{sName}/image-trust")
    async def fdictRecordImageTrust(sName: str, request: ImageTrustRequest):
        _fnRequireRegisteredProject(sName)
        dictPrompt = await _fdictRequireCurrentPrompt(sName)
        _fnRequireAnswerableRequest(dictPrompt, request)
        dictRecord = imageTrust.fdictBuildTrustRecord(
            request.sImageDigest, request.sChoice, request.bWithCredentials)
        fnRecordImageTrust(sName, dictRecord)
        return {"bSuccess": True, "dictImageTrust": dictRecord}


def fnRegisterAll(app, dictCtx):
    """Register the image-trust routes."""
    _fnRegisterReadPrompt(app, dictCtx)
    _fnRegisterRecordAnswer(app, dictCtx)
