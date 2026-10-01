"""The durable record of one council credential test job.

A job record is the test's own account of itself, written as each check
starts and ends, so the panel can show progress, a cancel can be
honoured, and a restarted hub can find a job whose hub died and settle
it. It lives beside the credential document under the researcher's
home (owner-only, written atomically) and never holds token material:
it names the provider, the image, the checks and their outcomes — and
never a credential path, which a restart finds by name and age.

The format changes for different reasons than the checks do, and the
restart sweep reads records without running a check, so it is its own
module. It is not a leaf: it imports the credential gate and store, and
the gate imports it back lazily.
"""

import json
import os
import re

from . import agentCouncilCredentialGate
from . import agentCouncilCredentialStore

__all__ = [
    "LIST_CHECKS",
    "SET_CHECK_IDS",
    "S_JOB_RUNNING",
    "S_JOB_RECORD_DIRECTORY",
    "F_TURN_TIMEOUT_SECONDS",
    "fsResolveJobRecordDirectory",
    "fsComposeJobRecordPath",
    "fdictReadJobRecord",
    "fnWriteJobRecord",
    "fdictCreateJobRecord",
    "flistReadRunningJobRecords",
    "flistReadAllJobRecords",
]

# The checks, in the order they run. The ids are what outcome records
# and the job record name; the labels are what the panel shows.
LIST_CHECKS = [
    ("loginPresent", "A copyable login is present in the project"),
    ("trivialTurn", "A copied access token authenticates a real turn"),
    ("originalLogin", "The project's own login is present and unchanged"),
    ("tokenNotRotated", "The project login's token was not rotated"),
    ("stagingCleaned", "The staged copy is gone and the runner destroyed"),
    ("failurePath", "A turn failing before the model is classified"),
    ("runnerKilledMidTurn", "A runner killed mid-turn is swept cleanly"),
]
SET_CHECK_IDS = frozenset(sCheckId for sCheckId, _ in LIST_CHECKS)

S_JOB_RUNNING = "running"
S_JOB_RECORD_DIRECTORY = "credentialTests"

# The per-turn hard timeout (plan section 8), recorded in every job so a
# researcher reading a timeout knows which bound fired.
F_TURN_TIMEOUT_SECONDS = 90.0


def fsResolveJobRecordDirectory():
    """Return the host directory durable job records live in."""
    return os.path.join(
        agentCouncilCredentialGate.fsResolveCredentialStoreDirectory(),
        S_JOB_RECORD_DIRECTORY)


def fsComposeJobRecordPath(sJobId):
    """Return one job record's path; the id is validated as hex."""
    if not re.fullmatch(r"[0-9a-f]{32}", sJobId or ""):
        raise ValueError("a credential-test job id is 32 hex characters")
    return os.path.join(fsResolveJobRecordDirectory(), f"{sJobId}.json")


def fdictReadJobRecord(sJobId):
    """Return a job record, or None when it does not exist or is unreadable."""
    try:
        with open(fsComposeJobRecordPath(sJobId), encoding="utf-8") as fileIn:
            jsonRecord = json.load(fileIn)
    except (OSError, ValueError):
        return None
    return jsonRecord if isinstance(jsonRecord, dict) else None


def fnWriteJobRecord(dictJob):
    """Persist a job record (0600, atomic); never holds token material."""
    os.makedirs(fsResolveJobRecordDirectory(), mode=0o700, exist_ok=True)
    agentCouncilCredentialStore.fnWriteJsonAtomically(
        fsComposeJobRecordPath(dictJob["sJobId"]), dictJob)


def fdictCreateJobRecord(sJobId, sProvider, sImageIdentity, sResourceName,
                         sContainerId, sRequestedModel):
    """Compose a fresh job record in the running state."""
    return {
        "sJobId": sJobId, "sProvider": sProvider,
        "sImageIdentity": sImageIdentity, "sResourceName": sResourceName,
        "sContainerId": sContainerId, "sRequestedModel": sRequestedModel,
        "iHubPid": os.getpid(), "sStatus": S_JOB_RUNNING,
        "sStartedIso": agentCouncilCredentialStore.fsNowIso(),
        "sFinishedIso": "", "sCurrentCheck": "", "sFailedCheck": "",
        "sDetail": "", "iConsentGeneration": 0,
        "listChecks": [{"sCheckId": sCheckId, "sLabel": sLabel,
                        "sStatus": "pending", "sDetail": ""}
                       for sCheckId, sLabel in LIST_CHECKS],
        "sCliVersion": "", "listUnsettledResources": [],
        "fTurnTimeoutSeconds": F_TURN_TIMEOUT_SECONDS,
    }


def flistReadAllJobRecords():
    """Return every readable durable job record, oldest file name first."""
    sDirectory = fsResolveJobRecordDirectory()
    if not os.path.isdir(sDirectory):
        return []
    listRecords = []
    for sName in sorted(os.listdir(sDirectory)):
        if sName.endswith(".json"):
            dictRecord = fdictReadJobRecord(sName[:-len(".json")])
            if dictRecord:
                listRecords.append(dictRecord)
    return listRecords


def flistReadRunningJobRecords():
    """Return every durable job record still marked running."""
    return [dictRecord for dictRecord in flistReadAllJobRecords()
            if dictRecord.get("sStatus") == S_JOB_RUNNING]
