"""Fixture builders for the "Reproduce a published project" lane.

Everything here is REAL: real git repositories built with the git on
PATH, and a real loopback HTTP server that git clones from over the
dumb-HTTP protocol. A stubbed git would let a test pass against a
clone command that never ran, which is the class of green-suite
defect this repository has shipped before.

The loopback server exists because the accepted URL shapes are
``https`` and ``ssh`` and neither can be served from a test without
certificates or keys. The tests that need a remote widen the accepted
schemes to include ``http`` for the loopback host only; the clone
command, the hardening flags and the export are then exercised exactly
as production runs them.
"""

import hashlib
import http.server
import json
import os
import subprocess
import threading
from functools import partial


S_FIXTURE_STEP_DIRECTORY = "MakeNumbers"
S_FIXTURE_SCRIPT = "MakeNumbers/generate.py"
S_FIXTURE_OUTPUT = "MakeNumbers/numbers.txt"
S_FIXTURE_WORKFLOW_PATH = ".vaibify/projects/project.json"
S_FIXTURE_IMAGE_DIGEST = "registry.example/demo@sha256:" + "a" * 64
S_FIXTURE_ARCHITECTURE = "amd64"


def fsRunGit(listArguments, sCwd):
    """Run git for real inside a fixture and return its stdout."""
    processGit = subprocess.run(
        ["git", "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=fixture", "-c", "commit.gpgsign=false",
         *listArguments],
        cwd=sCwd, capture_output=True, text=True, check=True,
    )
    return processGit.stdout.strip()


def fdictBuildWorkflow(sWorkflowName="Demo"):
    """Return a minimal valid project.json declaring one script and one output."""
    return {
        "sWorkflowName": sWorkflowName,
        "sPlotDirectory": "figures",
        "listSteps": [{
            "sName": "Make Numbers",
            "sDirectory": S_FIXTURE_STEP_DIRECTORY,
            "saDataCommands": ["python generate.py"],
            "saOutputDataFiles": ["numbers.txt"],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


def fdictBuildEnvelope(dictArchiveRecord=None):
    """Return an environment.json payload pinning a registry digest."""
    dictContainer = {
        "sImageDigest": S_FIXTURE_IMAGE_DIGEST,
        "sArchitecture": S_FIXTURE_ARCHITECTURE,
    }
    if dictArchiveRecord is not None:
        dictContainer["dictImageArchive"] = dictArchiveRecord
    return {"dictContainer": dictContainer}


def fnWriteManifest(sRepoPath, listRelativePaths):
    """Write MANIFEST.sha256 over the named files' current bytes."""
    listLines = ["# SHA-256 manifest of workflow artefacts\n"]
    for sRelative in listRelativePaths:
        with open(os.path.join(sRepoPath, sRelative), "rb") as fileHandle:
            sHash = hashlib.sha256(fileHandle.read()).hexdigest()
        listLines.append(f"{sHash}  {sRelative}\n")
    with open(os.path.join(sRepoPath, "MANIFEST.sha256"), "w") as fileHandle:
        fileHandle.write("".join(listLines))


def fnWriteJson(sRepoPath, sRelative, dictPayload):
    """Write one JSON file inside the fixture repository."""
    sAbsolute = os.path.join(sRepoPath, sRelative)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPayload, fileHandle, indent=2, sort_keys=True)


def fnWriteText(sRepoPath, sRelative, sBody):
    """Write one text file inside the fixture repository."""
    sAbsolute = os.path.join(sRepoPath, sRelative)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(sBody)


def fnCommitEverything(sRepoPath, sMessage="commit"):
    """Stage and commit every change; return the new HEAD commit."""
    fsRunGit(["add", "-A"], sRepoPath)
    fsRunGit(["commit", "--quiet", "--allow-empty", "-m", sMessage], sRepoPath)
    return fsRunGit(["rev-parse", "HEAD"], sRepoPath)


def fsBuildPublishedProject(sRepoPath, dictWorkflow=None, dictEnvelope=None):
    """Build a committed, reproduction-ready project; return its HEAD commit.

    The default branch is pinned to ``main`` by symbolic-ref, because a
    fixture that inherits the machine's ``init.defaultBranch`` passes
    on one laptop and fails on a runner defaulting to ``master``.
    """
    os.makedirs(sRepoPath, exist_ok=True)
    fsRunGit(["init", "--quiet"], sRepoPath)
    fsRunGit(["symbolic-ref", "HEAD", "refs/heads/main"], sRepoPath)
    fnWriteText(sRepoPath, S_FIXTURE_SCRIPT, "print(1)\n")
    fnWriteText(sRepoPath, S_FIXTURE_OUTPUT, "1\n")
    fnWriteJson(
        sRepoPath, S_FIXTURE_WORKFLOW_PATH,
        dictWorkflow if dictWorkflow is not None else fdictBuildWorkflow(),
    )
    fnWriteJson(
        sRepoPath, ".vaibify/environment.json",
        dictEnvelope if dictEnvelope is not None else fdictBuildEnvelope(),
    )
    fnWriteManifest(sRepoPath, [S_FIXTURE_SCRIPT, S_FIXTURE_OUTPUT])
    return fnCommitEverything(sRepoPath, "publish")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serve a bare repository's files without logging every request."""

    def log_message(self, sFormat, *args):
        return


class LoopbackGitServer:
    """A dumb-HTTP git remote on 127.0.0.1, serving one bare repository.

    ``git update-server-info`` is re-run after every push so the
    static ``info/refs`` git reads over dumb HTTP names the new HEAD.
    """

    def __init__(self, sBarePath):
        self.sBarePath = sBarePath
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(_QuietHandler, directory=sBarePath),
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()

    @property
    def sUrl(self):
        """The clone URL of the served repository."""
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    def fnRefreshServerInfo(self):
        """Re-publish info/refs after the bare repository changed."""
        fsRunGit(["update-server-info"], self.sBarePath)


def fnPublishToBare(sSourcePath, sBarePath):
    """Create a bare mirror of a source repository for the loopback server."""
    fsRunGit(["clone", "--quiet", "--bare", sSourcePath, sBarePath],
             os.path.dirname(sBarePath))
    fsRunGit(["update-server-info"], sBarePath)


def fnPushToBare(sSourcePath, sBarePath):
    """Push the source's main branch into the bare mirror."""
    fsRunGit(["push", "--quiet", sBarePath, "main:main"], sSourcePath)
    fsRunGit(["update-server-info"], sBarePath)
