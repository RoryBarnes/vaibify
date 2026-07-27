"""The one Docker stand-in the browser lane is allowed to use.

Why this exists as a single, fail-closed object rather than another
per-test mock: the suite already carries about twenty hand-rolled Docker
mocks, and the most-copied of them ends with ``return (0, "")`` -- a
permissive catch-all that answers success to any command it does not
recognise. `testDockerConnectionLive.py` records where that leads: the
mocks accepted every attribute access while, against a real daemon,
every container call raised ``URLSchemeUnknown``. A fake that agrees
with whatever the code under test happens to do cannot falsify
anything.

So this adapter has two rules:

1. **Declared contract.** Every command it models is listed in
   ``LIST_MODELLED_COMMANDS``, each with the Lane 2 assertion that
   proves the real container answers the same way. The contract is the
   list, not "whatever Lane 2 happened to exercise" -- live observation
   silently misses conditional paths such as error branches and
   retries, which is exactly where the transport bug lived.
2. **Fail closed.** An unmodelled command raises
   ``UnmodelledContainerCall``. It never returns a default. A journey
   that trips this is telling you the contract is incomplete, which is
   information; a green default would be the absence of information.

The container NAME and ID are kept distinct for the reason recorded in
AGENTS.md: the owner-of-record map is name-keyed while every URL
carries the id, and a ``name == id`` fixture once hid a bug that would
have closed every real session.
"""

import json


S_CONTAINER_ID = "browserlane0container0id0000000000000000000000000000000000000000"
S_CONTAINER_NAME = "browser-lane-project"
S_PROJECT_REPO = "/workspace/browserLaneProject"
S_WORKFLOW_PATH = f"{S_PROJECT_REPO}/.vaibify/workflows/project.json"


DICT_WORKFLOW = {
    "sWorkflowName": "Browser Lane Project",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 2,
    "sProjectRepoPath": S_PROJECT_REPO,
    "listSteps": [
        {
            "sName": "Generate",
            "sDirectory": "Generate",
            "sLabel": "A01",
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python generate.py"],
            "saOutputDataFiles": ["Generate/output.dat"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested", "sUser": "untested",
            },
        },
        {
            "sName": "Analyze",
            "sDirectory": "Analyze",
            "sLabel": "A02",
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": [
                "python analyze.py {A01.saOutputDataFiles}"
            ],
            "saOutputDataFiles": ["Analyze/summary.json"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested", "sUser": "untested",
            },
        },
    ],
}


# Every command this adapter answers, and the Lane 2 assertion that
# proves a real container answers it the same way. A row with no
# sLaneTwoAssertion is a contract hole; the invariant test refuses it.
LIST_MODELLED_COMMANDS = [
    {
        "sMatch": "git rev-parse --show-toplevel",
        "sPurpose": "project-repo detection",
        "sLaneTwoAssertion": "testRealContainerDetectsProjectRepo",
    },
    {
        "sMatch": ".vaibify/workflows",
        "sPurpose": "workflow discovery",
        "sLaneTwoAssertion": "testRealContainerListsWorkflows",
    },
    {
        "sMatch": "pipeline_state",
        "sPurpose": "pipeline-state read (absent on a fresh project)",
        "sLaneTwoAssertion": "testRealContainerHasNoPipelineStateYet",
    },
    {
        "sMatch": "test -d",
        "sPurpose": "directory probe during workflow discovery",
        "sLaneTwoAssertion": "testRealContainerProbesDirectories",
    },
    {
        "sMatch": "cp -f",
        "sPurpose": "state.json backup before an atomic save",
        "sLaneTwoAssertion": "testRealContainerCopiesAndRenamesFiles",
    },
    {
        "sMatch": "mv -f",
        "sPurpose": "atomic state.json rename from its .tmp",
        "sLaneTwoAssertion": "testRealContainerCopiesAndRenamesFiles",
    },
    {
        "sMatch": "printenv CONTAINER_USER",
        "sPurpose": "resolving the unprivileged container user",
        "sLaneTwoAssertion": "testRealContainerReportsItsContainerUser",
    },
    {
        "sMatch": "vaibifyPoll.list",
        "sPurpose": (
            "file-status poll: mtimes for the watched paths plus a "
            "sha256 fingerprint of the workflow file"
        ),
        "sLaneTwoAssertion": "testRealContainerStatsAndFingerprints",
    },
    {
        "sMatch": "python3 -c",
        "sPurpose": (
            "conftest-version scan, marker directory creation, and "
            "marker copy -- all run as python3 reading stdin"
        ),
        "sLaneTwoAssertion": "testRealContainerRunsPython3OverStdin",
    },
]


class UnmodelledContainerCall(RuntimeError):
    """Raised when the fake is asked something its contract omits."""


class FailClosedDockerAdapter:
    """A Docker stand-in that refuses to invent answers."""

    def __init__(self):
        self._dictFiles = {}
        self.listSeenCommands = []
        # Modification times the file-status poll reports, keyed by
        # container path. Mutable on purpose: the stale-state journey
        # ages an upstream artifact by bumping its stamp here, which is
        # what a real edit inside the container would do.
        self.dictFileModifiedTimes = {
            f"{S_PROJECT_REPO}/Generate/output.dat": 1000,
            f"{S_PROJECT_REPO}/Analyze/summary.json": 2000,
        }

    def fnTouchFile(self, sPath, iModifiedTime):
        """Age or freshen one watched path, as an in-container edit would."""
        self.dictFileModifiedTimes[sPath] = iModifiedTime

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": S_CONTAINER_ID[:12],
            "sName": S_CONTAINER_NAME,
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listSeenCommands.append(sCommand)
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, S_PROJECT_REPO + "\n")
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "pipeline_state" in sCommand:
            return (1, "")
        if "test -d" in sCommand:
            return (0, "")
        if "cp -f" in sCommand or "mv -f" in sCommand:
            return (0, "")
        if "printenv CONTAINER_USER" in sCommand:
            return (0, "researcher\n")
        if "vaibifyPoll.list" in sCommand:
            listLines = [
                f"{sPath} {iStamp}" for sPath, iStamp
                in sorted(self.dictFileModifiedTimes.items())
            ]
            listLines.append("fingerprint:" + "0" * 64)
            return (0, "\n".join(listLines) + "\n")
        if "python3 -c" in sCommand:
            # The conftest-version scan parses stdout as JSON; the
            # directory-creation and marker-copy helpers ignore it.
            # An empty object satisfies all three.
            return (0, "{}")
        raise UnmodelledContainerCall(
            "The browser lane's Docker adapter was asked to run a "
            f"command its contract does not model:\n  {sCommand}\n"
            "Add it to LIST_MODELLED_COMMANDS together with the Lane 2 "
            "assertion proving a real container answers the same way. "
            "Do NOT add a default return -- a fake that answers "
            "everything proves nothing."
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(sPath)

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        self._dictFiles[sPath] = baContent

    def fnWriteFileViaTar(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        self._dictFiles[sPath] = baContent

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        from types import SimpleNamespace
        self.listSeenCommands.append(sCommand)
        return SimpleNamespace(iExitCode=0, sStdout="ok", sStderr="")
