"""Hub routes that run a Docker image build and report its progress.

Split from ``registryRoutes`` along a real seam: the registry module
answers "what projects exist and what containers do they own", while
this module owns the long-running build — its execution thread, its
live progress record, and the two endpoints the dashboard uses to
start a build and to watch one. The two concerns change for different
reasons (registry CRUD versus build mechanics), and the build side
carries its own state.

The progress record is in-process only: a hub restart forgets it,
which is honest — the build it described died with the process. The
worker thread owns the record's lifecycle flags, because the POST
that started the build can be cancelled (tab closed) while the
docker build keeps running; only the thread knows when it actually
ended, and the polled GET lets a reopened tab re-attach to the same
build.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import logging
import os
from collections import deque

from fastapi import HTTPException, Request


logger = logging.getLogger(__name__)

_I_BUILD_PROGRESS_TAIL_LINES = 200
_DICT_BUILD_PROGRESS = {}


def fnRegisterAll(app, dictCtx):
    """Register the build, acquire, switch and build-progress routes."""
    _fnRegisterBuildContainer(app, dictCtx)
    _fnRegisterAcquireImage(app, dictCtx)
    _fnRegisterSwitchToBuilding(app, dictCtx)
    _fnRegisterBuildProgress(app, dictCtx)


def _fnRefuseBuildOfObtainedImage(dictProject):
    """409 a plain build of a project whose image is OBTAINED.

    A build would replace ``<projectName>:latest`` with a rebuild of a
    different digest while the registry still said the image was the
    author's, and the origin record would go stale in silence. The way
    back is the explicit switch, which clears the source and the record
    in one locked mutation.
    """
    from vaibify.config.registryManager import fbProjectImageIsObtained
    if not fbProjectImageIsObtained(dictProject):
        return
    raise HTTPException(409, detail={"sMessage": (
        "This project's image is the author's pinned image, obtained "
        "rather than built. To build from the Dockerfile instead, choose "
        "'Switch to building from the Dockerfile' on the Rebuild menu; "
        "to obtain the pinned image again, choose 'Re-obtain the pinned "
        "image'."
    ), "sAction": "switch-to-building"})


def _fdictOpenBuildProgress(sName):
    """Create and register the live progress record for one build."""
    dictProgress = {
        "bLive": True,
        "dequeTail": deque(maxlen=_I_BUILD_PROGRESS_TAIL_LINES),
        "iLineCount": 0,
        "sOutcome": "",
    }
    _DICT_BUILD_PROGRESS[sName] = dictProgress
    return dictProgress


def _fnRecordBuildLine(dictProgress, sLine):
    """Append one already-redacted build output line to the record."""
    dictProgress["dequeTail"].append(sLine.rstrip("\n"))
    dictProgress["iLineCount"] += 1


def _fnCloseBuildProgress(dictProgress, sOutcome):
    """Mark the build finished; outcome lands before the live flag drops."""
    dictProgress["sOutcome"] = sOutcome
    dictProgress["bLive"] = False


def _fnRegisterBuildContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/build."""

    @app.post("/api/containers/{sName}/build")
    async def fdictBuildContainer(
        sName: str, bNoCache: bool = False,
    ):
        from vaibify.gui.registryRoutes import _fdictRequireProject
        from vaibify.gui.routeContext import (
            fnRefuseContainerOnlyForHostProject,
        )
        # Before the daemon check, deliberately. On a machine with no
        # Docker at all -- the researcher host mode exists for -- a
        # daemon-first order answers "Docker is unavailable" for a
        # project that never wanted Docker.
        fnRefuseContainerOnlyForHostProject(sName, "Building an image")
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        _fnRefuseBuildOfObtainedImage(dictProject)
        _fnRefuseWhileABuildIsLive(sName)
        dictProgress = _fdictOpenBuildProgress(sName)
        try:
            await asyncio.to_thread(
                _fnExecuteBuild, dictProject, bNoCache, dictProgress,
            )
        except Exception as error:
            sTail = getattr(error, "sStderrTail", "") or ""
            logger.error(
                "Build failed for %s: %s%s",
                sName, error,
                "\nstderr tail:\n" + sTail if sTail else "",
            )
            raise HTTPException(
                500, detail=_fdictBuildFailureDetail(error, sTail),
            )
        return {"bSuccess": True, "sMessage": "Build complete"}


def _fnRefuseWhileABuildIsLive(sName):
    """409 when a build or an acquisition already owns this project's record."""
    dictExisting = _DICT_BUILD_PROGRESS.get(sName)
    if dictExisting is not None and dictExisting["bLive"]:
        raise HTTPException(409, detail={
            "sMessage": (
                "A build for this project is already running."
            ),
        })


S_ACTION_STOP_FIRST = "stop-first"


def _fdictContainerStatusOrNone(dictProject):
    """Ask the daemon whether the project's container exists; ``None`` if it cannot say."""
    from vaibify.docker.containerManager import fdictGetContainerStatus
    try:
        return fdictGetContainerStatus(dictProject["sContainerName"])
    except OSError:
        return None


def _fnRefuseWhileTheContainerExists(dictContainerStatus, sVerb):
    """409 unless the daemon has confirmed the project's container is gone.

    Re-obtaining and switching both rewrite what the project's name
    resolves to -- the tag, the registry entry, the origin record --
    so doing either under a container that still exists leaves the
    old image on screen described as the new one. The frontend stops
    the container first, but a stop it could not complete must not
    let the transition through, so the daemon is asked here and an
    answer that cannot be had refuses too.
    """
    if dictContainerStatus is None:
        raise HTTPException(409, detail={
            "sMessage": (
                f"{sVerb} needs the daemon to confirm the container is "
                "gone, and it could not be asked."
            ),
            "sAction": S_ACTION_STOP_FIRST,
        })
    if dictContainerStatus.get("bExists"):
        raise HTTPException(409, detail={
            "sMessage": (
                f"{sVerb} is refused while the container exists "
                f"({dictContainerStatus.get('sStatus')}). Stop it first; a "
                "stop that failed has to be fixed, not skipped."
            ),
            "sAction": S_ACTION_STOP_FIRST,
        })


def _fnRegisterAcquireImage(app, dictCtx):
    """Register POST /api/containers/{sName}/acquire-image.

    The twin of ``/build`` for a project whose image is the author's
    PINNED one: same progress record, same shape, so the dashboard's
    build modal polls it unchanged. The worker obtains the image
    through the published chain, proves which overlays it holds,
    stacks the additions the wizard asked for, tags the result as the
    project's, describes it in ``vaibify.yml`` and the registry, and
    only then writes the origin record the launch guard admits a start
    on. Emulation is consented twice -- the entry from the wizard and
    the body from the button -- and refused unless both agree.
    """

    @app.post("/api/containers/{sName}/acquire-image")
    async def fdictAcquireImage(
        sName: str, requestHttp: Request, bAllowEmulation: bool = False,
        bWithoutAdditions: bool = False,
    ):
        from vaibify.config.registryManager import fbProjectImageIsObtained
        from vaibify.gui.registryRoutes import _fdictRequireProject
        from vaibify.gui.routeContext import (
            fnRefuseContainerOnlyForHostProject,
            fnRejectAgentTokenLane,
        )
        fnRejectAgentTokenLane(requestHttp)
        fnRefuseContainerOnlyForHostProject(sName, "Obtaining an image")
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        if not fbProjectImageIsObtained(dictProject):
            raise HTTPException(409, detail={"sMessage": (
                "This project builds its image from the Dockerfile; "
                "there is no pinned image to obtain. Use Rebuild."
            )})
        _fnRefuseWhileABuildIsLive(sName)
        _fnRefuseWhileTheContainerExists(
            await asyncio.to_thread(_fdictContainerStatusOrNone, dictProject),
            "Obtaining the pinned image",
        )
        dictProgress = _fdictOpenBuildProgress(sName)
        try:
            dictOrigin = await asyncio.to_thread(
                _fdictExecuteAcquisition, dictProject, bAllowEmulation,
                dictProgress, bWithoutAdditions,
            )
        except Exception as error:
            logger.error("Acquisition failed for %s: %s", sName, error)
            raise HTTPException(
                500, detail=_fdictBuildFailureDetail(error, ""),
            )
        return {
            "bSuccess": True, "sMessage": "Image obtained",
            "dictImageOrigin": dictOrigin,
        }


def _fdictExecuteAcquisition(
    dictProject, bAllowEmulation, dictProgress, bWithoutAdditions=False,
):
    """Run the acquisition lane in a worker thread, feeding the progress record."""
    from vaibify.cli.configLoader import fsDockerDir
    from vaibify.docker.pinnedImageAcquisition import fdictAcquireForProject
    try:
        dictOrigin = fdictAcquireForProject(
            dictProject, bAllowEmulation,
            lambda sLine: _fnRecordBuildLine(dictProgress, sLine),
            sDockerDir=fsDockerDir(), bWithoutAdditions=bWithoutAdditions,
        )
    except BaseException as error:
        _fnRecordBuildLine(dictProgress, f"error: {error}")
        _fnCloseBuildProgress(dictProgress, "failed")
        raise
    _fnCloseBuildProgress(dictProgress, "succeeded")
    return dictOrigin


def _fnRegisterSwitchToBuilding(app, dictCtx):
    """Register POST /api/containers/{sName}/switch-to-building.

    The way back from an obtained image: clears the registry's image
    source AND the origin record in one locked mutation, then the
    frontend runs the ordinary build. Never a silent build over an
    entry that still says the image is the author's.
    """

    @app.post("/api/containers/{sName}/switch-to-building")
    async def fdictSwitchToBuilding(sName: str, requestHttp: Request):
        from vaibify.config.registryManager import (
            fbProjectImageIsObtained,
            fnSwitchProjectToBuilding,
        )
        from vaibify.gui.registryRoutes import _fdictRequireProject
        from vaibify.gui.routeContext import (
            fnRefuseContainerOnlyForHostProject,
            fnRejectAgentTokenLane,
        )
        fnRejectAgentTokenLane(requestHttp)
        fnRefuseContainerOnlyForHostProject(sName, "Switching to building")
        dictProject = _fdictRequireProject(sName)
        if not fbProjectImageIsObtained(dictProject):
            return {"bSwitched": False, "sMessage": (
                "This project already builds its image from the Dockerfile."
            )}
        _fnRefuseWhileABuildIsLive(sName)
        _fnRefuseWhileTheContainerExists(
            await asyncio.to_thread(_fdictContainerStatusOrNone, dictProject),
            "Switching to building",
        )
        await asyncio.to_thread(fnSwitchProjectToBuilding, sName)
        return {
            "bSwitched": True,
            "sBuildPath": f"/api/containers/{sName}/build",
        }


def _fnRegisterBuildProgress(app, dictCtx):
    """Register GET /api/containers/{sName}/build/progress.

    Read-only view of the live (or most recent) build for one project.
    A tab that started a build and was closed can re-open, get a 409
    from a duplicate build click, and re-attach here to watch the same
    build to completion — the docker build outlives the request that
    started it.
    """

    @app.get("/api/containers/{sName}/build/progress")
    async def fdictGetBuildProgress(sName: str):
        dictCtx["require"]()
        dictProgress = _DICT_BUILD_PROGRESS.get(sName)
        if dictProgress is None:
            return {
                "bKnown": False, "bLive": False, "saTailLines": [],
                "iLineCount": 0, "sOutcome": "",
            }
        return {
            "bKnown": True,
            "bLive": dictProgress["bLive"],
            "saTailLines": list(dictProgress["dequeTail"]),
            "iLineCount": dictProgress["iLineCount"],
            "sOutcome": dictProgress["sOutcome"],
        }


def _fnExecuteBuild(dictProject, bNoCache=False, dictProgress=None):
    """Load config and run the Docker image build.

    Runs in a worker thread. When ``dictProgress`` is given, this
    thread routes each redacted docker output line into it and closes
    it on the way out — in this function rather than the route,
    because the route's await can be cancelled by a disconnecting
    client while the build (and this thread) carry on.
    """
    from vaibify.cli.configLoader import (
        fconfigLoadFromPath, fsDockerDir,
    )
    from vaibify.cli.commandBuild import fnBuildFromConfig
    from vaibify.docker.imageBuilder import fnSetThreadBuildLineSink
    try:
        configProject = fconfigLoadFromPath(
            dictProject["sConfigPath"],
        )
        sDockerDir = fsDockerDir()
        if dictProgress is not None:
            fnSetThreadBuildLineSink(
                lambda sLine: _fnRecordBuildLine(dictProgress, sLine)
            )
        try:
            # The hub serves every project from its OWN launch
            # directory, so the build must be told which project it is
            # building; left to resolve for itself it reads the git
            # remote of whatever directory the hub was started in.
            fnBuildFromConfig(
                configProject, sDockerDir, bNoCache=bNoCache,
                sProjectDirectory=os.path.dirname(
                    dictProject["sConfigPath"],
                ),
            )
        finally:
            if dictProgress is not None:
                fnSetThreadBuildLineSink(None)
    except BaseException:
        if dictProgress is not None:
            _fnCloseBuildProgress(dictProgress, "failed")
        raise
    if dictProgress is not None:
        _fnCloseBuildProgress(dictProgress, "succeeded")


def _fdictBuildFailureDetail(error, sStderrTail):
    """Format the FastAPI detail payload for a build failure.

    The tail has already been credential-redacted by imageBuilder's
    ``fsRedactBuildOutputCredentials`` before it lands on the
    exception, so it is safe to surface to the GUI.
    """
    return {
        "sMessage": "Build failed",
        "sError": str(error),
        "sStderrTail": sStderrTail,
        # The one recovery a refusal offers, when it offers one (an
        # acquisition that cannot prove its baseline names the retry
        # without the added agents); empty for every other failure.
        "sAction": str(getattr(error, "sAction", "") or ""),
    }
