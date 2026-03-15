"""Execute pipeline scenes by running commands directly in containers."""

import json
import re


PATTERN_SCENE_LABEL = re.compile(
    r"\[Scene(\d+)\]|Scene(\d+):|=+\s*\n\s*Scene(\d+)"
)
PATTERN_SCENE_SUCCESS = re.compile(r"SUCCESS:\s*Scene(\d+)")
PATTERN_SCENE_FAILED = re.compile(r"FAILED:\s*Scene(\d+)")


async def _fnRunSetupIfNeeded(
    connectionDocker, sContainerId, dictScene,
    sSceneDirectory, fnStatusCallback,
):
    """Run setup commands unless bPlotOnly is True."""
    if dictScene.get("bPlotOnly", True):
        return 0
    return await _fnRunCommandList(
        connectionDocker, sContainerId,
        dictScene.get("saSetupCommands", []),
        sSceneDirectory, fnStatusCallback,
    )


async def fnRunSceneCommands(
    connectionDocker, sContainerId, dictScene,
    sWorkdir, fnStatusCallback,
):
    """Run a single scene's commands sequentially in its directory."""
    sSceneDirectory = dictScene.get("sDirectory", sWorkdir)
    iExitCode = await _fnRunSetupIfNeeded(
        connectionDocker, sContainerId, dictScene,
        sSceneDirectory, fnStatusCallback,
    )
    if iExitCode != 0:
        return iExitCode
    return await _fnRunCommandList(
        connectionDocker, sContainerId,
        dictScene.get("saCommands", []),
        sSceneDirectory, fnStatusCallback,
    )


async def _fnRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, fnStatusCallback,
):
    """Execute a list of commands, returning first non-zero exit code."""
    for sCommand in listCommands:
        iExitCode = await _fnRunSingleCommand(
            connectionDocker, sContainerId,
            sCommand, sWorkdir, fnStatusCallback,
        )
        if iExitCode != 0:
            return iExitCode
    return 0


async def _fnRunSingleCommand(
    connectionDocker, sContainerId,
    sCommand, sWorkdir, fnStatusCallback,
):
    """Execute one command and stream its output lines."""
    await fnStatusCallback(
        {"sType": "output", "sLine": f"$ {sCommand}"}
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand, sWorkdir=sWorkdir
    )
    for sLine in sOutput.splitlines():
        await fnStatusCallback({"sType": "output", "sLine": sLine})
    return iExitCode


async def _fnEmitSceneResult(fnStatusCallback, iSceneNumber, iExitCode):
    """Send a scenePass or sceneFail event based on exit code."""
    sType = "scenePass" if iExitCode == 0 else "sceneFail"
    await fnStatusCallback(
        {"sType": sType, "iSceneNumber": iSceneNumber}
    )


async def _fnEmitCompletion(fnStatusCallback, iExitCode):
    """Send the final completed or failed event."""
    sResultType = "completed" if iExitCode == 0 else "failed"
    await fnStatusCallback(
        {"sType": sResultType, "iExitCode": iExitCode}
    )


async def _fdictLoadScript(connectionDocker, sContainerId, fnStatusCallback):
    """Load script.json from the container, returning None on failure."""
    from . import sceneManager

    listPaths = sceneManager.flistFindScriptsInContainer(
        connectionDocker, sContainerId
    )
    if not listPaths:
        await fnStatusCallback(
            {"sType": "error", "sMessage": "No script.json found"}
        )
        return None
    return sceneManager.fdictLoadScriptFromContainer(
        connectionDocker, sContainerId, listPaths[0]
    )


async def fnRunAllScenes(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
):
    """Run all enabled scenes from the cached script."""
    dictScript = await _fdictLoadScript(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictScript is None:
        return 1
    await fnStatusCallback({"sType": "started", "sCommand": "runAll"})
    iResult = await _fnRunSceneList(
        connectionDocker, sContainerId,
        dictScript, sWorkdir, fnStatusCallback,
    )
    await _fnEmitCompletion(fnStatusCallback, iResult)
    return iResult


def _fbShouldRunScene(dictScene, iSceneNumber, iStartScene):
    """Return True if this scene should be executed."""
    if iSceneNumber < iStartScene:
        return False
    return dictScene.get("bEnabled", True)


async def _fnRunOneScene(
    connectionDocker, sContainerId, dictScene,
    iSceneNumber, sWorkdir, fnStatusCallback,
):
    """Run a single scene and emit its result event."""
    iExitCode = await fnRunSceneCommands(
        connectionDocker, sContainerId,
        dictScene, sWorkdir, fnStatusCallback,
    )
    await _fnEmitSceneResult(fnStatusCallback, iSceneNumber, iExitCode)
    return iExitCode


async def _fnRunSceneList(
    connectionDocker, sContainerId,
    dictScript, sWorkdir, fnStatusCallback,
    iStartScene=1,
):
    """Iterate scenes and run each eligible one from iStartScene."""
    iFinalExitCode = 0
    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        iSceneNumber = iIndex + 1
        if not _fbShouldRunScene(dictScene, iSceneNumber, iStartScene):
            continue
        iExitCode = await _fnRunOneScene(
            connectionDocker, sContainerId, dictScene,
            iSceneNumber, sWorkdir, fnStatusCallback,
        )
        if iExitCode != 0:
            iFinalExitCode = iExitCode
    return iFinalExitCode


async def fnRunFromScene(
    connectionDocker, sContainerId, iStartScene,
    sWorkdir, fnStatusCallback,
):
    """Run scenes starting from iStartScene (1-based)."""
    dictScript = await _fdictLoadScript(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictScript is None:
        return 1
    await fnStatusCallback(
        {"sType": "started", "sCommand": f"runFrom:{iStartScene}"}
    )
    iFinalExitCode = await _fnRunSceneList(
        connectionDocker, sContainerId,
        dictScript, sWorkdir, fnStatusCallback,
        iStartScene=iStartScene,
    )
    await _fnEmitCompletion(fnStatusCallback, iFinalExitCode)
    return iFinalExitCode


async def _fbVerifySceneOutputs(
    connectionDocker, sContainerId,
    dictScene, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a scene exist."""
    sSceneDirectory = dictScene.get("sDirectory", sWorkdir)
    for sOutputFile in dictScene.get("saOutputFiles", []):
        sCheckCommand = f"test -f {sOutputFile}"
        iExitCode, _ = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCheckCommand, sWorkdir=sSceneDirectory
        )
        if iExitCode != 0:
            await fnStatusCallback(
                {"sType": "output", "sLine": f"Missing: {sOutputFile}"}
            )
            return False
    return True


async def _fnVerifySceneList(
    connectionDocker, sContainerId, dictScript,
    sWorkdir, fnStatusCallback,
):
    """Verify outputs for every scene, returning True if all present."""
    bAllPresent = True
    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        bSceneOk = await _fbVerifySceneOutputs(
            connectionDocker, sContainerId,
            dictScene, sWorkdir, fnStatusCallback,
        )
        await _fnEmitSceneResult(
            fnStatusCallback, iIndex + 1, 0 if bSceneOk else 1
        )
        if not bSceneOk:
            bAllPresent = False
    return bAllPresent


async def fnVerifyOnly(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
):
    """Check that each scene's output files exist without running."""
    dictScript = await _fdictLoadScript(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictScript is None:
        return 1
    await fnStatusCallback(
        {"sType": "started", "sCommand": "verify"}
    )
    bAllPresent = await _fnVerifySceneList(
        connectionDocker, sContainerId, dictScript,
        sWorkdir, fnStatusCallback,
    )
    iExitCode = 0 if bAllPresent else 1
    await _fnEmitCompletion(fnStatusCallback, iExitCode)
    return iExitCode


def _fnToggleSelectedScenes(dictScript, listSceneIndices):
    """Set bEnabled only for scenes whose indices are in the list."""
    setSelected = set(listSceneIndices)
    for iIndex in range(len(dictScript["listScenes"])):
        dictScript["listScenes"][iIndex]["bEnabled"] = (
            iIndex in setSelected
        )


async def _fnExecuteSelectedScenes(
    connectionDocker, sContainerId, listSceneIndices,
    dictScript, sScriptPath, sWorkdir, fnStatusCallback,
):
    """Toggle scenes, save, run, and emit completion."""
    from . import sceneManager

    _fnToggleSelectedScenes(dictScript, listSceneIndices)
    sceneManager.fnSaveScriptToContainer(
        connectionDocker, sContainerId, dictScript, sScriptPath,
    )
    await fnStatusCallback(
        {"sType": "started", "sCommand": "runSelected"}
    )
    iResult = await _fnRunSceneList(
        connectionDocker, sContainerId,
        dictScript, sWorkdir, fnStatusCallback,
    )
    await _fnEmitCompletion(fnStatusCallback, iResult)
    return iResult


async def fnRunSelectedScenes(
    connectionDocker, sContainerId, listSceneIndices,
    dictScript, sScriptPath, sWorkdir, fnStatusCallback,
):
    """Run only selected scenes by toggling bEnabled."""
    from . import sceneManager

    dictBackup = json.loads(json.dumps(dictScript))
    try:
        iResult = await _fnExecuteSelectedScenes(
            connectionDocker, sContainerId, listSceneIndices,
            dictScript, sScriptPath, sWorkdir, fnStatusCallback,
        )
    finally:
        sceneManager.fnSaveScriptToContainer(
            connectionDocker, sContainerId, dictBackup, sScriptPath,
        )
    return iResult
