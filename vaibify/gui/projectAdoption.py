"""Adopt an existing directory in a resource as a vaibify Project.

Sandbox to Project is one step with many pieces: a directory becomes a
git repository if it is not one, gains a commit if it has none, gains a
``project.json`` that names itself, and joins the tracked-repository
sidecar so the dashboard lists it. Before this module those pieces were
separate routes a caller assembled by hand, and the assembly WAS the
defect. An in-container agent that wrote ``project.json`` directly --
which the container guide told it to do -- produced a directory the
dashboard could see but had never been told to offer, and every
workflow action then refused with a message about the session rather
than about the piece that was missing. The researcher's own words were
"I don't see how to turn this work into a project."

So: one entry point, one order, idempotent at every stage. A re-run
reports what was ALREADY true instead of refusing, because an agent
that cannot observe host-side state will retry, and a retry that
half-succeeds is worse than one that reports.

Every refusal carries a ``sRefusal`` code and an ``sRemedy`` naming the
next action, because the caller is frequently an agent with no view of
the dashboard: a refusal it cannot act on becomes an hour of improvised
shell work, which is how the defect above stayed invisible.

**This never rewrites history.** The empty initial commit that
``init-project-repo`` creates is a convenience, not a precondition.
Measured inside a real container: every git operation vaibify performs
succeeds on a repository whose root commit carries content
(``git diff --cached --quiet`` answers 0, ``git log -1 --format=%ct``
answers an epoch). Only an UNBORN HEAD -- a repository with no commits
at all -- degrades anything, and what it costs is ``SOURCE_DATE_EPOCH``,
which ``determinismEnvironment`` already treats as best-effort and
records as a skip. A repository with no commits therefore gains one; a
repository with any commit is left exactly as the researcher left it.
Grafting an empty root commit beneath existing history would rewrite
every commit id to buy nothing.
"""

__all__ = [
    "S_STAGE_CREATED_REPOSITORY",
    "S_STAGE_CREATED_INITIAL_COMMIT",
    "S_STAGE_WROTE_PROJECT_FILE",
    "S_STAGE_TRACKED_REPOSITORY",
    "T_ADOPTION_STAGES",
    "fdictAdoptDirectoryAsProject",
    "fnRefuseAdoption",
    "fsDeriveProjectFileName",
    "fsResolveProjectFileName",
    "fsValidateProjectDirectoryName",
    "fsValidateProjectFileName",
    "fsValidateProjectName",
]

import json
import posixpath
import re

from fastapi import HTTPException

from . import trackedReposManager
from . import workflowManager
from .pipelineRunner import fsShellQuote


# The four things adoption can do. A response names the ones it
# performed and the ones it found already satisfied, because "created"
# and "was already there" are different facts and a caller that cannot
# tell them apart cannot tell a successful retry from a no-op.
S_STAGE_CREATED_REPOSITORY = "created-git-repository"
S_STAGE_CREATED_INITIAL_COMMIT = "created-initial-commit"
S_STAGE_WROTE_PROJECT_FILE = "wrote-project-file"
S_STAGE_TRACKED_REPOSITORY = "tracked-repository"

T_ADOPTION_STAGES = (
    S_STAGE_CREATED_REPOSITORY,
    S_STAGE_CREATED_INITIAL_COMMIT,
    S_STAGE_WROTE_PROJECT_FILE,
    S_STAGE_TRACKED_REPOSITORY,
)

_PATTERN_PROJECT_FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_PATTERN_DIRECTORY_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

_I_MAXIMUM_NAME_LENGTH = 200

# New projects start with the same four-hour advisory per-step runtime
# limit a wizard-created project gets. Held here rather than imported
# from the route module so the two creation lanes cannot drift.
F_NEW_PROJECT_RUNTIME_LIMIT_SECONDS = 14400.0


def fnRefuseAdoption(iStatus, sRefusal, sMessage, sRemedy):
    """Raise a refusal that names both the problem and the way out.

    ``sRemedy`` is not decoration. The caller is often an in-container
    agent that cannot see the Repos panel, the Project field, or the
    filesystem outside its own directory, so a refusal without a next
    action is one it will work around rather than report.
    """
    raise HTTPException(iStatus, {
        "sMessage": sMessage,
        "sRemedy": sRemedy,
        "sRefusal": sRefusal,
    })


def fsValidateProjectDirectoryName(sDirectory):
    """Return the cleaned single-segment directory name, or refuse.

    Rejects separators before anything touches the container: a name
    carrying ``/`` or ``..`` is the path-traversal shape, and refusing
    it here means no later stage has to re-reason about it.
    """
    sClean = (sDirectory or "").strip().strip("/")
    if not sClean:
        fnRefuseAdoption(
            400, "adoption-directory-name-empty",
            "No directory was named.",
            "Pass sDirectory as the name of a directory that already "
            "exists in the workspace, for example 'myAnalysis'.",
        )
    if "/" in sClean or ".." in sClean:
        fnRefuseAdoption(
            400, "adoption-directory-name-not-one-segment",
            f"'{sClean}' is a path, not a directory name.",
            "Pass a single directory name with no '/' and no '..'. "
            "Only a directory at the top of the workspace can become "
            "a project repository.",
        )
    if sClean.startswith("."):
        fnRefuseAdoption(
            400, "adoption-directory-hidden",
            f"Refusing to adopt the hidden directory '{sClean}'.",
            "A project repository should not be hidden. Rename it to "
            "a visible name and adopt that.",
        )
    if not _PATTERN_DIRECTORY_NAME.match(sClean):
        fnRefuseAdoption(
            400, "adoption-directory-name-invalid",
            f"'{sClean}' is not a usable directory name.",
            "Use letters, digits, '_', '-' and '.', starting with a "
            "letter, digit or '_'.",
        )
    return sClean


def fsValidateProjectName(sProjectName):
    """Return the cleaned human-facing project name, or refuse.

    This is the name the Project field shows. It is validated, and
    written, separately from the file name because a project that
    names itself nothing falls back to displaying its own file name --
    which is what every hand-authored project observed in the field
    did, because the template they were copied from omits the key.
    """
    sClean = (sProjectName or "").strip()
    if not sClean:
        fnRefuseAdoption(
            400, "adoption-project-name-empty",
            "No project name was given.",
            "Pass sProjectName as the name the dashboard should show, "
            "for example 'Batch analysis run'.",
        )
    if len(sClean) > _I_MAXIMUM_NAME_LENGTH:
        fnRefuseAdoption(
            400, "adoption-project-name-too-long",
            f"The project name is {len(sClean)} characters; the "
            f"maximum is {_I_MAXIMUM_NAME_LENGTH}.",
            "Shorten sProjectName. It is a label, not a description.",
        )
    return sClean


def fsValidateProjectFileName(sFileName):
    """Return the normalized ``.json`` basename for a project file, or refuse.

    Accepts the name with or without its extension so a caller may
    pre-append it.

    The wizard's create route still carries its own copy of this rule
    (``_fsValidateAndNormalizeFileName``). Deliberately left there:
    its refusals are plain-string ``detail`` bodies that the wizard
    renders, and converting them to this module's
    ``{sMessage, sRemedy, sRefusal}`` shape would change a working
    error surface for no gain to adoption. The duplication is two
    copies of one regex, which is cheaper than the wrong abstraction;
    if a third appears, unify them then.
    """
    sClean = (sFileName or "").strip()
    if sClean.endswith(".json"):
        sClean = sClean[:-len(".json")]
    if not sClean or len(sClean) > _I_MAXIMUM_NAME_LENGTH:
        fnRefuseAdoption(
            400, "adoption-file-name-invalid",
            f"A project file name must be 1-{_I_MAXIMUM_NAME_LENGTH} "
            f"characters.",
            "Pass sFileName as a bare name such as 'batchAnalysis'; the "
            "'.json' extension is added for you.",
        )
    if "/" in sClean or ".." in sClean:
        fnRefuseAdoption(
            400, "adoption-file-name-invalid",
            f"'{sClean}' is a path, not a file name.",
            "Pass a bare file name with no '/' and no '..'. The file "
            "is always written inside the repository's .vaibify "
            "directory.",
        )
    if not _PATTERN_PROJECT_FILE_NAME.match(sClean):
        fnRefuseAdoption(
            400, "adoption-file-name-invalid",
            f"'{sClean}' is not a usable project file name.",
            "Use letters, digits, '_', '-' and '.', starting with a "
            "letter, digit or '_'.",
        )
    return sClean + ".json"


def fsDeriveProjectFileName(sProjectName):
    """Return a camelCase project file name derived from the display name.

    A caller that names the project but not the file gets a file named
    after it, so the two never disagree by accident. Words are joined
    camelCase because that is the file-naming convention throughout
    this repository, and a researcher reading
    ``.vaibify/projects/`` should not be able to tell which files a
    person named and which vaibify did.

    A name made entirely of characters the file-name rule forbids
    refuses through the same validator as an explicit one, rather than
    silently becoming a default name that matches no project.
    """
    saWords = [
        sWord for sWord in re.split(r"[^A-Za-z0-9]+", sProjectName or "")
        if sWord
    ]
    if not saWords:
        return fsValidateProjectFileName("")
    sCamel = saWords[0][:1].lower() + saWords[0][1:] + "".join(
        sWord[:1].upper() + sWord[1:] for sWord in saWords[1:]
    )
    return fsValidateProjectFileName(sCamel)


def fsResolveProjectFileName(sRequestedFileName, sProjectName):
    """Return the project file name: the caller's, or derived from the name.

    The one place an absent file name is turned into a real one. Both
    branches end in :func:`fsValidateProjectFileName`, so no caller can
    reach the write stage with a name that would resolve to a
    directory.
    """
    if (sRequestedFileName or "").strip():
        return fsValidateProjectFileName(sRequestedFileName)
    return fsDeriveProjectFileName(sProjectName)


def _fsResolveRepositoryPath(sResourceId, sDirectory):
    """Return the absolute container path of the directory to adopt.

    Resolved through ``trackedReposManager``, deliberately, rather than
    through ``projectRoots`` with a root this module chose. Adoption's
    last stage hands a bare repository NAME to the tracking sidecar,
    which resolves that name against its OWN root -- so if adoption
    resolved the same name against a different root, the two would
    disagree and tracking would report a repository it had just
    prepared as missing. Measured: it does exactly that, and the live
    acceptance lane caught it.

    Deferring to the tracking authority means the two cannot diverge.
    There is no second root here to keep in step.
    """
    try:
        sRoot = trackedReposManager.fsRepositoryRootFor(sResourceId)
    except ValueError as error:
        fnRefuseAdoption(
            409, "adoption-resource-root-unresolved",
            f"This resource has no directory recorded: {error}",
            "Re-register the resource, or open it from the hub so its "
            "directory is recorded, then adopt again.",
        )
    return posixpath.join(sRoot, sDirectory)


def _fbProbePath(fbProbe, sContainerId, sFullPath):
    """Return one declared path probe's answer, or refuse if it failed.

    The typed-read adapters raise ``OSError`` when the READ itself
    could not run, and return False only for a path that is genuinely
    absent. Collapsing those two would make an unobservable container
    look like an empty one, so a failed probe becomes a refusal that
    says so -- decided, because nothing has been written when it
    fires.
    """
    try:
        return fbProbe(sContainerId, sFullPath)
    except OSError as errorProbe:
        fnRefuseAdoption(
            500, "adoption-path-unobservable",
            f"Could not look at '{sFullPath}': {errorProbe}",
            "Check the container is running and the path is readable, "
            "then adopt again. Nothing was written, so a retry is "
            "safe.",
        )


def _fnRequireDirectoryPresent(connectionDocker, sContainerId, sFullPath):
    """Refuse unless sFullPath is an existing directory in the container.

    Both probes are declared typed reads rather than ``test`` execs.
    They answer a yes/no question about a path, which is exactly what
    the typed-read seam is for: the adapter builds the program, so the
    path cannot become program or shell syntax, and the site stops
    counting as one that could change a container.
    """
    if _fbProbePath(
        connectionDocker.fbContainerPathIsDirectory,
        sContainerId, sFullPath,
    ):
        return
    if _fbProbePath(
        connectionDocker.fbContainerPathIsFile, sContainerId, sFullPath,
    ):
        fnRefuseAdoption(
            400, "adoption-path-not-directory",
            f"'{sFullPath}' exists but is a file.",
            "A project repository is a directory. Adopt the directory "
            "that contains this file, or move the file aside.",
        )
    fnRefuseAdoption(
        404, "adoption-directory-missing",
        f"There is no directory at '{sFullPath}'.",
        "Create the directory first (the Files panel, or 'mkdir' in "
        "the terminal), then adopt it. Adoption never creates the "
        "directory, so a typo cannot silently produce an empty "
        "project.",
    )


def _fsDetectRepositoryRoot(connectionDocker, sContainerId, sFullPath):
    """Return the git work-tree root containing sFullPath, or ''.

    Empty means the directory is in no work tree at all. Any other
    answer is a real toplevel, which the caller must compare against
    the directory itself -- they are the same only when the directory
    IS a repository root.
    """
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"git -C {fsShellQuote(sFullPath)} rev-parse --show-toplevel",
    )
    if iExitCode != 0:
        return ""
    saLines = (sOutput or "").strip().splitlines()
    return saLines[-1].strip() if saLines else ""


def _fnRefuseDirectoryInsideAnotherRepository(sFullPath, sEnclosingRoot):
    """Refuse a directory that lives inside a different git repository.

    A project's repo root is auto-detected from its ``project.json``
    upwards, so adopting a SUBDIRECTORY of an existing repository would
    silently make the OUTER repository the project repo: every declared
    path would then resolve from a root the researcher never named.
    Refusing names the root that would have been chosen.
    """
    fnRefuseAdoption(
        409, "adoption-directory-inside-another-repository",
        f"'{sFullPath}' is inside the git repository at "
        f"'{sEnclosingRoot}', so adopting it would make "
        f"'{sEnclosingRoot}' the project repository and resolve every "
        f"declared path from there.",
        f"Adopt '{posixpath.basename(sEnclosingRoot)}' itself, or move "
        f"this directory out of that repository and adopt it "
        f"separately.",
    )


def _fbCreateRepositoryIfAbsent(
    connectionDocker, sContainerId, sFullPath,
):
    """Make sFullPath a git repository if it is not one; report whether it ran.

    Refuses first when the directory sits inside a DIFFERENT
    repository, because ``git init`` would then nest one repository
    inside another -- legal in git, and a surprise nobody asked for.
    """
    sExistingRoot = _fsDetectRepositoryRoot(
        connectionDocker, sContainerId, sFullPath,
    )
    if sExistingRoot and sExistingRoot != sFullPath:
        _fnRefuseDirectoryInsideAnotherRepository(sFullPath, sExistingRoot)
    if sExistingRoot == sFullPath:
        return False
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"git -C {fsShellQuote(sFullPath)} "
        f"-c init.defaultBranch=main init",
    )
    if iExitCode != 0:
        fnRefuseAdoption(
            500, "adoption-git-init-failed",
            f"Could not make '{sFullPath}' a git repository: "
            f"{(sOutput or '').strip()}",
            "Check the directory is writable by the container user, "
            "then adopt again. Nothing was written, so a retry is "
            "safe.",
        )
    return True


def _fbCreateInitialCommitIfUnborn(
    connectionDocker, sContainerId, sFullPath,
):
    """Give the repository a commit if it has none; report whether it ran.

    Only an unborn HEAD is acted on. A repository that already has a
    commit is left alone even when that commit carries content: see
    this module's docstring for what was measured, and why grafting an
    empty root commit beneath existing history would rewrite every
    commit id to buy nothing.
    """
    sQuoted = fsShellQuote(sFullPath)
    iHeadCode, _sHeadOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"git -C {sQuoted} rev-parse --verify HEAD",
    )
    if iHeadCode == 0:
        return False
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"git -C {sQuoted} "
        f"-c user.email=vaibify@local -c user.name=vaibify "
        f"commit --allow-empty -m 'Initialize vaibify project repo'",
    )
    if iExitCode != 0:
        fnRefuseAdoption(
            500, "adoption-initial-commit-failed",
            f"Could not create the initial commit in '{sFullPath}': "
            f"{(sOutput or '').strip()}",
            "A repository with no commit still runs, but its outputs "
            "are not byte-reproducible because SOURCE_DATE_EPOCH has "
            "no commit to read. Commit once in the terminal, then "
            "adopt again.",
        )
    return True


def _fdictFindProjectAlreadyInRepository(
    connectionDocker, sContainerId, sRepositoryPath,
):
    """Return the project this repository already hosts, or None.

    "Make this directory a Project" is a question about the DIRECTORY,
    not about a file name, so a directory that already hosts a project
    is already a Project -- whatever that project happens to be called.
    Answering at the level of the file name instead would write a
    SECOND project beside a hand-authored one whose name differed by a
    character, and both would look legitimate in the Project field with
    nothing to say which the agent had been working in. That is the
    real migration case: every project authored before adoption existed
    was named by hand.

    Adding a further project to a repository is the wizard's create
    route, which is the action that means "a new project". This one
    means "this directory, once".
    """
    for dictWorkflow in workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId,
    ):
        if dictWorkflow.get("sProjectRepoPath") == sRepositoryPath:
            return dictWorkflow
    return None


def _fnRequireProjectNameFree(
    connectionDocker, sContainerId, sProjectName, sTargetPath,
):
    """Refuse when another project in this resource already has this name.

    The project file being written is exempt: on a re-run it is the
    project whose name this is, and refusing there would make adoption
    non-idempotent.
    """
    for dictWorkflow in workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId,
    ):
        if dictWorkflow["sPath"] == sTargetPath:
            continue
        if dictWorkflow["sName"] != sProjectName:
            continue
        fnRefuseAdoption(
            409, "adoption-project-name-taken",
            f"A project named '{sProjectName}' already exists at "
            f"'{dictWorkflow['sPath']}'.",
            "Choose a different sProjectName, or open the existing "
            "project from the Project field in the toolbar.",
        )


def _fdictBlankProjectContent(sProjectName):
    """Return the minimum-viable project.json body for a fresh adoption.

    ``sWorkflowName`` is present and populated. A project file without
    it displays its own file name in the Project field, and the
    template every hand-authored project is copied from omitted the
    key -- so the omission propagated from one project to the next.
    """
    return {
        "sWorkflowName": sProjectName,
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": -1,
        "fDefaultWallClockBudgetSeconds":
            F_NEW_PROJECT_RUNTIME_LIMIT_SECONDS,
        "listSteps": [],
    }


def _fbWriteProjectFileIfAbsent(
    connectionDocker, sContainerId, sTargetPath, sProjectName,
):
    """Write the project file unless it is already there; report whether it ran.

    An existing file is never overwritten. A re-run of adoption must
    not discard steps the agent has since added, and "the file is
    already here" is the normal state of every retry.
    """
    if _fbProbePath(
        connectionDocker.fbContainerPathIsFile,
        sContainerId, sTargetPath,
    ):
        return False
    sContent = json.dumps(
        _fdictBlankProjectContent(sProjectName), indent=2,
    ) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sTargetPath, sContent.encode("utf-8"),
    )
    return True


def _fsEnsureProjectsDirectory(
    connectionDocker, sContainerId, sRepositoryPath,
):
    """Create the canonical .vaibify/projects directory and return its path."""
    sProjectsDirectory = posixpath.join(
        sRepositoryPath, workflowManager.VAIBIFY_PROJECTS_DIR,
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sProjectsDirectory)}",
    )
    if iExitCode != 0:
        fnRefuseAdoption(
            500, "adoption-projects-directory-failed",
            f"Could not create '{sProjectsDirectory}': "
            f"{(sOutput or '').strip()}",
            "Check the repository is writable by the container user, "
            "then adopt again. Nothing was written, so a retry is "
            "safe.",
        )
    return sProjectsDirectory


def _fbTrackRepositoryIfUntracked(
    connectionDocker, sContainerId, sDirectory,
):
    """Add the repository to the tracked sidecar; report whether it changed.

    Tracking is what makes the dashboard offer the project at all, and
    it is the piece an agent writing ``project.json`` by hand could
    neither perform nor observe -- the sidecar lives outside the
    directory the agent was confined to.
    """
    dictSidecar = trackedReposManager.fdictReadSidecar(
        connectionDocker, sContainerId,
    )
    listTracked = (dictSidecar or {}).get("listTracked") or []
    if any(dictEntry.get("sName") == sDirectory
           for dictEntry in listTracked):
        return False
    dictStatus = trackedReposManager.fdictComputeRepoStatus(
        connectionDocker, sContainerId, sDirectory,
    )
    if dictStatus.get("bMissing"):
        fnRefuseAdoption(
            404, "adoption-repository-untrackable",
            f"The repository '{sDirectory}' could not be read back "
            f"after it was prepared.",
            "Run 'vaibify reconcile' to resettle the container's "
            "records, then adopt again.",
        )
    trackedReposManager.fnAddTracked(
        connectionDocker, sContainerId, sDirectory,
        dictStatus.get("sUrl"),
    )
    return True


def fdictAdoptDirectoryAsProject(
    connectionDocker, sContainerId, sDirectory, sProjectName,
    sFileName="",
):
    """Make an existing directory a tracked vaibify Project, idempotently.

    The one path, and it validates its OWN inputs. An earlier cut left
    the file-name rule in the route and trusted callers to have applied
    it; driving the module directly with an empty name then joined a
    path ending in ``/``, the "is it already there" probe matched the
    PROJECTS DIRECTORY, and adoption reported ``wrote-project-file``
    already satisfied and overall success for a project that did not
    exist. A single entry point that can be corrupted by its own
    caller is not one path, so the rules live here. The route still
    validates first, so a malformed request never enters the carrier;
    both sides call these same validators and cannot disagree.

    Callers assemble no part of this sequence: every stage is here, in
    this order, and each reports whether it acted so the response can
    distinguish a first adoption from a retry.

    Runs entirely inside one held carrier drain. Every probe below
    reaches the general exec primitive, which the mutation gate treats
    as mutating because a primitive handed command text cannot know
    what the text does -- and the probes that GUARD the write belong
    inside the same drain as the write in any case, or a second session
    slips between the check and the write.

    Refusals are raised, not returned; the route carries them back out
    of the worker so an expected 4xx never poisons the journal record.
    """
    sDirectory = fsValidateProjectDirectoryName(sDirectory)
    sProjectName = fsValidateProjectName(sProjectName)
    sFileName = fsResolveProjectFileName(sFileName, sProjectName)
    sRepositoryPath = _fsResolveRepositoryPath(
        sContainerId, sDirectory,
    )
    _fnRequireDirectoryPresent(
        connectionDocker, sContainerId, sRepositoryPath,
    )
    return _fdictRunAdoptionStages(
        connectionDocker, sContainerId, sDirectory, sProjectName,
        sFileName, sRepositoryPath,
    )


def _fdictRunAdoptionStages(
    connectionDocker, sContainerId, sDirectory, sProjectName,
    sFileName, sRepositoryPath,
):
    """Run the four stages in order, on a directory known to exist.

    The order is the contract. The repository must exist before a
    commit can be made in it; the ``.vaibify`` directory must not be
    created until the nested-repository refusal has had its say, or a
    refused adoption would leave litter behind; and the name check
    guards the write, so it sits directly above it.
    """
    dictActed = {
        S_STAGE_CREATED_REPOSITORY: _fbCreateRepositoryIfAbsent(
            connectionDocker, sContainerId, sRepositoryPath,
        ),
        S_STAGE_CREATED_INITIAL_COMMIT: _fbCreateInitialCommitIfUnborn(
            connectionDocker, sContainerId, sRepositoryPath,
        ),
    }
    dictExisting = _fdictFindProjectAlreadyInRepository(
        connectionDocker, sContainerId, sRepositoryPath,
    )
    if dictExisting is not None:
        sTargetPath = dictExisting["sPath"]
        sProjectName = dictExisting["sName"]
        dictActed[S_STAGE_WROTE_PROJECT_FILE] = False
    else:
        sTargetPath = posixpath.join(
            _fsEnsureProjectsDirectory(
                connectionDocker, sContainerId, sRepositoryPath,
            ),
            sFileName,
        )
        _fnRequireProjectNameFree(
            connectionDocker, sContainerId, sProjectName, sTargetPath,
        )
        dictActed[S_STAGE_WROTE_PROJECT_FILE] = (
            _fbWriteProjectFileIfAbsent(
                connectionDocker, sContainerId, sTargetPath,
                sProjectName,
            )
        )
    dictActed[S_STAGE_TRACKED_REPOSITORY] = _fbTrackRepositoryIfUntracked(
        connectionDocker, sContainerId, sDirectory,
    )
    return _fdictBuildAdoptionReport(
        dictActed, sDirectory, sRepositoryPath, sTargetPath,
        sProjectName,
    )


def _fdictBuildAdoptionReport(
    dictActed, sDirectory, sRepositoryPath, sTargetPath, sProjectName,
):
    """Return the response: what adoption did, and what was already so.

    The two stage lists partition ``T_ADOPTION_STAGES``, and that is
    the load-bearing property. A caller distinguishes a first adoption
    from a safe retry by reading them, so a stage missing from both
    would be a stage nobody can ask about -- which is how a green
    answer comes to cover work that never happened.
    """
    return {
        "sDirectory": sDirectory,
        "sRepositoryPath": sRepositoryPath,
        "sProjectPath": sTargetPath,
        "sProjectName": sProjectName,
        "saStagesPerformed": [
            sStage for sStage in T_ADOPTION_STAGES if dictActed[sStage]
        ],
        "saStagesAlreadySatisfied": [
            sStage for sStage in T_ADOPTION_STAGES if not dictActed[sStage]
        ],
        "bProjectIsNew": dictActed[S_STAGE_WROTE_PROJECT_FILE],
    }
