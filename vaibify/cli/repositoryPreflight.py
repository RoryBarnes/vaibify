"""Ask each repository's remote whether the configured branch exists.

A project's wizard-written ``branch: main`` met a remote whose default
branch is ``master``, and the container started without that
repository after an hour of building (a live start, 2026-09-21). The
remote answers the same question in a second through ``git ls-remote``,
which also names its default branch -- so the refusal can say what to
write instead of only what was wrong. A remote that cannot be asked
(no network, authentication the host does not hold) reports NOT
CHECKED: the entrypoint categorises those failures itself, and a
network miss is not evidence about a branch.
"""

import os
import subprocess

from .preflightResult import (
    PreflightResult, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_SCOPE_PROJECT,
)


__all__ = [
    "RemoteUnreachableError",
    "S_PREFLIGHT_NAME",
    "fdictProbeRepositoryBranch",
    "flistMissingBranches",
    "fpreflightRepositoryBranches",
    "fsDefaultBranchOfRemote",
]


S_PREFLIGHT_NAME = "repository-branches"
F_REMOTE_TIMEOUT_SECONDS = 20.0


class RemoteUnreachableError(Exception):
    """The remote gave no answer; nothing is known about its branches."""


def _fdictParseLsRemote(sStdout, sBranch):
    """Return the default branch and whether ``sBranch`` was listed."""
    sDefaultBranch = ""
    bBranchExists = False
    for sLine in sStdout.splitlines():
        if sLine.startswith("ref: refs/heads/") and sLine.endswith("\tHEAD"):
            sDefaultBranch = sLine[len("ref: refs/heads/"):-len("\tHEAD")]
        elif sLine.endswith(f"\trefs/heads/{sBranch}"):
            bBranchExists = True
    return {"bBranchExists": bBranchExists, "sDefaultBranch": sDefaultBranch}


def fdictProbeRepositoryBranch(sUrl, sBranch, fnRun=subprocess.run):
    """Return ``{bBranchExists, sDefaultBranch}`` from the remote, or raise.

    ``GIT_TERMINAL_PROMPT=0`` so a private remote fails rather than
    waits for a password nobody is there to type.
    """
    dictEnvironment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        processLsRemote = fnRun(
            ["git", "ls-remote", "--symref", sUrl, "HEAD",
             f"refs/heads/{sBranch}"],
            capture_output=True, text=True,
            timeout=F_REMOTE_TIMEOUT_SECONDS, env=dictEnvironment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as errorRun:
        raise RemoteUnreachableError(f"{sUrl}: {errorRun}") from errorRun
    if processLsRemote.returncode != 0:
        raise RemoteUnreachableError(
            f"{sUrl}: {(processLsRemote.stderr or '').strip()[:200]}",
        )
    return _fdictParseLsRemote(processLsRemote.stdout, sBranch)


def fsDefaultBranchOfRemote(sUrl, fnProbe=None):
    """Return the remote's default branch, or '' when it cannot be asked."""
    if fnProbe is None:
        fnProbe = fdictProbeRepositoryBranch
    try:
        return fnProbe(sUrl, "HEAD")["sDefaultBranch"]
    except RemoteUnreachableError:
        return ""


def flistMissingBranches(listRepositories, fnProbe):
    """Return ``(name, branch, default)`` for each branch a remote lacks."""
    listMissing = []
    for dictRepository in listRepositories or []:
        sUrl = dictRepository.get("url") or ""
        sBranch = dictRepository.get("branch") or ""
        if not sUrl or not sBranch:
            continue
        dictAnswer = fnProbe(sUrl, sBranch)
        if not dictAnswer["bBranchExists"]:
            listMissing.append((
                dictRepository.get("name") or sUrl, sBranch,
                dictAnswer["sDefaultBranch"],
            ))
    return listMissing


def _fsDescribeMissingBranch(sName, sBranch, sDefaultBranch):
    sSentence = f"'{sName}' names branch '{sBranch}', which its remote does not have"
    if sDefaultBranch:
        sSentence += f" (the remote's default branch is '{sDefaultBranch}')"
    return sSentence


def fpreflightRepositoryBranches(config, fnProbe=None):
    """Return a fail or not-checked result for the config's branches, else None.

    The probe is looked up at call time, never bound as a default, so a
    test that replaces it on the module reaches every caller and none
    touches a remote.
    """
    if fnProbe is None:
        fnProbe = fdictProbeRepositoryBranch
    listRepositories = getattr(config, "listRepositories", None) or []
    if not listRepositories:
        return None
    try:
        listMissing = flistMissingBranches(listRepositories, fnProbe)
    except RemoteUnreachableError as errorRemote:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the repository branches were not checked before the "
                f"build: {errorRemote}"
            ),
        )
    if not listMissing:
        return None
    sMissing = "; ".join(
        _fsDescribeMissingBranch(*tMissing) for tMissing in listMissing
    )
    return PreflightResult(
        sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_PROJECT,
        sMessage=(
            f"{sMissing}. The container would start without it, after the "
            "whole build."
        ),
        sRemediation=(
            "Set branch: under repositories in vaibify.yml to a branch the "
            "remote has, then build again."
        ),
    )
