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

import errno
import hashlib
import io
import json
import posixpath
import tarfile

from vaibify.config import mutationAdmission
from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
)


S_CONTAINER_ID ="browserlane0container0id0000000000000000000000000000000000000000"
S_CONTAINER_NAME = "browser-lane-project"
# The immutable image id a real daemon reports for every running
# container. The council credential gate compares it against the
# maintainer's evidence record, so a fake that omitted it would leave
# the gate resolving against nothing.
S_IMAGE_IDENTITY = "sha256:" + "fa4e" * 16
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


# The typed operations the Files tab's upload and download reach, each
# with the real-container test that shows a real container answers the
# same way. They are adapter METHODS, not commands, so they are declared
# here rather than in LIST_MODELLED_COMMANDS; the same rule holds: a row
# with no sLaneTwoAssertion, or naming a test that does not exist in
# tests/testConfinedStreamingLive.py, is a contract hole.
LIST_MODELLED_FILE_OPERATIONS = [
    {
        "sMethod": "fnWriteFileFromStream",
        "sPurpose": (
            "streamed write: refuses an existing file unless replacing "
            "was allowed, a body of the wrong length, a path outside "
            "its root or through a forbidden name, and a full disk"
        ),
        "sLaneTwoAssertion":
            "testReplacementAndShortBodiesAreRefusedByTheRealProgram",
    },
    {
        "sMethod": "fnMakeDirectory",
        "sPurpose": "folder creation below the authorized root",
        "sLaneTwoAssertion":
            "testANestedFileLandsWithItsParentsCreatedAsTheContainerUser",
    },
    {
        "sMethod": "fiterReadFileConfined",
        "sPurpose": (
            "file read: follows a link that stays inside the root, "
            "refuses one that leaves it, and reports a missing path"
        ),
        "sLaneTwoAssertion":
            "testTheConfinedReadFollowsInRootLinksAndRefusesTheRest",
    },
    {
        "sMethod": "fiterReadDirectoryAsTar",
        "sPurpose": "folder read as a tar whose links stay links",
        "sLaneTwoAssertion":
            "testAFolderArchiveRoundTripsTheTreeAsTheContainerSeesIt",
    },
    {
        "sMethod": "fdictReadFilesystemUsage",
        "sPurpose": "free space behind a path, for the upfront check",
        "sLaneTwoAssertion":
            "testAFilePastTheFetchCeilingIsWrittenIntactInBoundedMemory",
    },
]

I_FILE_STREAM_CHUNK_BYTES = 1 << 20
I_DEFAULT_FREE_BYTES = 1 << 40

# When the modelled Claude login expires, in epoch milliseconds. Zero
# means the document states no expiry, which is what every journey but
# the login-cap one wants. Set and reset by that test; kept here rather
# than on the adapter instance because the hub builds its own.
I_LOGIN_EXPIRES_AT_EPOCH_MILLISECONDS = 0


class UnmodelledContainerCall(RuntimeError):
    """Raised when the fake is asked something its contract omits."""


class FailClosedDockerAdapter:
    """A Docker stand-in that refuses to invent answers."""

    def __init__(self):
        self._dictFiles = {}
        self.listSeenCommands = []
        # Container paths a workspace seed landed, in the order they
        # crossed. A journey asserts against this to prove the
        # researcher's SELECTION reached the container, not merely that
        # the route answered 200.
        self.listSeededPaths = []
        # Containers a journey started after conversion; see
        # fnRecordContainerStarted.
        self.listStartedContainers = []
        # Modification times the file-status poll reports, keyed by
        # container path. Mutable on purpose: the stale-state journey
        # ages an upstream artifact by bumping its stamp here, which is
        # what a real edit inside the container would do.
        self.dictFileModifiedTimes = {
            f"{S_PROJECT_REPO}/Generate/output.dat": 1000,
            f"{S_PROJECT_REPO}/Analyze/summary.json": 2000,
        }
        # What the council's snapshot pre-flight measures. Comfortably
        # inside the capture bounds so a council is convenable; a
        # journey may raise it to drive the refusal.
        self.dictRepositoryWeight = {
            "iFileCount": 120,
            "iTotalBytes": 2 * 1024 * 1024,
            "bTruncated": False,
            "bLargestFilesTruncated": False,
            "listLargestFiles": [
                {"sPath": "README.md", "iSizeBytes": 2048},
            ],
            "listEscapingSymlinks": [], "listSpecialFiles": [],
            "listSubmodules": [],
        }
        # The git-tracked snapshot scope's two reads. None means "every
        # byte is tracked and nothing is untracked", so a journey that
        # only raises the weight still meets the size wall; a journey
        # about the tracked-files offer supplies both answers.
        self.dictTrackedIdentities = None
        self.dictUntrackedInventory = None
        # What the Repos panel's discovery finds under the workspace
        # root: the lane's one project repository.
        self.setWorkspaceRepositories = {
            S_PROJECT_REPO[len(S_WORKSPACE_ROOT) + 1:],
        }
        # The files and folders the Files tab lists, uploads into and
        # downloads from. Kept apart from ``_dictFiles`` (what the
        # product wrote through the older writers) so a listing shows
        # only what a journey seeded or uploaded. Free space is a knob
        # a journey lowers to drive the no-space refusals; every
        # streamed write is recorded with the confinement it was handed.
        self.dictProjectFiles = {}
        self.dictProjectSymlinks = {}
        self.setProjectDirectories = set()
        self.iFreeBytes = I_DEFAULT_FREE_BYTES
        self.listStreamedWrites = []

    def fdictReadDaemonCapacity(self):
        """Report an unmeasurable daemon, so the bounds are the floors.

        A daemon reading is not a container call, so it has no
        container assertion to make — but it must be MODELLED rather
        than left to the fail-closed default, because the council's
        snapshot bounds ask for it on every pre-flight. Zero means
        "unknown", which pins the lane to the declared floors instead
        of to whatever machine is running the suite.
        """
        return {"iMemoryBytes": 0, "iCpuCount": 0}

    def fdictWeighRepository(self, sContainerId, sRepositoryPath):
        """Answer the council's snapshot pre-flight: a small repository.

        Modelled rather than left to raise, because the lane's project
        repo IS small and a council must be convenable in the browser
        journey. A journey wanting the too-large refusal raises
        dictRepositoryWeight, exactly as a researcher's output tree does
        to the real probe.
        """
        if not sRepositoryPath.startswith(S_WORKSPACE_ROOT):
            raise UnmodelledContainerCall(
                "Repository weigh outside the workspace volume, which "
                f"this fake does not speak for: {sRepositoryPath}"
            )
        return dict(self.dictRepositoryWeight)

    def fdictFetchTrackedIdentities(self, sContainerId, sRepositoryPath):
        """Answer the tracked-scope read; scoped to the workspace volume."""
        self._fnRefuseOutsideWorkspace(sRepositoryPath)
        if self.dictTrackedIdentities is not None:
            return dict(self.dictTrackedIdentities)
        return {
            "bSuccess": True, "sReason": "", "sHeadSha": "lanehead0001",
            "sPorcelainDigest": "laneporcelain0001", "iChangedCount": 0,
            "dictEntries": {"project.json": {
                "sMode": "100644", "listStages": [0], "bSkipWorktree": False,
                "sType": "file", "sIdentity": "0" * 40,
                "iSizeBytes": self.dictRepositoryWeight["iTotalBytes"]}}}

    def fdictFetchUntrackedInventory(self, sContainerId, sRepositoryPath):
        """Answer the omission inventory read; scoped like the weigh."""
        self._fnRefuseOutsideWorkspace(sRepositoryPath)
        if self.dictUntrackedInventory is not None:
            return dict(self.dictUntrackedInventory)
        return {"bSuccess": True, "sReason": "", "bComplete": True,
                "listEntries": []}

    def _fnRefuseOutsideWorkspace(self, sRepositoryPath):
        if not sRepositoryPath.startswith(S_WORKSPACE_ROOT):
            raise UnmodelledContainerCall(
                "Repository read outside the workspace volume, which "
                f"this fake does not speak for: {sRepositoryPath}"
            )

    def fnTouchFile(self, sPath, iModifiedTime):
        """Age or freshen one watched path, as an in-container edit would."""
        self.dictFileModifiedTimes[sPath] = iModifiedTime

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": S_CONTAINER_ID[:12],
            "sName": S_CONTAINER_NAME,
            "sImage": "ubuntu:24.04",
            "sImageIdentity": S_IMAGE_IDENTITY,
        }] + list(self.listStartedContainers)

    # The reaper loop and the remnant scanner run inside the lane's hub
    # on their own cadence. Each call they make is modelled with a world
    # that holds nothing left over, so the glyph stays hidden unless a
    # journey seeds the scan itself; an unmodelled call would be recorded
    # as a failed cleanup and paint the glyph red for a reason that has
    # nothing to do with the behaviour under test.
    def fsetListMountSourcesOfAllContainers(self):
        return set()

    def flistListAllContainers(self):
        return [{
            "sContainerId": dictRow["sContainerId"], "sName": dictRow["sName"],
            "sStatus": "running", "dictLabels": {},
        } for dictRow in self.flistGetRunningContainers()]

    def fsReadProcessTable(self, sContainerId):
        self._fnRequireKnownContainer(sContainerId)
        return "@@ clock 1700000000 100 4096 900 899\n1 0 1 1 0 S 10 300 0 sleep\n"

    def fdictReadContainerHostConfig(self, sContainerId):
        self._fnRequireKnownContainer(sContainerId)
        return {"Init": True}

    def flistRunningExecIdentifiers(self, sContainerId):
        self._fnRequireKnownContainer(sContainerId)
        return []

    def _fnRequireKnownContainer(self, sContainerId):
        setKnown = {dictRow["sContainerId"] for dictRow in self.flistGetRunningContainers()}
        if sContainerId not in setKnown:
            raise UnmodelledContainerCall(
                f"the hub asked about container {sContainerId!r}, which this "
                "lane is not running")

    def fnRecordContainerStarted(self, sName, sContainerId):
        """Make a container the lane just STARTED report as running.

        A journey that converts a project and then acts on the result
        needs the world to agree that the new container exists: the
        start executor is patched to avoid a real daemon, so without
        this the lane would insist the container it just started is
        not running, and every follow-on route would 404 for a reason
        that has nothing to do with the behaviour under test.
        """
        self.listStartedContainers.append({
            "sContainerId": sContainerId,
            "sShortId": sContainerId[:12],
            "sName": sName,
            "sImage": "ubuntu:24.04",
            "sImageIdentity": S_IMAGE_IDENTITY,
        })

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
        """Hash a file the Files tab models; answer as before for the rest.

        A modelled file answers its real digest, and a path inside a
        modelled folder that holds nothing answers ``""`` (the typed
        read's spelling of "absent"), so an upload's prior hash is a
        real comparison. Every other path keeps the constant it always
        had, which nothing compares.
        """
        if sPath in self.dictProjectFiles:
            return hashlib.sha256(self.dictProjectFiles[sPath]).hexdigest()
        if posixpath.dirname(sPath) in self.setProjectDirectories:
            return ""
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
        if not self._fbIsInsideWorkspace(sDirectoryPath):
            raise UnmodelledContainerCall(
                "The browser lane's adapter was asked to list a "
                f"directory its contract does not model: {sDirectoryPath}"
            )
        if not self._fbIsModelledDirectory(sDirectoryPath):
            raise FileNotFoundError(
                f"Cannot list directory in container: {sDirectoryPath}"
            )
        return self._flistChildNames(sDirectoryPath)

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
            self._fbIsModelledDirectory(self._fsFollowLinkLexically(sPath))
            for sPath in listPaths
        ]

    def flistReadGitRepoStatuses(self, sContainerId, listRepoPaths):
        """Answer the Repos panel's batched git-status typed read.

        A typed read, not a command, so it is exempt from
        ``LIST_MODELLED_COMMANDS`` — the fail-closed COMMAND contract is
        untouched. The fake models no git history, so every requested
        repo reports a clean, empty status rather than raising an
        ``AttributeError`` the caller does not catch (it guards only
        ``OSError``/``ValueError``). Returning the empty list per repo is
        the honest "nothing to report" answer for a container with no
        commits, and it keeps a project-open journey free of a spurious
        500 the moment the panel polls.
        """
        return [
            {"sPath": sRepoPath, "bMissing": False, "sBranch": "main",
             "sPorcelain": "", "sUrl": ""}
            for sRepoPath in listRepoPaths
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
        if (sPath in self.dictProjectFiles
                or sPath in self.dictProjectSymlinks
                or sPath in self.setProjectDirectories):
            return True
        return (
            sPath[len(S_WORKSPACE_ROOT) + 1:].rsplit("/.git", 1)[0]
            in self.setWorkspaceRepositories
        )

    # --- The Files tab's files -------------------------------------
    #
    # An in-memory project volume that REFUSES where the real programs
    # refuse (LIST_MODELLED_FILE_OPERATIONS names the live test behind
    # each). A fake that always succeeded would report every upload
    # landed and every download whole.

    def fnResetProjectFiles(self):
        """Forget every modelled file, link, folder and write record."""
        self.dictProjectFiles.clear()
        self.dictProjectSymlinks.clear()
        self.setProjectDirectories.clear()
        self.listStreamedWrites.clear()
        self.iFreeBytes = I_DEFAULT_FREE_BYTES

    def fnSeedDirectory(self, sPath):
        """Make a folder exist, with every folder above it."""
        self._fnRequireInsideWorkspace(sPath)
        self._fnRegisterDirectoryChain(sPath)

    def fnSeedFile(self, sPath, baContent):
        """Make a file exist, as an in-container writer would have."""
        self._fnRequireInsideWorkspace(sPath)
        self._fnRegisterDirectoryChain(posixpath.dirname(sPath))
        self.dictProjectFiles[sPath] = baContent

    def fnSeedSymlink(self, sPath, sTarget):
        """Make a symlink exist; its target is text, as on a real disk."""
        self._fnRequireInsideWorkspace(sPath)
        self._fnRegisterDirectoryChain(posixpath.dirname(sPath))
        self.dictProjectSymlinks[sPath] = sTarget

    def _fbIsInsideWorkspace(self, sPath):
        return sPath == S_WORKSPACE_ROOT or sPath.startswith(
            S_WORKSPACE_ROOT + "/")

    def _fnRequireInsideWorkspace(self, sPath):
        if not self._fbIsInsideWorkspace(sPath):
            raise UnmodelledContainerCall(
                "A file operation outside the workspace volume, which "
                f"this fake does not speak for: {sPath}"
            )

    def _fnRegisterDirectoryChain(self, sDirectory):
        while sDirectory.startswith(S_WORKSPACE_ROOT + "/"):
            self.setProjectDirectories.add(sDirectory)
            sDirectory = posixpath.dirname(sDirectory)

    def _fbIsModelledDirectory(self, sPath):
        return (
            sPath == S_WORKSPACE_ROOT
            or sPath in self.setProjectDirectories
            or sPath[len(S_WORKSPACE_ROOT) + 1:]
            in self.setWorkspaceRepositories
        )

    def _flistChildNames(self, sDirectory):
        setNames = set()
        if sDirectory == S_WORKSPACE_ROOT:
            setNames.update(self.setWorkspaceRepositories)
        for sPath in (
            list(self.dictProjectFiles) + list(self.dictProjectSymlinks)
            + list(self.setProjectDirectories)
        ):
            if posixpath.dirname(sPath) == sDirectory:
                setNames.add(posixpath.basename(sPath))
        return sorted(setNames)

    def _fsFollowLinkLexically(self, sPath):
        """Resolve a chain of modelled links as text; any other path as is."""
        for _ in range(8):
            if sPath not in self.dictProjectSymlinks:
                return sPath
            sTarget = self.dictProjectSymlinks[sPath]
            sPath = posixpath.normpath(posixpath.join(
                posixpath.dirname(sPath), sTarget))
        return sPath

    def _fnRefuseBelowRootOrForbidden(
        self, sPath, sAuthorizedRoot, tForbiddenNames, sVerb,
    ):
        sRoot = posixpath.normpath(sAuthorizedRoot or "/")
        sBelow = posixpath.relpath(sPath, sRoot)
        if sBelow.startswith("..") or sBelow == ".":
            raise ContainerWriteRefusedError(
                f"{sVerb} to {sPath} refused: refused: the path is not "
                "below its authorized root")
        for sName in sBelow.split("/"):
            if sName in tForbiddenNames:
                raise ContainerWriteRefusedError(
                    f"{sVerb} to {sPath} refused: refused: writes "
                    f"through '{sName}' are not permitted")

    def _fnRequireParentFolders(self, sPath, sAuthorizedRoot, bCreate):
        sParent = posixpath.dirname(sPath)
        if self._fbIsModelledDirectory(sParent):
            return
        if not bCreate or not sParent.startswith(
            posixpath.normpath(sAuthorizedRoot or "/") + "/"
        ):
            raise FileNotFoundError(
                errno.ENOENT, f"Cannot write {sPath}: not found: "
                f"'{posixpath.basename(sParent)}'")
        self._fnRegisterDirectoryChain(sParent)

    def _fnRefuseUnwritableFinal(self, sPath, bReplaceAllowed):
        sName = posixpath.basename(sPath)
        if (sPath in self.dictProjectSymlinks
                or self._fbIsModelledDirectory(sPath)):
            raise ContainerWriteRefusedError(
                f"Write to {sPath} refused: refused: '{sName}' is a "
                "symlink or a directory")
        if sPath in self.dictProjectFiles and not bReplaceAllowed:
            raise ContainerWriteExistsError(
                f"Write to {sPath} refused: refused: '{sName}' already "
                "exists")

    def _fbaReceiveStream(self, fileSource, iExpectedBytes, sPath):
        listChunks = []
        iReceived = 0
        for baChunk in iter(
            lambda: fileSource.read(I_FILE_STREAM_CHUNK_BYTES), b""
        ):
            iReceived += len(baChunk)
            listChunks.append(baChunk)
        if iExpectedBytes is not None and iReceived != iExpectedBytes:
            raise ContainerWriteRefusedError(
                f"Write to {sPath} refused: refused: {iReceived} bytes "
                f"arrived but {iExpectedBytes} were expected")
        return b"".join(listChunks)

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource,
        iExpectedBytes=None, bReplaceAllowed=True, iMode=None,
        sAuthorizedRoot=None, tForbiddenNames=(), bCreateParents=False,
    ):
        """Write one file from a stream; the old bytes survive a refusal."""
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileFromStream")
        self._fnRequireInsideWorkspace(sFilePath)
        self._fnRefuseBelowRootOrForbidden(
            sFilePath, sAuthorizedRoot, tForbiddenNames, "Write")
        self._fnRequireParentFolders(
            sFilePath, sAuthorizedRoot, bCreateParents)
        self._fnRefuseUnwritableFinal(sFilePath, bReplaceAllowed)
        baContent = self._fbaReceiveStream(
            fileSource, iExpectedBytes, sFilePath)
        if len(baContent) > self.iFreeBytes:
            raise OSError(
                errno.ENOSPC,
                f"Cannot write {sFilePath} in the container: no space "
                "left on device")
        self.dictProjectFiles[sFilePath] = baContent
        self.listStreamedWrites.append({
            "sPath": sFilePath, "iBytes": len(baContent),
            "bReplaceAllowed": bReplaceAllowed,
            "sAuthorizedRoot": sAuthorizedRoot,
            "tForbiddenNames": tuple(tForbiddenNames),
            "bCreateParents": bCreateParents,
        })

    def fnMakeDirectory(
        self, sContainerId, sDirectoryPath,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Create a folder (and its missing parents below the root)."""
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnMakeDirectory")
        self._fnRequireInsideWorkspace(sDirectoryPath)
        self._fnRefuseBelowRootOrForbidden(
            sDirectoryPath, sAuthorizedRoot, tForbiddenNames, "Write")
        if (sDirectoryPath in self.dictProjectFiles
                or sDirectoryPath in self.dictProjectSymlinks):
            raise ContainerWriteRefusedError(
                f"Write to {sDirectoryPath} refused: it is not a folder")
        self._fnRegisterDirectoryChain(sDirectoryPath)

    def fdictReadFilesystemUsage(self, sContainerId, sPath):
        """Report the volume's space; ``iFreeBytes`` is the knob."""
        self._fnRequireInsideWorkspace(sPath)
        return {
            "iTotalBytes": I_DEFAULT_FREE_BYTES,
            "iUsedBytes": I_DEFAULT_FREE_BYTES - self.iFreeBytes,
            "iFreeBytes": self.iFreeBytes,
        }

    def _fnRefuseReadOutsideRoot(self, sPath, sAuthorizedRoot):
        sRoot = posixpath.normpath(sAuthorizedRoot or "/")
        if not (sPath + "/").startswith(sRoot.rstrip("/") + "/"):
            raise ContainerReadRefusedError(
                f"Read of {sPath} refused: {sPath!r} is not below "
                f"{sRoot!r}")

    def _fsResolveReadableFile(self, sFilePath, sAuthorizedRoot):
        """Follow in-root links as text; refuse one that leaves the root."""
        sRoot = posixpath.normpath(sAuthorizedRoot or "/")
        sCurrent = sFilePath
        for _ in range(8):
            if sCurrent not in self.dictProjectSymlinks:
                return sCurrent
            sTarget = self.dictProjectSymlinks[sCurrent]
            sResolved = posixpath.normpath(posixpath.join(
                posixpath.dirname(sCurrent), sTarget))
            if not (sResolved + "/").startswith(sRoot.rstrip("/") + "/"):
                raise ContainerReadRefusedError(
                    f"Read of {sFilePath} refused: refused: "
                    f"'{posixpath.basename(sCurrent)}' points to "
                    f"'{sTarget}', which is outside the project")
            sCurrent = sResolved
        raise ContainerReadRefusedError(
            f"Read of {sFilePath} refused: refused: more than 8 links "
            "in a row")

    def fiterReadFileConfined(
        self, sContainerId, sFilePath, sAuthorizedRoot=None,
    ):
        """Yield a file's bytes; every refusal surfaces on the first pull."""
        self._fnRequireInsideWorkspace(sFilePath)
        self._fnRefuseReadOutsideRoot(sFilePath, sAuthorizedRoot)
        sReal = self._fsResolveReadableFile(sFilePath, sAuthorizedRoot)
        if self._fbIsModelledDirectory(sReal):
            raise ContainerReadRefusedError(
                f"Read of {sFilePath} refused: refused: "
                f"'{sFilePath}' is not a regular file")
        if sReal not in self.dictProjectFiles:
            raise FileNotFoundError(
                errno.ENOENT,
                f"{sFilePath}: not found: '{posixpath.basename(sReal)}'")
        baContent = self.dictProjectFiles[sReal]
        for iStart in range(0, len(baContent), I_FILE_STREAM_CHUNK_BYTES):
            yield baContent[iStart:iStart + I_FILE_STREAM_CHUNK_BYTES]

    def _fnAddFolderToTar(self, tarOut, sFolder, sArchiveName):
        tarOut.addfile(self._finfoDirectory(sArchiveName))
        for sPath in sorted(self.setProjectDirectories):
            if posixpath.dirname(sPath) == sFolder:
                self._fnAddFolderToTar(
                    tarOut, sPath,
                    f"{sArchiveName}/{posixpath.basename(sPath)}")
        for sPath, baContent in sorted(self.dictProjectFiles.items()):
            if posixpath.dirname(sPath) == sFolder:
                infoFile = tarfile.TarInfo(
                    f"{sArchiveName}/{posixpath.basename(sPath)}")
                infoFile.size = len(baContent)
                tarOut.addfile(infoFile, io.BytesIO(baContent))
        for sPath, sTarget in sorted(self.dictProjectSymlinks.items()):
            if posixpath.dirname(sPath) == sFolder:
                infoLink = tarfile.TarInfo(
                    f"{sArchiveName}/{posixpath.basename(sPath)}")
                infoLink.type = tarfile.SYMTYPE
                infoLink.linkname = sTarget
                tarOut.addfile(infoLink)

    @staticmethod
    def _finfoDirectory(sName):
        infoDirectory = tarfile.TarInfo(sName)
        infoDirectory.type = tarfile.DIRTYPE
        infoDirectory.mode = 0o755
        return infoDirectory

    def fiterReadDirectoryAsTar(
        self, sContainerId, sDirectoryPath, sAuthorizedRoot=None,
    ):
        """Yield a folder as a tar whose links stay links."""
        self._fnRequireInsideWorkspace(sDirectoryPath)
        self._fnRefuseReadOutsideRoot(sDirectoryPath, sAuthorizedRoot)
        if not self._fbIsModelledDirectory(sDirectoryPath):
            raise FileNotFoundError(
                errno.ENOENT, f"{sDirectoryPath}: not found")
        bufferTar = io.BytesIO()
        with tarfile.open(fileobj=bufferTar, mode="w") as tarOut:
            self._fnAddFolderToTar(
                tarOut, sDirectoryPath, posixpath.basename(sDirectoryPath))
        baArchive = bufferTar.getvalue()
        for iStart in range(0, len(baArchive), I_FILE_STREAM_CHUNK_BYTES):
            yield baArchive[iStart:iStart + I_FILE_STREAM_CHUNK_BYTES]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        return self._ftAnswerModelledCommand(sCommand)

    def fbaFetchCredentialFile(self, sContainerId, sPath):
        """The council's bounded credential read, same modelled paths."""
        return self.fbaFetchFile(sContainerId, sPath)

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath in self.dictProjectFiles:
            return self.dictProjectFiles[sPath]
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        # The council's launch-time login-presence probe: the journey
        # models a project the researcher has already logged in to.
        if sPath.endswith("/.claude/.credentials.json"):
            dictOauth = {"accessToken": "fixture-access-token"}
            # Absent by default, exactly as the ordinary journeys want:
            # a login with no stated expiry clamps nothing and the
            # convene form says nothing about it. A test that needs the
            # cap notice sets the module knob and resets it.
            if I_LOGIN_EXPIRES_AT_EPOCH_MILLISECONDS:
                dictOauth["expiresAt"] = I_LOGIN_EXPIRES_AT_EPOCH_MILLISECONDS
            return json.dumps({"claudeAiOauth": dictOauth}).encode("utf-8")
        raise FileNotFoundError(sPath)

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        self._dictFiles[sPath] = baContent

    def fnWriteFileViaTar(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        self._dictFiles[sPath] = baContent

    def fnWriteTreeViaTar(
        self, sContainerId, sDestinationDirectory, listHostPaths,
        iUid=None, iGid=None, sArchiveName=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
        bCreateDestination=False,
    ):
        """Record the tree copy as one entry per archived top-level path.

        Recorded rather than ignored so a journey can assert WHAT
        crossed into the container. The real primitive walks each
        directory; the lane only ever asserts the selection it passed,
        so the top level is the honest granularity to model -- pretending
        to expand a host tree here would be inventing content the fake
        never read.
        """
        import os
        for sHostPath in listHostPaths:
            sLanded = f"{sDestinationDirectory}/{os.path.basename(sHostPath)}"
            self.listSeededPaths.append(sLanded)

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
