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
from collections import deque

from fastapi import HTTPException


logger = logging.getLogger(__name__)

_I_BUILD_PROGRESS_TAIL_LINES = 200
_DICT_BUILD_PROGRESS = {}


def fnRegisterAll(app, dictCtx):
    """Register the build and build-progress routes."""
    _fnRegisterBuildContainer(app, dictCtx)
    _fnRegisterBuildProgress(app, dictCtx)


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
        dictExisting = _DICT_BUILD_PROGRESS.get(sName)
        if dictExisting is not None and dictExisting["bLive"]:
            raise HTTPException(409, detail={
                "sMessage": (
                    "A build for this project is already running."
                ),
            })
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
            fnBuildFromConfig(
                configProject, sDockerDir, bNoCache=bNoCache,
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
    }
