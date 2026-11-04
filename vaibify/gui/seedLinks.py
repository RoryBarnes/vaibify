"""How a project's symbolic links cross into a container with its files.

Two rules, both for the seed copy only (``POST .../seed-workspace``),
so every other user of the tree copy is unchanged.

**The link contract.** A link the seed archive would carry, at the top
level or nested, crosses only when its target text is relative and,
resolved lexically from the link's own place in the DESTINATION layout,
stays inside the project: the rule ``fsValidatePathWithinRoot`` applies,
on the archive's repository-relative names, where a path stays inside
the root exactly when its normal form does not begin with ``..``. The
host's ``realpath`` is never consulted, because the link will be read
in the container. An absolute target names a host path that does not
exist there, or names something else, so it is never copied and never
rewritten. Each link left behind is reported by its repository-relative
path and the reason. A link that crosses is archived as itself; the
receiver lands it with ``symlink()`` and never follows it.

**The agent-doc links.** A converted repository may track ``CLAUDE.md``,
``AGENTS.md`` and ``GEMINI.md`` as links to ``.vaibify/AGENTS.md``. The
entrypoint makes them at container start, but the seed lands after the
first start, when ``.vaibify`` did not exist yet. So after the seed, a
fixed program, run as the container user, creates each one the
entrypoint would have: only where NOTHING exists at the name and only
when ``.vaibify/AGENTS.md`` exists. It never removes a file, never
replaces a link (a broken one included) and never touches a regular
file, so a researcher's own instruction file survives. The adoption
helper in ``replayRoutes`` runs ``rm -f`` before ``ln -s`` and must not
be used here.
"""

__all__ = [
    "S_AGENT_DOC_TARGET",
    "S_LINK_ABSOLUTE_TARGET",
    "S_LINK_OUTSIDE_PROJECT",
    "T_AGENT_DOC_NAMES",
    "fdictDescribeTopLevelLink",
    "ffnBuildSeedLinkFilter",
    "flistCreateMissingAgentDocLinks",
    "fsSeedLinkRefusal",
]

import json
import os
import posixpath

S_LINK_ABSOLUTE_TARGET = "absolute target"
S_LINK_OUTSIDE_PROJECT = "points outside the project"
S_AGENT_DOC_TARGET = ".vaibify/AGENTS.md"
T_AGENT_DOC_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")

# Run in the container as ``python3 -I -c <this> <root> <target> <names>``.
# Fixed text: the root, the target and the names arrive as arguments.
_S_AGENT_DOC_LINK_PROGRAM = r"""
import json, os, stat, sys
iRoot = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
listCreated = []
try:
    try:
        bSource = stat.S_ISREG(os.stat(sys.argv[2], dir_fd=iRoot).st_mode)
    except OSError:
        bSource = False
    for sName in (sys.argv[3:] if bSource else []):
        try:
            os.lstat(sName, dir_fd=iRoot)
            continue
        except FileNotFoundError:
            pass
        try:
            os.symlink(sys.argv[2], sName, dir_fd=iRoot)
        except FileExistsError:
            continue
        listCreated.append(sName)
finally:
    os.close(iRoot)
print(json.dumps(listCreated))
"""


def fsSeedLinkRefusal(sMemberPath, sLinkTarget):
    """Return why the link at a repository-relative path is not copied, or ''."""
    if posixpath.isabs(sLinkTarget):
        return S_LINK_ABSOLUTE_TARGET
    sResolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(sMemberPath), sLinkTarget))
    if sResolved == ".." or sResolved.startswith("../"):
        return S_LINK_OUTSIDE_PROJECT
    return ""


def ffnBuildSeedLinkFilter(listSkipped):
    """Return a ``tarfile`` filter that drops every link the contract refuses.

    Each dropped link is appended to ``listSkipped`` as ``{sPath,
    sReason}``. Member names are relative to the destination, which is
    the project's root, so they are the repository-relative paths.
    """
    def finfoKeepOrDrop(infoMember):
        if not infoMember.issym():
            return infoMember
        sReason = fsSeedLinkRefusal(infoMember.name, infoMember.linkname)
        if not sReason:
            return infoMember
        listSkipped.append({"sPath": infoMember.name, "sReason": sReason})
        return None
    return finfoKeepOrDrop


def fdictDescribeTopLevelLink(sHostPath):
    """Return a Files to Copy entry for one top-level link, never following it."""
    sName = os.path.basename(sHostPath)
    return {
        "sName": sName, "sPath": sHostPath, "bIsDirectory": False,
        "bHasConfig": False, "bIsLink": True,
        "sNotCopied": fsSeedLinkRefusal(sName, os.readlink(sHostPath)),
    }


def flistCreateMissingAgentDocLinks(connectionDocker, sContainerId, sProjectRoot):
    """Create each missing agent-doc link in the container; return the names made.

    Runs as the container user, under the caller's carrier admission.
    Raises ``RuntimeError`` with the program's own words when it fails.
    """
    from vaibify.docker.execLane import LIST_HUB_PYTHON_ARGV
    tExecResult = connectionDocker.ftRunProgramWithStdin(
        sContainerId,
        LIST_HUB_PYTHON_ARGV + [
            "-c", _S_AGENT_DOC_LINK_PROGRAM, sProjectRoot,
            S_AGENT_DOC_TARGET, *T_AGENT_DOC_NAMES,
        ],
        b"",
    )
    if tExecResult.iExitCode != 0:
        raise RuntimeError(
            (tExecResult.sStderr or "").strip()
            or f"the link program exited {tExecResult.iExitCode}")
    return json.loads(tExecResult.sStdout)
