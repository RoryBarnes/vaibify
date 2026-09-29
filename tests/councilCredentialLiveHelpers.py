"""Shared fixtures for the live credential-test lane, and its child hub.

A real module rather than a script held in a string: the child hub the
kill-mid-test lane starts runs on the HOST and imports the lane's own
runtime builder, and ``testNoContainerSideScriptImportsAHostTestModule``
rightly refuses any string that imports from ``tests`` (such strings
usually run inside a container, where no ``tests`` package exists).

Run as ``python -m tests.councilCredentialLiveHelpers <evidencePath>
<stagingDirectory> <imageIdentity>``, it starts one credential test
whose first turn hangs, prints the job, and sleeps until killed.
"""

import io
import json
import os
import sys
import tarfile
import time

S_RUNNER_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
S_RESOURCE_NAME = "credentialLiveProject"

S_FAKE_CLI = r'''
import json, sys, time
saArgv = sys.argv[1:]
sModel = saArgv[saArgv.index("--model") + 1]
sInstruction = saArgv[saArgv.index("--append-system-prompt") + 1]
sys.stdin.buffer.read()
print(json.dumps({"type": "system", "subtype": "init", "model": sModel}),
      flush=True)
if "interrupted" in sInstruction or "SLOW" in sModel:
    time.sleep(600)
if "no-such-model" in sModel:
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True,
                      "result": "invalid model: " + sModel}), flush=True)
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "OK", "usage": {"input_tokens": 1,
                                            "output_tokens": 1}}),
      flush=True)
'''


def fbaBuildFakeCliSnapshot():
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        baScript = S_FAKE_CLI.encode("utf-8")
        infoScript = tarfile.TarInfo(name="fakeCli.py")
        infoScript.size = len(baScript)
        infoScript.mode = 0o644
        fileTar.addfile(infoScript, io.BytesIO(baScript))
    return bufferTar.getvalue()


class HostLoginDocker:
    """The project login, served from the host; never under the lock."""

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        return json.dumps({"claudeAiOauth": {
            "accessToken": "sk-ant-oat01-LIVE-LANE-NOT-A-TOKEN",
            "scopes": ["user:inference"],
            "expiresAt": 4102444800000}}).encode("utf-8")


def fdictBuildLiveRuntime(sImageIdentity, fKillAfterSeconds=6.0):
    """A production runtime with the fake CLI and no egress."""
    from vaibify.gui import (
        agentCouncilCredentialTest, agentCouncilDockerGateway,
        agentCouncilRegistry, agentCouncilRunner)
    dictRuntime = agentCouncilCredentialTest.fdictBuildJobRuntime(
        HostLoginDocker(), agentCouncilDockerGateway.fdockerCreateCouncilClient(),
        agentCouncilRegistry.fdictCreateCouncilRegistry(), S_RESOURCE_NAME,
        "projectcontainerid")
    dictRuntime.update({
        "baSnapshotTar": fbaBuildFakeCliSnapshot(),
        "saCliProgram": ["python3",
                         agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT
                         + "/fakeCli.py"],
        "fdictProvisionEgress": lambda dictJob, dictRuntime: {},
        "fsReadCliVersion": lambda dictJob, dictRuntime: "fake-cli 1.0",
        "fTurnTimeoutSeconds": 60.0,
        "fKillAfterSeconds": fKillAfterSeconds,
    })
    return dictRuntime




def main():
    """Be the hub that dies mid-test: start a hanging job, then wait."""
    from vaibify.config import secretManager
    from vaibify.gui import (
        agentCouncilCredentialGate, agentCouncilCredentialTest)
    sEvidencePath, sStagingDirectory, sImageIdentity = sys.argv[1:4]
    agentCouncilCredentialGate.fsResolveCredentialEvidencePath = (
        lambda: sEvidencePath)
    secretManager._fsGetTempDirectory = lambda: sStagingDirectory
    dictStarted = agentCouncilCredentialTest.fdictStartCredentialTest(
        {}, "claude", sImageIdentity, S_RESOURCE_NAME, "SLOW-haiku",
        fdictBuildLiveRuntime(sImageIdentity, fKillAfterSeconds=600.0))
    print(json.dumps(dictStarted), flush=True)
    time.sleep(600)


if __name__ == "__main__":
    main()
