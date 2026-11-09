"""Read each project's own dependency declaration from its remote, at one commit.

A container can hold several projects, and each declares what it needs
in its own repository. The image installs only what the build is told
about, so the build reads every declaration itself rather than trusting
the copy a container start later finds on disk: the Level 3 shadow and
``reproduce.sh`` run the image with its start bypassed, so a dependency
that arrives only at start is one they never see.

A project is a listed repository whose tree has ``.vaibify/``. A
``pip_no_deps`` or ``c_and_pip`` repository is installed without its
dependencies by design and is never read. Its declaration is the first
of the lock's own inputs (``pyproject.toml``, ``requirements.in``,
``requirements.txt``) at the repository root, so the image and the
lock are compiled from one file; the mirror the entrypoint writes is
never read, because it is a copy of the image, not a declaration.

Each declaration is read at ONE commit and the commit is recorded: the
branch is fetched shallow and blobless, and the commit is whatever that
fetch brought, so the record names exactly the bytes that were read.
Every git call carries the hardening list, runs in an empty directory
that selects no repository, and never prompts. GitHub is asked the way
the container clones it (``ftGitTransportFor``).

Only requirement lines are accepted. An option line, a URL, a path, an
editable install, or a reference to another file is refused, naming the
file and line, because none of them can be installed into an image the
way the researcher's file implies.
"""

import os
import re
import subprocess
import sys

from packaging.requirements import InvalidRequirement, Requirement

from vaibify.reproducibility.dependencyPinning import (
    S_VAIBIFY_REQUIREMENTS_PATH, T_LOCK_INPUT_CANDIDATES,
)
from vaibify.reproducibility.gitHardening import (
    LIST_GIT_HARDENING_CONFIG, fcontextOpenHermeticGitInvocation,
)

from .preflightResult import (
    PreflightResult, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_WARN,
    S_SCOPE_PROJECT,
)
from .pythonPackagePreflight import (
    IndexUnreachableError, fbIndexIsOverridden, flistUnknownPythonPackages,
    fsNormalizePackageName,
)
from . import repositoryPreflight
from .repositoryPreflight import (
    F_REMOTE_TIMEOUT_SECONDS, RemoteUnreachableError, ftGitTransportFor,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


__all__ = [
    "ProjectDeclarationError",
    "S_PREFLIGHT_NAME",
    "T_DECLARATION_FILES",
    "T_UNREAD_INSTALL_METHODS",
    "fdictReadProjectDeclaration",
    "flistCandidateRepositories",
    "flistParsePyprojectDependencies",
    "flistParseRequirementsText",
    "flistReadProjectDeclarations",
    "fpreflightProjectDeclarations",
]


S_PREFLIGHT_NAME = "project-declarations"
T_UNREAD_INSTALL_METHODS = ("pip_no_deps", "c_and_pip")
T_DECLARATION_FILES = tuple(
    sCandidate for sCandidate in T_LOCK_INPUT_CANDIDATES
    if sCandidate != S_VAIBIFY_REQUIREMENTS_PATH
)
_S_PROJECT_MARKER_DIRECTORY = ".vaibify"
_REGEX_COMMENT = re.compile(r"(^|\s)#.*$")
_REGEX_COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


class ProjectDeclarationError(Exception):
    """A project's declaration could not be read, or says something no image can install."""


def flistCandidateRepositories(listRepositories):
    """Return the listed repositories whose declaration the build reads."""
    return [
        dictRepository for dictRepository in listRepositories or []
        if dictRepository.get("url")
        and dictRepository.get("installMethod") not in T_UNREAD_INSTALL_METHODS
    ]


def flistReadProjectDeclarations(listRepositories, fnRun=subprocess.run):
    """Return the declaration of every project among ``listRepositories``.

    A candidate whose tree has no ``.vaibify/`` is not a project and is
    left out. Raises ``ProjectDeclarationError`` or
    ``RemoteUnreachableError`` on the first one that cannot be read.
    """
    listDeclarations = []
    for dictRepository in flistCandidateRepositories(listRepositories):
        dictDeclaration = fdictReadProjectDeclaration(dictRepository, fnRun)
        if dictDeclaration is not None:
            listDeclarations.append(dictDeclaration)
    return listDeclarations


def fdictReadProjectDeclaration(dictRepository, fnRun=subprocess.run):
    """Return one repository's declaration at the commit its branch names, or None.

    The answer is ``{sRepository, sUrl, sBranch, sCommit, sFile,
    listRequirements}``; ``sFile`` is empty when the project declares
    nothing at its root. None means the tree has no ``.vaibify/``.
    """
    sRepository = dictRepository.get("name") or dictRepository["url"]
    sUrl = dictRepository["url"]
    sBranch = _fsBranchToRead(dictRepository, sRepository, fnRun)
    sAskedUrl, listCredentialConfig = ftGitTransportFor(sUrl)
    with fcontextOpenHermeticGitInvocation(
        dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    ) as (sWorkingDirectory, dictEnvironment):
        def fsRunGit(*listArguments, bBytes=False):
            return _fsRunGit(
                [*listCredentialConfig, *listArguments], sUrl,
                sWorkingDirectory, dictEnvironment, fnRun, bBytes)
        fsRunGit("init", "-q")
        fsRunGit("remote", "add", "origin", sAskedUrl)
        fsRunGit("fetch", "-q", "--depth", "1", "--filter=blob:none",
                 "origin", f"refs/heads/{sBranch}")
        sCommit = fsRunGit("rev-parse", "FETCH_HEAD").strip()
        if not _REGEX_COMMIT.match(sCommit):
            raise RemoteUnreachableError(f"{sUrl}: no commit was fetched")
        dictRootEntries = _fdictParseRootTree(fsRunGit("ls-tree", "-z", sCommit))
        if dictRootEntries.get(_S_PROJECT_MARKER_DIRECTORY, ("", "", ""))[1] != "tree":
            return None
        sFile, sObject = _ftSelectDeclarationFile(dictRootEntries, sRepository)
        sText = _fsDecodeDeclaration(
            fsRunGit("cat-file", "blob", sObject, bBytes=True) if sFile else b"",
            sRepository, sFile)
    sLabel = f"{sRepository}'s {sFile} at {sCommit[:12]}"
    return {
        "sRepository": sRepository, "sUrl": sUrl, "sBranch": sBranch,
        "sCommit": sCommit, "sFile": sFile,
        "listRequirements": _flistParseDeclaration(sFile, sText, sLabel),
    }


def _fsBranchToRead(dictRepository, sRepository, fnRun):
    """Return the branch the container clones, after the remote confirms it."""
    sBranch = dictRepository.get("branch") or ""
    dictAnswer = repositoryPreflight.fdictProbeRepositoryBranch(
        dictRepository["url"], sBranch or "HEAD", fnRun)
    if not sBranch:
        sBranch = dictAnswer["sDefaultBranch"]
        if not sBranch:
            raise ProjectDeclarationError(
                f"{sRepository}: its remote names no default branch, so "
                "there is no commit to read its declaration from")
        return sBranch
    if not dictAnswer["bBranchExists"]:
        raise ProjectDeclarationError(
            f"{sRepository}: its remote has no branch '{sBranch}', so its "
            "declaration cannot be read")
    return sBranch


def _fsRunGit(listArguments, sUrl, sWorkingDirectory, dictEnvironment,
              fnRun, bBytes=False):
    """Return one hardened git call's output; a failed call raises unreachable."""
    try:
        processGit = fnRun(
            ["git", *LIST_GIT_HARDENING_CONFIG, *listArguments],
            capture_output=True, text=not bBytes,
            timeout=F_REMOTE_TIMEOUT_SECONDS, env=dictEnvironment,
            cwd=sWorkingDirectory,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as errorRun:
        raise RemoteUnreachableError(f"{sUrl}: {errorRun}") from errorRun
    if processGit.returncode != 0:
        sError = processGit.stderr
        if isinstance(sError, bytes):
            sError = sError.decode("utf-8", "replace")
        raise RemoteUnreachableError(f"{sUrl}: {(sError or '').strip()[:200]}")
    return processGit.stdout


def _fdictParseRootTree(sListing):
    """Return ``{name: (mode, type, object)}`` for the root of a ``ls-tree -z`` listing."""
    dictEntries = {}
    for sRecord in (sListing or "").split("\0"):
        if "\t" not in sRecord:
            continue
        sHeader, sName = sRecord.split("\t", 1)
        tHeader = tuple(sHeader.split())
        if len(tHeader) == 3:
            dictEntries[sName] = tHeader
    return dictEntries


def _ftSelectDeclarationFile(dictRootEntries, sRepository):
    """Return ``(file, object)`` for the first declaration the lock would read."""
    for sFile in T_DECLARATION_FILES:
        if sFile not in dictRootEntries:
            continue
        sMode, sType, sObject = dictRootEntries[sFile]
        if sType != "blob" or sMode not in ("100644", "100755"):
            raise ProjectDeclarationError(
                f"{sRepository}'s {sFile} is not an ordinary file (a "
                "symbolic link or a directory), so it cannot be read as "
                "a declaration")
        return sFile, sObject
    return "", ""


def _fsDecodeDeclaration(baContent, sRepository, sFile):
    try:
        return baContent.decode("utf-8")
    except UnicodeDecodeError as errorDecode:
        raise ProjectDeclarationError(
            f"{sRepository}'s {sFile} is not UTF-8 text") from errorDecode


def _flistParseDeclaration(sFile, sText, sLabel):
    if not sFile:
        return []
    if sFile == "pyproject.toml":
        return flistParsePyprojectDependencies(sText, sLabel)
    return flistParseRequirementsText(sText, sLabel)


def flistParseRequirementsText(sText, sLabel):
    """Return each requirement a requirements file declares, refusing the rest."""
    listRequirements = []
    for iLine, sRawLine in enumerate((sText or "").splitlines(), start=1):
        sLine = _REGEX_COMMENT.sub("", sRawLine).strip()
        if not sLine:
            continue
        if sLine.startswith("-") or sLine.endswith("\\"):
            raise ProjectDeclarationError(
                f"{sLabel}, line {iLine}: '{sRawLine.strip()}' is a pip "
                "option (an index, an editable install, another file, or a "
                "continued line), which an image's declaration cannot carry. "
                "List each package as a requirement line instead.")
        listRequirements.append(
            _fsParseOneRequirement(sLine, f"{sLabel}, line {iLine}"))
    return listRequirements


def flistParsePyprojectDependencies(sText, sLabel):
    """Return ``[project] dependencies`` from a pyproject.toml, refusing what cannot install."""
    try:
        dictDocument = tomllib.loads(sText or "")
    except tomllib.TOMLDecodeError as errorToml:
        raise ProjectDeclarationError(
            f"{sLabel} is not valid TOML: {errorToml}") from errorToml
    dictProject = dictDocument.get("project")
    if not isinstance(dictProject, dict):
        return []
    if "dependencies" in (dictProject.get("dynamic") or []):
        raise ProjectDeclarationError(
            f"{sLabel} declares its dependencies as dynamic, so they "
            "cannot be read without running its build backend. List them "
            "under [project] dependencies instead.")
    listDeclared = dictProject.get("dependencies") or []
    if not isinstance(listDeclared, list) or not all(
            isinstance(sEntry, str) for sEntry in listDeclared):
        raise ProjectDeclarationError(
            f"{sLabel}: [project] dependencies is not a list of strings")
    return [
        _fsParseOneRequirement(sEntry, f"{sLabel}, dependency {iEntry}")
        for iEntry, sEntry in enumerate(listDeclared, start=1)
    ]


def _fsParseOneRequirement(sText, sWhere):
    """Return one requirement in its standard spelling, or refuse it by name."""
    try:
        sUrl = Requirement(sText).url
    except InvalidRequirement as errorRequirement:
        raise ProjectDeclarationError(
            f"{sWhere}: '{sText}' is not a package requirement (a path or "
            f"URL is not accepted): {errorRequirement}") from errorRequirement
    if sUrl:
        raise ProjectDeclarationError(
            f"{sWhere}: '{sText}' names a URL. An image installs from the "
            "package index, so name the package and its version instead.")
    return str(Requirement(sText))


def fpreflightProjectDeclarations(config, fnRead=None, fbNameExists=None):
    """Return a fail, not-checked or warn result for the projects' declarations, else None.

    Unlike the branch check, a remote that cannot be asked FAILS here:
    the build itself must read every declaration, so a build started
    without one would stop at the same remote, after the container was
    already stopped. The readers are looked up at call time, so a test
    that replaces them on their modules reaches every caller.
    """
    if fnRead is None:
        fnRead = flistReadProjectDeclarations
    listRepositories = getattr(config, "listRepositories", None) or []
    if not flistCandidateRepositories(listRepositories):
        return None
    try:
        listDeclarations = fnRead(listRepositories)
    except (ProjectDeclarationError, RemoteUnreachableError) as errorRead:
        return _fpreflightUnreadable(errorRead)
    return _fpreflightUnknownNames(config, listDeclarations, fbNameExists) \
        or _fpreflightNamesAlsoListedAsRepositories(
            listRepositories, listDeclarations)


def _fpreflightUnreadable(errorRead):
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"A project's dependency declaration could not be read: "
            f"{errorRead}. The image installs every project's declared "
            "packages, so it cannot be built without it."),
        sRemediation=(
            "Fix the file named above and push it, or, for a private "
            "repository, sign in on this machine (`gh auth login`), then "
            "build again."),
    )


def _fpreflightUnknownNames(config, listDeclarations, fbNameExists):
    from . import pythonPackagePreflight
    if fbNameExists is None:
        fbNameExists = pythonPackagePreflight.fbNameExistsOnIndex
    if fbIndexIsOverridden(getattr(config, "sPipInstallFlags", "")):
        return None
    listUnknown = []
    try:
        for dictDeclaration in listDeclarations:
            for sRequirement in flistUnknownPythonPackages(
                    dictDeclaration["listRequirements"], fbNameExists):
                listUnknown.append(
                    f"'{sRequirement}' ({dictDeclaration['sRepository']}'s "
                    f"{dictDeclaration['sFile']})")
    except IndexUnreachableError as errorIndex:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the projects' package names were not checked before the "
                f"build: {errorIndex}"),
        )
    if not listUnknown:
        return None
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"{', '.join(listUnknown)} does not exist on the package index "
            "(pypi.org), so the build would stop at pip."),
        sRemediation="Fix the name in that file, push it, then build again.",
    )


def _fpreflightNamesAlsoListedAsRepositories(listRepositories, listDeclarations):
    """Warn where a declared package is also a repository the container installs at start.

    The image installs the declared package from the index; the
    container's start then installs the clone over it. The shadow rerun
    and ``reproduce.sh`` bypass the start, so they run the image's copy.
    Neither is chosen silently: the researcher is told.
    """
    dictRepositoryByName = {
        fsNormalizePackageName(dictRepository.get("name") or ""): dictRepository
        for dictRepository in listRepositories if dictRepository.get("name")
    }
    listNotes = []
    for dictDeclaration in listDeclarations:
        for sRequirement in dictDeclaration["listRequirements"]:
            sName = fsNormalizePackageName(Requirement(sRequirement).name)
            if sName in dictRepositoryByName:
                listNotes.append(
                    f"{sName} ({dictDeclaration['sRepository']} declares it)")
    if not listNotes:
        return None
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_WARN, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"{', '.join(sorted(set(listNotes)))}: the image installs this "
            "from the package index, and the container's start installs "
            "your listed clone over it. The shadow rerun and reproduce.sh "
            "use the image's copy."),
    )
