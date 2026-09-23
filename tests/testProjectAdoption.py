"""Every way adopting a directory as a Project can refuse, and what it says.

Adoption is one step with four pieces, and the reason it exists is that
a caller assembling those pieces by hand gets a directory the dashboard
can see but has never been told to offer. So the assertions here are
about the SEQUENCE and the REFUSALS rather than about git: that the
order cannot be rearranged without a test failing, that every refusal
names a remedy, and that a re-run reports rather than re-does.

What this file does NOT prove: that git behaves as assumed. The
connection here is a stub that answers the specific commands the module
issues, so it can only show the module reacts correctly to those
answers -- it cannot show the answers are what a real git gives. The
git semantics adoption relies on were measured separately inside a real
container (see ``projectAdoption``'s module docstring), and the live
end-to-end drive is the adoption block at the end of
``testContainerAcceptance.py`` -- which is where the claim that
matters, "discovery can see what adoption produced", is actually
asked. Read a green run here as "the ordering and the refusals hold",
never as "adoption works".
"""

import json

import pytest
from fastapi import HTTPException

from vaibify.gui import projectAdoption


S_CONTAINER = "container-abcdef"
S_ROOT = "/workspace"
S_DIRECTORY = "analysisSandbox"
S_PROJECT_NAME = "Batch analysis run"
S_FILE_NAME = "batchAnalysis.json"
S_REPOSITORY = S_ROOT + "/" + S_DIRECTORY
S_PROJECT_PATH = (
    S_REPOSITORY + "/.vaibify/projects/" + S_FILE_NAME
)


class FakeConnection:
    """Answers only the commands ``projectAdoption`` actually issues.

    Deliberately narrow. A stub that tried to emulate git would be a
    second implementation of git's CLI contract, and a test asserting
    against it would be asserting against that emulation. This answers
    the handful of probes the module makes and RAISES on anything else,
    so a new probe cannot quietly receive a plausible default.
    """

    def __init__(
        self, setDirectories=frozenset(), setFiles=frozenset(),
        dictRepositoryRoots=None, setReposWithCommits=frozenset(),
        dictFailures=None, setUnobservablePaths=frozenset(),
    ):
        self.setDirectories = set(setDirectories)
        self.setFiles = set(setFiles)
        self.dictRepositoryRoots = dict(dictRepositoryRoots or {})
        self.setReposWithCommits = set(setReposWithCommits)
        self.dictFailures = dict(dictFailures or {})
        self.setUnobservablePaths = set(setUnobservablePaths)
        self.saCommands = []
        self.saProbes = []
        self.dictWritten = {}

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        assert sContainerId == S_CONTAINER
        self.saCommands.append(sCommand)
        for sKind, tAnswer in self.dictFailures.items():
            if sKind in sCommand:
                return tAnswer
        return self._ftAnswer(sCommand)

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        """The declared directory probe, as the real adapter answers it."""
        assert sContainerId == S_CONTAINER
        self.saProbes.append("isdir:" + sPath)
        if sPath in self.setUnobservablePaths:
            raise OSError(f"cannot probe {sPath}")
        return sPath in self.setDirectories

    def fbContainerPathIsFile(self, sContainerId, sPath):
        """The declared file probe, as the real adapter answers it."""
        assert sContainerId == S_CONTAINER
        self.saProbes.append("isfile:" + sPath)
        if sPath in self.setUnobservablePaths:
            raise OSError(f"cannot probe {sPath}")
        return sPath in self.setFiles

    def _ftAnswer(self, sCommand):
        if "rev-parse --show-toplevel" in sCommand:
            sRoot = self.dictRepositoryRoots.get(self._fsGitPath(sCommand))
            return (0, sRoot + "\n") if sRoot else (128, "not a git repo")
        if "rev-parse --verify HEAD" in sCommand:
            sPath = self._fsGitPath(sCommand)
            return (0, "abc123\n") if sPath in self.setReposWithCommits \
                else (128, "")
        if " init" in sCommand and sCommand.startswith("git "):
            sPath = self._fsGitPath(sCommand)
            self.dictRepositoryRoots[sPath] = sPath
            return (0, "Initialized empty repository")
        if " commit " in sCommand:
            self.setReposWithCommits.add(self._fsGitPath(sCommand))
            return (0, "[main (root-commit)] Initialize")
        if sCommand.startswith("mkdir -p "):
            self.setDirectories.add(self._fsTarget(sCommand))
            return (0, "")
        raise AssertionError(f"unstubbed container command: {sCommand}")

    @staticmethod
    def _fsTarget(sCommand):
        """Return the single quoted path argument of a probe command."""
        return sCommand.split(" ", 2)[-1].strip().strip("'")

    @staticmethod
    def _fsGitPath(sCommand):
        """Return the -C argument of a git command."""
        return sCommand.split("-C ", 1)[1].split(" ", 1)[0].strip("'")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        assert sContainerId == S_CONTAINER
        self.dictWritten[sPath] = baContent
        self.setFiles.add(sPath)


@pytest.fixture(autouse=True)
def fixtureStubTheSidecarAndTheSearch(monkeypatch):
    """Keep tracking and project discovery off any real container.

    Both are exercised for the CALLS adoption makes, not for their own
    behavior: the sidecar records what was tracked, and the search
    answers which project names are taken.
    """
    dictState = {"saTracked": [], "listProjects": []}

    monkeypatch.setattr(
        projectAdoption.trackedReposManager, "fsRepositoryRootFor",
        lambda sResourceId: S_ROOT,
    )
    monkeypatch.setattr(
        projectAdoption.trackedReposManager, "fdictReadSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [
                {"sName": sName} for sName in dictState["saTracked"]
            ],
        },
    )
    monkeypatch.setattr(
        projectAdoption.trackedReposManager, "fdictComputeRepoStatus",
        lambda connectionDocker, sContainerId, sRepoName: {
            "bMissing": False, "sUrl": "",
        },
    )
    monkeypatch.setattr(
        projectAdoption.trackedReposManager, "fnAddTracked",
        lambda connectionDocker, sContainerId, sRepoName, sUrl: (
            dictState["saTracked"].append(sRepoName)
        ),
    )
    monkeypatch.setattr(
        projectAdoption.workflowManager,
        "flistFindWorkflowsInContainer",
        lambda connectionDocker, sContainerId: dictState["listProjects"],
    )
    return dictState


def _fdictAdopt(connectionFake, sFileName=S_FILE_NAME):
    """Run adoption with the fixture's canonical arguments."""
    return projectAdoption.fdictAdoptDirectoryAsProject(
        connectionFake, S_CONTAINER, S_DIRECTORY, S_PROJECT_NAME,
        sFileName,
    )


def _fconnectionBareDirectory():
    """A plain directory: exists, is no repository, has no project."""
    return FakeConnection(setDirectories={S_REPOSITORY})


# ----------------------------------------------------------------------
# The request never reaches the container
# ----------------------------------------------------------------------


@pytest.mark.parametrize("sDirectory,sRefusal", [
    ("", "adoption-directory-name-empty"),
    ("   ", "adoption-directory-name-empty"),
    ("one/two", "adoption-directory-name-not-one-segment"),
    ("../escape", "adoption-directory-name-not-one-segment"),
    (".hidden", "adoption-directory-hidden"),
    ("has space", "adoption-directory-name-invalid"),
    ("-leadingDash", "adoption-directory-name-invalid"),
])
def testDirectoryNameRefusalsNameTheirCause(sDirectory, sRefusal):
    """Each malformed directory name refuses with its own code."""
    with pytest.raises(HTTPException) as recordRaised:
        projectAdoption.fsValidateProjectDirectoryName(sDirectory)
    assert recordRaised.value.status_code == 400
    assert recordRaised.value.detail["sRefusal"] == sRefusal


@pytest.mark.parametrize("sProjectName,sRefusal", [
    ("", "adoption-project-name-empty"),
    ("  ", "adoption-project-name-empty"),
    ("x" * 201, "adoption-project-name-too-long"),
])
def testProjectNameRefusalsNameTheirCause(sProjectName, sRefusal):
    """A project must be named, and the name must fit."""
    with pytest.raises(HTTPException) as recordRaised:
        projectAdoption.fsValidateProjectName(sProjectName)
    assert recordRaised.value.status_code == 400
    assert recordRaised.value.detail["sRefusal"] == sRefusal


@pytest.mark.parametrize("sFileName", [
    "", "   ", "a/b.json", "../b.json", "has space.json", ".dotfile",
    "x" * 201,
])
def testProjectFileNameRefusalsAreAllFourHundred(sFileName):
    """Every malformed file name refuses before the container is reached."""
    with pytest.raises(HTTPException) as recordRaised:
        projectAdoption.fsValidateProjectFileName(sFileName)
    assert recordRaised.value.status_code == 400
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-file-name-invalid"
    )


@pytest.mark.parametrize("sProjectName,sExpected", [
    ("Batch analysis run", "batchAnalysisRun.json"),
    ("  spaced  out  ", "spacedOut.json"),
    ("Punctuation: it's fine!", "punctuationItSFine.json"),
    ("already camelCase", "alreadyCamelCase.json"),
    ("42 answers", "42Answers.json"),
])
def testADisplayNameBecomesACamelCaseFileName(sProjectName, sExpected):
    """A display name becomes a usable file name without the caller asking.

    camelCase because that is this repository's file-naming
    convention: a researcher reading ``.vaibify/projects/`` should not
    be able to tell which files a person named and which vaibify did.
    """
    assert projectAdoption.fsDeriveProjectFileName(
        sProjectName,
    ) == sExpected


def testADisplayNameWithNoUsableCharactersRefusesRatherThanDefaulting():
    """A name that cannot become a file name refuses, naming the remedy.

    The alternative -- falling back to a default file name -- would put
    the project somewhere the researcher never asked for, under a name
    matching no project they can see.
    """
    with pytest.raises(HTTPException) as recordRaised:
        projectAdoption.fsDeriveProjectFileName("...")
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-file-name-invalid"
    )
    assert recordRaised.value.detail["sRemedy"]


# ----------------------------------------------------------------------
# The directory itself
# ----------------------------------------------------------------------


def testAMissingDirectoryRefusesAndIsNeverCreated():
    """Adoption never creates the directory; a typo must not make a project.

    The refusal is the feature. If adoption created what it could not
    find, a mistyped name would produce an empty project beside the
    real one and both would look legitimate.
    """
    connectionFake = FakeConnection()
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.status_code == 404
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-directory-missing"
    )
    assert not any(
        sCommand.startswith("mkdir")
        for sCommand in connectionFake.saCommands
    )


def testAPathThatIsAFileRefusesDistinctlyFromOneThatIsAbsent():
    """"Exists but is a file" and "is not there" are different problems."""
    connectionFake = FakeConnection(setFiles={S_REPOSITORY})
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.status_code == 400
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-path-not-directory"
    )


def testADirectoryInsideAnotherRepositoryRefusesAndNamesThatRepository():
    """Adopting a subdirectory would silently repoint the project root.

    A project's repo root is detected from its ``project.json`` upwards,
    so adopting ``outer/inner`` would make ``outer`` the project
    repository and resolve every declared path from there. The refusal
    names the root that would have been chosen, because that is the
    directory the researcher probably meant.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_ROOT + "/outerRepo"},
    )
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.status_code == 409
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-directory-inside-another-repository"
    )
    assert "outerRepo" in recordRaised.value.detail["sMessage"]
    assert "outerRepo" in recordRaised.value.detail["sRemedy"]


def testNoRepositoryIsInitializedInsideAnother():
    """The nesting refusal fires BEFORE git init, not after."""
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_ROOT + "/outerRepo"},
    )
    with pytest.raises(HTTPException):
        _fdictAdopt(connectionFake)
    assert not any(
        " init" in sCommand for sCommand in connectionFake.saCommands
    )


# ----------------------------------------------------------------------
# The container refuses mid-sequence
# ----------------------------------------------------------------------


@pytest.mark.parametrize("sFailingFragment,sRefusal", [
    ("init", "adoption-git-init-failed"),
    ("commit", "adoption-initial-commit-failed"),
    ("mkdir -p", "adoption-projects-directory-failed"),
])
def testAFailedContainerCommandRefusesWithItsOwnCode(
    sFailingFragment, sRefusal,
):
    """Each failing stage names itself rather than a generic 500."""
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictFailures={sFailingFragment: (1, "permission denied")},
    )
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.detail["sRefusal"] == sRefusal
    assert "permission denied" in recordRaised.value.detail["sMessage"]


def testAnUntrackableRepositoryRefusesAfterItIsPrepared():
    """A repository the sidecar cannot read back refuses, naming reconcile."""
    connectionFake = _fconnectionBareDirectory()
    with pytest.raises(HTTPException) as recordRaised:
        with pytest.MonkeyPatch.context() as recordPatch:
            recordPatch.setattr(
                projectAdoption.trackedReposManager,
                "fdictComputeRepoStatus",
                lambda connectionDocker, sContainerId, sRepoName: {
                    "bMissing": True,
                },
            )
            _fdictAdopt(connectionFake)
    assert recordRaised.value.status_code == 404
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-repository-untrackable"
    )


def testADuplicateProjectNameRefusesAndNamesTheExistingPath(
    fixtureStubTheSidecarAndTheSearch,
):
    """Two projects in one resource may not share a display name."""
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [
        {"sName": S_PROJECT_NAME, "sPath": "/workspace/other/p.json"},
    ]
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(_fconnectionBareDirectory())
    assert recordRaised.value.status_code == 409
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-project-name-taken"
    )
    assert "/workspace/other/p.json" in (
        recordRaised.value.detail["sMessage"]
    )


# ----------------------------------------------------------------------
# Every refusal is actionable
# ----------------------------------------------------------------------


def testEveryRefusalCarriesAMessageARemedyAndACode():
    """A refusal an agent cannot act on becomes improvised shell work.

    This is the property the whole failure-mode enumeration exists to
    hold: the in-container agent has no view of the dashboard, so a
    refusal that does not name the next action is one it will work
    around rather than relay.
    """
    listRefusals = []
    for tArguments in [
        (400, "a", "m", "r"), (404, "b", "m", "r"), (409, "c", "m", "r"),
    ]:
        try:
            projectAdoption.fnRefuseAdoption(*tArguments)
        except HTTPException as errorRefusal:
            listRefusals.append(errorRefusal)
    assert len(listRefusals) == 3
    for errorRefusal in listRefusals:
        assert set(errorRefusal.detail) == {
            "sMessage", "sRemedy", "sRefusal",
        }
        assert all(errorRefusal.detail.values())


# ----------------------------------------------------------------------
# The happy paths, and what they report
# ----------------------------------------------------------------------


def testABareDirectoryPerformsAllFourStages(
    fixtureStubTheSidecarAndTheSearch,
):
    """A sandbox directory becomes a tracked project in one call."""
    connectionFake = _fconnectionBareDirectory()
    dictReport = _fdictAdopt(connectionFake)
    assert dictReport["saStagesPerformed"] == list(
        projectAdoption.T_ADOPTION_STAGES
    )
    assert dictReport["saStagesAlreadySatisfied"] == []
    assert dictReport["bProjectIsNew"] is True
    assert dictReport["sProjectPath"] == S_PROJECT_PATH
    assert dictReport["sRepositoryPath"] == S_REPOSITORY
    assert fixtureStubTheSidecarAndTheSearch["saTracked"] == [S_DIRECTORY]


def testAnExistingRepositoryWithCommitsReportsThoseStagesSatisfied():
    """What was already true is reported as already true, not as done."""
    dictReport = _fdictAdopt(FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
    ))
    assert dictReport["saStagesPerformed"] == [
        projectAdoption.S_STAGE_WROTE_PROJECT_FILE,
        projectAdoption.S_STAGE_TRACKED_REPOSITORY,
    ]
    assert dictReport["saStagesAlreadySatisfied"] == [
        projectAdoption.S_STAGE_CREATED_REPOSITORY,
        projectAdoption.S_STAGE_CREATED_INITIAL_COMMIT,
    ]


def testTheWrittenProjectFileNamesItself():
    """``sWorkflowName`` is populated, so the Project field shows a name.

    A project file without it falls back to displaying its own file
    name, and the template every hand-authored project was copied from
    omitted the key -- so the omission propagated from project to
    project. This is the assertion that stops it.
    """
    connectionFake = _fconnectionBareDirectory()
    _fdictAdopt(connectionFake)
    dictWritten = json.loads(
        connectionFake.dictWritten[S_PROJECT_PATH].decode("utf-8"),
    )
    assert dictWritten["sWorkflowName"] == S_PROJECT_NAME
    assert dictWritten["listSteps"] == []


def testARerunReportsEverythingSatisfiedAndOverwritesNothing(
    fixtureStubTheSidecarAndTheSearch,
):
    """Adoption is idempotent, because an agent that cannot see will retry.

    The second run must not discard steps added between the two, and
    must not refuse -- a refusal on retry is indistinguishable, to the
    caller, from a refusal on a genuine conflict.
    """
    connectionFake = _fconnectionBareDirectory()
    _fdictAdopt(connectionFake)
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [
        {"sName": S_PROJECT_NAME, "sPath": S_PROJECT_PATH},
    ]
    baFirstWrite = connectionFake.dictWritten[S_PROJECT_PATH]
    dictReport = _fdictAdopt(connectionFake)
    assert dictReport["saStagesPerformed"] == []
    assert dictReport["saStagesAlreadySatisfied"] == list(
        projectAdoption.T_ADOPTION_STAGES
    )
    assert dictReport["bProjectIsNew"] is False
    assert connectionFake.dictWritten[S_PROJECT_PATH] is baFirstWrite


def testTheProjectsOwnNameNeverCollidesWithItself(
    fixtureStubTheSidecarAndTheSearch,
):
    """The duplicate-name check exempts the file being adopted.

    Without the exemption a re-run would refuse with
    ``adoption-project-name-taken`` against itself, which is the exact
    shape of a bug that makes a retry look like a conflict.
    """
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [
        {"sName": S_PROJECT_NAME, "sPath": S_PROJECT_PATH},
    ]
    dictReport = _fdictAdopt(FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
        setFiles={S_PROJECT_PATH},
    ))
    assert dictReport["bProjectIsNew"] is False


def testAdoptionNeverRewritesHistory():
    """No stage may rewrite commit ids to buy a cosmetic empty root.

    Measured in a real container: every git operation vaibify performs
    works on a repository whose root commit carries content. Grafting
    an empty commit beneath existing history would rewrite every id --
    and on a repository with a remote, that is a rewrite the researcher
    never asked for.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
    )
    _fdictAdopt(connectionFake)
    sIssued = " ".join(connectionFake.saCommands)
    for sForbidden in [
        "rebase", "--orphan", "filter-branch", "reset --hard",
        "commit-tree", "update-ref", "push",
    ]:
        assert sForbidden not in sIssued, (
            f"adoption issued '{sForbidden}'"
        )


def testARepositoryThatAlreadyHostsAProjectGetsNoSecondOne(
    fixtureStubTheSidecarAndTheSearch,
):
    """"Make this a Project" is about the DIRECTORY, not a file name.

    The migration case: every project authored before adoption existed
    was named by hand, so its file name is whatever its author chose.
    Deriving a file name from the researcher's chosen display name and
    writing THAT would put a second project beside the first, both
    looking legitimate in the Project field with nothing to say which
    the agent had been working in. Adoption therefore reports the
    project the repository already hosts and writes nothing.
    """
    sHandAuthored = (
        S_REPOSITORY + "/.vaibify/projects/handAuthored.json"
    )
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [{
        "sName": "handAuthored.json",
        "sPath": sHandAuthored,
        "sProjectRepoPath": S_REPOSITORY,
    }]
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
        setFiles={sHandAuthored},
    )
    dictReport = _fdictAdopt(connectionFake)
    assert dictReport["sProjectPath"] == sHandAuthored, (
        "adoption reported a project other than the one the "
        "repository already hosts"
    )
    assert dictReport["sProjectName"] == "handAuthored.json", (
        "adoption renamed the existing project to the name it was "
        "asked for"
    )
    assert dictReport["bProjectIsNew"] is False
    assert connectionFake.dictWritten == {}, (
        f"a second project file was written: "
        f"{list(connectionFake.dictWritten)}"
    )


@pytest.mark.falsification
def testAProjectInADifferentRepositoryDoesNotCountAsThisOne(
    fixtureStubTheSidecarAndTheSearch,
):
    """The already-hosts check is scoped to THIS repository.

    Matching on anything looser would make the first project in the
    workspace suppress adoption everywhere else -- the shape of the
    owner-map defect this repository has shipped once, where a lookup
    keyed on the wrong field passed every test whose fixture used one
    value for both.

    Kills: matching the already-hosts check on any project in
    the resource rather than on this repository, so the first
    project in the workspace suppresses adoption everywhere.
    """
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [{
        "sName": "A project belonging to another repository",
        "sPath": S_ROOT + "/otherRepo/.vaibify/projects/other.json",
        "sProjectRepoPath": S_ROOT + "/otherRepo",
    }]
    connectionFake = _fconnectionBareDirectory()
    dictReport = _fdictAdopt(connectionFake)
    assert dictReport["bProjectIsNew"] is True
    assert dictReport["sProjectPath"] == S_PROJECT_PATH
    assert S_PROJECT_PATH in connectionFake.dictWritten
