"""The rerun lane with its REAL runner, on real directories.

``rerunVerification.fdictRerunAndVerifyWorkflow`` is what the shadow runs
inside its own admission. Driven here through a container stand-in whose
every command really runs in ``bash`` against a real directory, the whole
lane is exercised as it is in production: vaibify's render of the
assessor written beside the repository, ``prepare`` and ``clear`` run
through the repo-file adapter, the REAL ``pipelineRunner`` naming every
command, exporting each test command's JUnit path and recording every exit
status, a real pytest writing the reports, and ``assess`` graded and then
re-derived on the host. Only Docker itself is absent; the shadow's
creation and destruction are exercised by ``testRerunVerifiesWhatItRan``
and its live legs.
"""

import os
import re
import subprocess
import sys

from vaibify.docker.dockerConnection import DockerConnection, ExecResult
from vaibify.reproducibility import rerunVerification
from vaibify.reproducibility.repoFiles import ContainerRepoFiles
from tests.testRerunVerifiesWhatItRan import LocalShellContainer


S_LANE_CONTAINER_ID = "assessedRerunLane"
_RE_GNU_TIME_INVOCATION = re.compile(r"/usr/bin/time -f '[^']*' ")


def fsWithoutGnuTimeOffLinux(sCommand):
    """Drop the runner's GNU ``time`` invocation where this host's is BSD.

    The runner wraps a container command in ``/usr/bin/time -f``, which
    a Linux image answers with GNU time or with none. This stand-in runs
    the command on the developer's machine instead, and macOS ships a
    BSD ``/usr/bin/time`` with no ``-f``, so every wrapped command would
    exit 1 before it ran. The wrapped ``bash -c`` is what the container
    runs; only the CPU reading is lost, and no test here reads it.
    """
    if sys.platform != "darwin":
        return sCommand
    return _RE_GNU_TIME_INVOCATION.sub("", sCommand)


class LocalRunnerContainer(LocalShellContainer):
    """A container stand-in the real pipeline runner can drive.

    Adds the streamed exec the runner uses for step commands, run for
    real in the requested working directory, and the batched small-file
    read the state machinery uses.
    """

    def ftRunInContainerStreamedWithChunks(
        self, sContainerId, sCommand, fnEmitChunk, sWorkdir=None,
        sUser=None, sLane=None
    ):
        """Run one command in bash from ``sWorkdir``; stream its lines."""
        del sContainerId, sUser
        self.listCommands.append(sCommand)
        dictEnvironment = dict(os.environ)
        dictEnvironment.pop("PYTEST_ADDOPTS", None)
        completed = subprocess.run(
            ["bash", "-c", fsWithoutGnuTimeOffLinux(sCommand)],
            cwd=sWorkdir or None,
            capture_output=True, text=True, env=dictEnvironment,
            timeout=600)
        for sLine in completed.stdout.splitlines():
            fnEmitChunk("stdout", sLine)
        for sLine in completed.stderr.splitlines():
            fnEmitChunk("stderr", sLine)
        return ExecResult(
            iExitCode=completed.returncode, sStdout=completed.stdout,
            sStderr=completed.stderr)

    def fdictFetchSmallFiles(self, sContainerId, listPaths, sLane=None):
        """Return ``{sPath: bytes or None}`` from the real filesystem."""
        del sContainerId
        dictFiles = {}
        for sPath in listPaths:
            try:
                with open(sPath, "rb") as fileIn:
                    dictFiles[sPath] = fileIn.read()
            except OSError:
                dictFiles[sPath] = None
        return dictFiles

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource, iExpectedBytes=None,
        bReplaceAllowed=True, iMode=None, sAuthorizedRoot=None,
        tForbiddenNames=(), bCreateParents=False,
    ):
        """Write real bytes from a stream, as the runner's plugin write does."""
        del sContainerId, iExpectedBytes, bReplaceAllowed, iMode
        del sAuthorizedRoot, tForbiddenNames
        if bCreateParents:
            os.makedirs(os.path.dirname(sFilePath), exist_ok=True)
        with open(sFilePath, "wb") as fileOut:
            fileOut.write(fileSource.read())

    flistContainerPathsExist = DockerConnection.flistContainerPathsExist


def fdictRerunThroughTheLane(sRepoRoot, dictWorkflow, sWorkflowPath=""):
    """Run the lane's core on a real repository; return its outcome.

    ``sWorkflowPath`` defaults to a workflow file under the repository's
    ``.vaibify/workflows``, which is where the runner resolves the
    repository root from.
    """
    sWorkflowPath = sWorkflowPath or os.path.join(
        sRepoRoot, ".vaibify", "workflows", "project.json")
    connectionLocal = LocalRunnerContainer()
    filesRepo = ContainerRepoFiles(
        connectionLocal, S_LANE_CONTAINER_ID, sRepoRoot)
    dictOutcome = rerunVerification.fdictRerunAndVerifyWorkflow(
        connectionLocal, S_LANE_CONTAINER_ID, dictWorkflow, sWorkflowPath,
        filesRepo)
    dictOutcome["listCommandsRun"] = list(connectionLocal.listCommands)
    return dictOutcome
