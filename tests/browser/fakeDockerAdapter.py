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
S_WORKSPACE_ROOT = "/workspace"
# Imported from the product rather than re-spelled: a fake that drifted
# from the real marker path would answer the recognition probe for a
# path nothing asks about, and report its own container unrecognized.
from vaibify.gui.registryRoutes import (  # noqa: E402
    S_VAIBIFY_MARKER_DIRECTORY,
)
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
        "sMatch": "mkdir -p",
        "sPurpose": (
            "state-directory bootstrap before a state.json save (a "
            "legacy root-layout repo has no .vaibify directory yet)"
        ),
        "sLaneTwoAssertion": "testRealContainerMakesDirectories",
    },
    {
        "sMatch": "printenv CONTAINER_USER",
        "sPurpose": "resolving the unprivileged container user",
        "sLaneTwoAssertion": "testRealContainerReportsItsContainerUser",
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
        # What the Repos panel's discovery finds under the workspace
        # root: the lane's one project repository.
        self.setWorkspaceRepositories = {
            S_PROJECT_REPO[len(S_WORKSPACE_ROOT) + 1:],
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

    def _ftAnswerDirectoryCreate(self, sCommand):
        """Answer `mkdir -p`, but only for paths inside the workspace.

        Same scoping rule as the directory probe below: a bare verb
        match would answer 0 for a creation anywhere at all, including
        outside the volume. A creation that has wandered surfaces as
        an unmodelled call instead.
        """
        if S_WORKSPACE_ROOT not in sCommand:
            raise UnmodelledContainerCall(
                "Directory creation outside the workspace volume, "
                f"which the lane never legitimately does: {sCommand}"
            )
        return (0, "")

    def _ftAnswerDirectoryProbe(self, sCommand):
        """Answer `test -d`, but only for paths inside the workspace.

        A bare substring match on "test -d" would answer 0 for any
        path at all, including one outside the volume -- which is
        exactly the kind of semantically-wrong-but-green answer a
        permissive mock gives. Scoping it to the workspace means a
        probe that has wandered surfaces as an unmodelled call.
        """
        if S_WORKSPACE_ROOT not in sCommand:
            raise UnmodelledContainerCall(
                "Directory probe outside the workspace volume, which "
                f"this fake does not speak for:\n  {sCommand}"
            )
        return (0, "")

    def _ftAnswerFileMove(self, sCommand):
        """Answer `cp -f`/`mv -f` only for the atomic state-save paths.

        These commands carry the state.json backup-and-rename. Answering
        0 for an arbitrary copy or move would let a test pass while the
        code moved the wrong file.
        """
        if ".vaibify/state.json" not in sCommand:
            raise UnmodelledContainerCall(
                "Copy/move of something other than the workflow state "
                f"file, which this fake does not model:\n  {sCommand}"
            )
        return (0, "")

    def _ftAnswerModelledCommand(self, sCommand):
        """Return ``(iExitCode, sStdout)`` for a modelled command, else raise.

        The single fail-closed contract shared by BOTH exec surfaces —
        the blocking ``ftResultExecuteCommand`` and the streamed
        ``ftRunInContainerStreamed``. Only commands the browser lane's
        journeys actually issue are modelled, each mirrored by a Lane 2
        assertion; anything else raises ``UnmodelledContainerCall`` so a
        fabricated success can never stand in for a real one on either API.
        """
        self.listSeenCommands.append(sCommand)
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, S_PROJECT_REPO + "\n")
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "pipeline_state" in sCommand:
            return (1, "")
        if "test -d" in sCommand:
            return self._ftAnswerDirectoryProbe(sCommand)
        if "cp -f" in sCommand or "mv -f" in sCommand:
            return self._ftAnswerFileMove(sCommand)
        if "mkdir -p" in sCommand:
            return self._ftAnswerDirectoryCreate(sCommand)
        if "printenv CONTAINER_USER" in sCommand:
            return (0, "researcher\n")
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

    # The file-status poll's two TYPED READS. They are adapter methods,
    # not commands, so they are modelled here rather than in
    # LIST_MODELLED_COMMANDS -- the poll stopped composing `xargs -a`
    # over a scratch file when it moved onto typed reads, and the
    # command entry that used to stand for it was retired with it.
    #
    # MEASURED, and worth knowing: no journey in this lane currently
    # reaches either method. A version of them that raised on every
    # call left all seventy tests green, because the lane's journeys do
    # not dwell in an open workflow long enough to poll. They are
    # modelled correctly anyway -- a fake that answers wrongly is a
    # trap for the journey that finally does -- but the coverage claim
    # belongs to whoever writes that journey, not to this file.
    def fdictStatPathMtimes(self, sContainerId, listPaths):
        return {
            sPath: str(self.dictFileModifiedTimes[sPath])
            for sPath in listPaths
            if sPath in self.dictFileModifiedTimes
        }

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        return "0" * 64

    # The Repos panel's discovery, as TYPED READS. Two `find` execs
    # became one directory listing plus one batched existence probe
    # when the panel's poll stopped being able to mutate.
    #
    # MEASURED, on the same terms as the two above: no journey in this
    # lane reaches either method. Versions that raised on every call
    # left all seventy tests green. Modelled correctly regardless --
    # the trap is a fake that answers WRONGLY for the journey that
    # finally arrives -- but claiming no coverage this lane lacks.
    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        if sDirectoryPath != S_WORKSPACE_ROOT:
            raise UnmodelledContainerCall(
                "The browser lane's adapter was asked to list a "
                f"directory its contract does not model: {sDirectoryPath}"
            )
        return sorted(self.setWorkspaceRepositories)

    def flistContainerPathsExist(self, sContainerId, listPaths):
        return [
            self._fbPathExists(sPath) for sPath in listPaths
        ]

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        """Answer discovery's type probe for the paths it models.

        Every workspace entry this adapter knows about is a
        repository directory, so this and the existence probe agree
        here. They do not agree in production, which is the point of
        asking separately: a plain FILE has no ``.git`` child either,
        and used to be offered as somewhere to run ``git init``.
        """
        return [
            sPath[len(S_WORKSPACE_ROOT) + 1:]
            in self.setWorkspaceRepositories
            for sPath in listPaths
        ]

    def _fbPathExists(self, sPath):
        """Answer the typed existence read for the paths it models.

        The vaibify marker directory is answered TRUE because this
        adapter stands in for a vaibify container: registry recognition
        asks for it through the typed read (an arbitrary exec would be
        refused on the enforced request lane), and a fake that said no
        would report its own container as unrecognized.
        """
        if sPath == S_VAIBIFY_MARKER_DIRECTORY:
            return True
        return (
            sPath[len(S_WORKSPACE_ROOT) + 1:].rsplit("/.git", 1)[0]
            in self.setWorkspaceRepositories
        )

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        return self._ftAnswerModelledCommand(sCommand)

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

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        from types import SimpleNamespace
        # Same fail-closed contract as the blocking API: only modelled
        # commands answer; anything else raises rather than inventing a
        # green exit code the browser lane would read as a real success.
        iExitCode, sStdout = self._ftAnswerModelledCommand(sCommand)
        return SimpleNamespace(
            iExitCode=iExitCode, sStdout=sStdout, sStderr="",
        )
