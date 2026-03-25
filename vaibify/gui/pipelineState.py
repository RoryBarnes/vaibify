"""Pipeline state persistence for reconnecting to running pipelines.

Writes state to /workspace/.vaibify/pipeline_state.json inside the
container so the GUI can recover pipeline status after a browser
disconnect, tab close, or GUI restart.
"""

import json
from datetime import datetime, timezone

I_MAX_OUTPUT_LINES = 500
S_STATE_PATH = "/workspace/.vaibify/pipeline_state.json"


def fdictBuildInitialState(sAction, sLogPath, iStepCount):
    """Build the initial state dictionary when a pipeline starts."""
    return {
        "bRunning": True,
        "sAction": sAction,
        "sLogPath": sLogPath,
        "sStartTime": datetime.now(timezone.utc).isoformat(),
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": -1,
        "iStepCount": iStepCount,
        "dictStepResults": {},
        "listRecentOutput": [],
    }


def fdictBuildStepStarted(iStepNumber):
    """Return a partial update dict for a step starting."""
    return {"iActiveStep": iStepNumber}


def fdictBuildStepResult(iStepNumber, sStatus, iExitCode=0):
    """Return a result entry for a completed step."""
    return {
        "iStepNumber": iStepNumber,
        "sStatus": sStatus,
        "iExitCode": iExitCode,
    }


def fdictBuildCompletedState(iExitCode):
    """Return a partial update dict for pipeline completion."""
    return {
        "bRunning": False,
        "iActiveStep": -1,
        "iExitCode": iExitCode,
        "sEndTime": datetime.now(timezone.utc).isoformat(),
    }


def fnWriteState(connectionDocker, sContainerId, dictState):
    """Write the full state dict to the container."""
    sContent = json.dumps(dictState, indent=2)
    connectionDocker.fnWriteFile(
        sContainerId, S_STATE_PATH, sContent.encode("utf-8")
    )


def fnUpdateState(connectionDocker, sContainerId, dictState, dictUpdate):
    """Merge dictUpdate into dictState and write to container."""
    dictState.update(dictUpdate)
    fnWriteState(connectionDocker, sContainerId, dictState)


def fnRecordStepResult(
    connectionDocker, sContainerId, dictState, dictResult
):
    """Add a step result and write to container."""
    sKey = str(dictResult["iStepNumber"])
    dictState["dictStepResults"][sKey] = {
        "sStatus": dictResult["sStatus"],
        "iExitCode": dictResult["iExitCode"],
    }
    fnWriteState(connectionDocker, sContainerId, dictState)


def fnAppendOutput(dictState, sLine):
    """Append an output line to the ring buffer."""
    listOutput = dictState["listRecentOutput"]
    listOutput.append(sLine)
    if len(listOutput) > I_MAX_OUTPUT_LINES:
        dictState["listRecentOutput"] = listOutput[-I_MAX_OUTPUT_LINES:]


def fdictReadState(connectionDocker, sContainerId):
    """Read the pipeline state from the container, or None."""
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            f"cat {S_STATE_PATH} 2>/dev/null",
        )
        if iExitCode != 0 or not sOutput.strip():
            return None
        return json.loads(sOutput)
    except (json.JSONDecodeError, OSError):
        return None


def fnClearState(connectionDocker, sContainerId):
    """Remove the pipeline state file."""
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {S_STATE_PATH}"
    )
