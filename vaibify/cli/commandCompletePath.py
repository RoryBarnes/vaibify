"""CLI subcommand: vaibify complete-path.

A HIDDEN helper for the shell completion scripts, never typed by a
researcher. On every TAB in ``vaibify push`` / ``vaibify pull`` the
scripts ask it for the paths that exist on one side of the transfer, so
the scripts keep only position logic and never learn how a project, a
container or a workspace root is found.

The contract is narrow on purpose. It prints one candidate per line, in
the form the researcher typed (relative stays relative, absolute stays
absolute, a directory ends in ``/``), and it prints NOTHING and exits 0
on every failure: a stopped container, an unknown project and an
unreachable daemon are all "no suggestions", because a completion
function has no channel on which to explain itself and an error message
here lands in the middle of the researcher's command line.

Names come from the inside of a container, where an agent may have
created anything, so a candidate is offered only if it cannot move the
terminal: any control character (a newline would split one candidate
into two, an escape sequence would be interpreted by the terminal that
displays the menu) removes the name from the answer.

This module is imported by ``main.py`` BEFORE the other commands, which
is the whole of its latency story: loading every command costs several
times what a TAB may spend, and none of them is needed to answer it.
"""

import contextlib
import io
import os
import posixpath
import signal
import threading
import unicodedata

import click

from .commandRemote import DeclaredNameArgument
from .configLoader import fconfigResolveProject


__all__ = ["fnCompletePathCommand", "flistCompletePathCandidates"]


S_SIDE_CONTAINER = "container"
S_SIDE_HOST = "host"
S_CONTROL_CHARACTER_CATEGORY = "Cc"

# What a TAB may wait for. The Docker client's own read timeout is ten
# minutes, set for long pushes, and a daemon that accepts a connection
# and never answers would freeze the researcher's prompt for all of it.
I_COMPLETION_TIME_LIMIT_SECONDS = 5


def ftSplitTypedPath(sPartial):
    """Return (the directory part, the name prefix) of a typed path.

    The directory part keeps its trailing slash so that candidates can
    be rebuilt in exactly the form that was typed.
    """
    iLastSlash = sPartial.rfind("/")
    return sPartial[:iLastSlash + 1], sPartial[iLastSlash + 1:]


def fbNameIsSafeToOffer(sName):
    """Return True when a name can be printed without side effects.

    A control character could move the terminal or split one candidate
    into two. A name that is not valid text (a byte string the
    filesystem allows and UTF-8 does not) would make the print itself
    fail, and a completion must fail by saying nothing, not by half.
    """
    try:
        sName.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(
        unicodedata.category(sCharacter) == S_CONTROL_CHARACTER_CATEGORY
        for sCharacter in sName
    )


def flistSelectOfferableNames(listNames, sPrefix):
    """Return the names a shell would offer for this prefix, safe to print.

    A name beginning with a dot is offered only when the prefix does, as
    every shell does for files: ``.git`` is never a surprise.
    """
    bOfferHidden = sPrefix.startswith(".")
    return [
        sName for sName in listNames
        if sName.startswith(sPrefix)
        and (bOfferHidden or not sName.startswith("."))
        and fbNameIsSafeToOffer(sName)
    ]


def _fsAppendDirectoryMark(sName, bIsDirectory):
    """Return the name, ending in a slash when it is a directory."""
    return sName + "/" if bIsDirectory else sName


def flistCompleteInContainer(
    sContainerName, sListedDirectory, sTypedDirectory, sPrefix,
):
    """Return completions of sPrefix inside a container directory.

    Two typed reads, not one per entry: the listing, then a single
    batched is-a-directory probe over only the names that matched.
    """
    from vaibify.docker.dockerConnection import DockerConnection
    connectionDocker = DockerConnection()
    listNames = flistSelectOfferableNames(
        connectionDocker.flistDirectoryEntries(
            sContainerName, sListedDirectory,
        ),
        sPrefix,
    )
    listIsDirectory = connectionDocker.flistContainerDirectoriesExist(
        sContainerName,
        [posixpath.join(sListedDirectory, sName) for sName in listNames],
    )
    return [
        sTypedDirectory + _fsAppendDirectoryMark(sName, bIsDirectory)
        for sName, bIsDirectory in zip(listNames, listIsDirectory)
    ]


def flistCompleteOnHost(sRootDirectory, sTypedDirectory, sPrefix):
    """Return completions of sPrefix inside a directory on this machine.

    A relative typed directory is read from ``sRootDirectory`` and an
    absolute one from itself, which is what ``os.path.join`` does with
    both; ``~`` is expanded for the read and kept as typed in the answer.
    """
    sListedDirectory = os.path.join(
        sRootDirectory, os.path.expanduser(sTypedDirectory),
    )
    listNames = sorted(flistSelectOfferableNames(
        os.listdir(sListedDirectory), sPrefix,
    ))
    return [
        sTypedDirectory + _fsAppendDirectoryMark(
            sName, os.path.isdir(os.path.join(sListedDirectory, sName)),
        )
        for sName in listNames
    ]


def fconfigResolveProjectQuietly(sProjectName):
    """Return the project push and pull would use, or None; never prints.

    ``fconfigResolveProject`` explains an unknown or ambiguous project
    on stdout and exits, which is right for a command and wrong for a
    completion. This is the same resolution with the explanation
    discarded, so ``-p`` and the registry mean here exactly what they
    mean to the command being completed.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            return fconfigResolveProject(sProjectName)
        except (SystemExit, Exception):
            return None


def _fsResolveHostRoot(configProject, sSide):
    """Return the directory host-side relative paths are read from.

    Empty when the answer is "inside the container". A host project has
    no container, so BOTH sides of a transfer are read from its own
    directory -- the same resolution ``vaibify push`` applies -- while a
    container project reads its host side from where the shell is.
    """
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(configProject.sProjectName) or {}
    if dictProject.get("sMode") == "host":
        return dictProject.get("sDirectory") or os.getcwd()
    if sSide == S_SIDE_HOST:
        return os.getcwd()
    return ""


def _flistCompleteForProject(configProject, sSide, sPartial):
    """Return completions of a typed path on the side the project names."""
    sTypedDirectory, sPrefix = ftSplitTypedPath(sPartial)
    sHostRoot = _fsResolveHostRoot(configProject, sSide)
    if sHostRoot:
        return flistCompleteOnHost(sHostRoot, sTypedDirectory, sPrefix)
    from vaibify.docker.fileTransfer import fsResolveContainerPath
    return flistCompleteInContainer(
        configProject.sProjectName,
        fsResolveContainerPath(
            sTypedDirectory or ".", configProject.sWorkspaceRoot,
        ),
        sTypedDirectory, sPrefix,
    )


def _fnRaiseCompletionTimeout(iSignal, _):
    """Abandon whatever the completion is waiting on."""
    raise TimeoutError("completion did not answer in time")


@contextlib.contextmanager
def fcontextLimitWallClockTime(iSeconds):
    """Interrupt the body after iSeconds; the caller sees ``TimeoutError``.

    Only the main thread can be signalled, so anywhere else the body runs
    unlimited rather than failing for a reason that has nothing to do
    with the researcher's path.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    fnPreviousHandler = signal.signal(
        signal.SIGALRM, _fnRaiseCompletionTimeout,
    )
    signal.alarm(iSeconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, fnPreviousHandler)


def flistCompletePathCandidates(sSide, sProjectName, sPartial):
    """Return the completions of a typed path, or an empty list."""
    configProject = fconfigResolveProjectQuietly(sProjectName)
    if configProject is None:
        return []
    try:
        with fcontextLimitWallClockTime(I_COMPLETION_TIME_LIMIT_SECONDS):
            return _flistCompleteForProject(configProject, sSide, sPartial)
    except Exception:  # noqa: BLE001 -- a completion has no way to complain
        return []


@click.command("complete-path", hidden=True)
@click.option(
    "--side", "sSide", required=True,
    type=click.Choice([S_SIDE_CONTAINER, S_SIDE_HOST]),
    help="Which end of a transfer the path is on.",
)
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name, resolved as push and pull resolve it.",
)
@click.argument(
    "sPartial", metavar="PARTIAL", default="", cls=DeclaredNameArgument,
)
def fnCompletePathCommand(sSide, sProjectName, sPartial):
    """List the paths that complete PARTIAL, one per line (TAB helper)."""
    listCandidates = flistCompletePathCandidates(
        sSide, sProjectName, sPartial,
    )
    if listCandidates:
        click.echo("\n".join(listCandidates))
