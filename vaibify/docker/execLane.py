"""Which environment a program the hub runs inside a container inherits.

The image's ``ENV PATH`` leads with the container user's ``~/.local/bin``
so that a researcher's pip-installed console scripts resolve from a
step command. That directory belongs to the in-container agent, and so
does the user site-packages directory beside it. A program the HUB runs
through that PATH therefore resolves through directories the agent can
write: a planted ``cat`` ran as root in the containment probes, and a
planted ``python3`` or a ``usercustomize.py`` could answer a typed read
with whatever the agent chose. The entrypoint already pins a fixed
system PATH for its own root phase (``fnUseFixedSystemPath``); this
module is the same guard for every ``docker exec`` the hub performs.

Every exec primitive takes a LANE:

- **hub-internal** (the default) runs the hub's own programs -- git
  state, typed reads, markers, containment probes -- with the fixed
  system PATH, ``/opt/conda/bin`` first when the image installs conda
  there (root-owned, ``Dockerfile``), and never ``~/.local/bin``. Its
  Python programs run as ``python3 -I``, which ignores the user site,
  every ``PYTHON*`` variable and the working directory.
- **integrity-read** is the hub-internal environment run as a SEPARATE
  read-only user: numeric UID :data:`I_INTEGRITY_READ_UID`, which no
  image needs an account for, in the container user's group so the
  group-readable home directories Ubuntu creates stay traversable.
  Everything the dashboard poll and the level gates read runs here --
  the snapshot hasher, the typed reads, the marker and plot digests --
  so a process the agent controls (UID 1000) can neither ptrace, signal
  nor read the ``/proc`` entry of the program that hashes a file for
  the dashboard, and the agent cannot alter a root-owned interpreter.
  What it buys is bounded: an agent can still CHANGE a file (reported
  as changed) or make it unreadable (reported as unknown). ``HOME`` is
  the root directory so git reads no agent-owned configuration, and
  ``safe.directory`` is granted because the repositories belong to
  another user.
- **researcher-command** is opt-in and keeps the image PATH: a step
  run, a test run, the terminal, an agent CLI, a preflight ``which``
  for the researcher's own command. These RUN the researcher's
  environment, so hiding it from them would be a defect, not a guard.

A caller declares a non-default lane with the constant, never a string
literal, so ``tests/testExecLaneInvariant.py`` can enumerate every
declaration by reading the source.
"""

__all__ = [
    "S_EXEC_LANE_HUB_INTERNAL",
    "S_EXEC_LANE_INTEGRITY_READ",
    "S_EXEC_LANE_RESEARCHER_COMMAND",
    "T_EXEC_LANES",
    "I_INTEGRITY_READ_UID",
    "DICT_INTEGRITY_READ_ENVIRONMENT",
    "fsIntegrityReadUser",
    "LIST_FIXED_SYSTEM_PATH",
    "S_CONDA_BIN_DIRECTORY",
    "LIST_HUB_PYTHON_ARGV",
    "S_HUB_PYTHON_COMMAND",
    "fnValidateExecLane",
    "fsImagePathFromEnvironmentList",
    "fsBuildHubInternalPath",
    "fdictBuildHubInternalEnvironment",
    "fdictResolveExecEnvironment",
]

S_EXEC_LANE_HUB_INTERNAL = "hub-internal"
S_EXEC_LANE_INTEGRITY_READ = "integrity-read"
S_EXEC_LANE_RESEARCHER_COMMAND = "researcher-command"
T_EXEC_LANES = (
    S_EXEC_LANE_HUB_INTERNAL, S_EXEC_LANE_INTEGRITY_READ,
    S_EXEC_LANE_RESEARCHER_COMMAND,
)

# A high numeric UID with no account in any image: ``docker exec``
# accepts a bare ``uid:group`` and creates no files, so nothing needs
# rebuilding. 65532 is the conventional "nonroot" of distroless images.
I_INTEGRITY_READ_UID = 65532

# The root directory as HOME: it exists, root owns it, and git finds no
# ``.gitconfig`` there. The ``GIT_CONFIG_*`` triple is git's
# command-scope configuration, one of the scopes ``safe.directory``
# honours, so a repository owned by the container user can be read by
# a different UID in an image whose system configuration never said so.
DICT_INTEGRITY_READ_ENVIRONMENT = {
    "HOME": "/",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "safe.directory",
    "GIT_CONFIG_VALUE_0": "*",
}

# The same list the entrypoint's root phase exports; the test
# ``tests/testExecLane.py`` holds the two spellings together.
LIST_FIXED_SYSTEM_PATH = [
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
    "/sbin", "/bin",
]

# Root-owned in every conda image the Dockerfile builds; prepended only
# when the image's own PATH names it, so a pip image never gains a
# directory it does not have.
S_CONDA_BIN_DIRECTORY = "/opt/conda/bin"

# ``-I`` isolates the interpreter from the user site, the working
# directory and every ``PYTHON*`` variable -- the three channels an
# agent can reach without root. The system site-packages, where the
# image installs its analysis stack as root, stay importable.
LIST_HUB_PYTHON_ARGV = ["python3", "-I"]
S_HUB_PYTHON_COMMAND = " ".join(LIST_HUB_PYTHON_ARGV)


def fnValidateExecLane(sLane):
    """Raise ``ValueError`` unless ``sLane`` names a declared lane."""
    if sLane not in T_EXEC_LANES:
        raise ValueError(
            f"{sLane!r} is not an exec lane; the declared lanes are "
            f"{list(T_EXEC_LANES)}"
        )


def fsImagePathFromEnvironmentList(listEnvironment):
    """Return the ``PATH`` value from a Docker ``Config.Env`` list, or ``""``."""
    if not isinstance(listEnvironment, (list, tuple)):
        return ""
    for sEntry in listEnvironment:
        if isinstance(sEntry, str) and sEntry.startswith("PATH="):
            return sEntry[len("PATH="):]
    return ""


def fsBuildHubInternalPath(sImagePath):
    """Return the fixed PATH, with ``/opt/conda/bin`` first when the image has it."""
    listDirectories = list(LIST_FIXED_SYSTEM_PATH)
    if S_CONDA_BIN_DIRECTORY in (sImagePath or "").split(":"):
        listDirectories.insert(0, S_CONDA_BIN_DIRECTORY)
    return ":".join(listDirectories)


def fdictBuildHubInternalEnvironment(sImagePath):
    """Return the environment every hub-internal exec carries."""
    return {"PATH": fsBuildHubInternalPath(sImagePath)}


def fdictResolveExecEnvironment(sLane, sImagePath, dictEnvironment=None):
    """Return the exec environment for ``sLane``, or ``None`` for none.

    A hub-internal exec always carries the fixed PATH, and a caller's
    own variables ride beside it -- the fixed PATH wins over a caller's
    PATH, so no call site can hand the image PATH back by accident. An
    integrity read adds the read-only user's HOME and git scope. A
    researcher-command exec carries exactly what the caller passed.
    """
    fnValidateExecLane(sLane)
    dictResolved = dict(dictEnvironment or {})
    if sLane != S_EXEC_LANE_RESEARCHER_COMMAND:
        dictResolved.update(fdictBuildHubInternalEnvironment(sImagePath))
    if sLane == S_EXEC_LANE_INTEGRITY_READ:
        dictResolved.update(DICT_INTEGRITY_READ_ENVIRONMENT)
    return dictResolved or None


def fsIntegrityReadUser(sContainerUser):
    """Return the ``uid:group`` the integrity-read lane execs as.

    The group is the container user's own: Ubuntu creates home
    directories group-readable and world-closed, so a reader outside
    the group could not enter them. A ``user:group`` spelling keeps
    its group; a name or a bare uid is its own primary group.
    """
    sGroup = (sContainerUser or "").strip()
    if ":" in sGroup:
        sGroup = sGroup.split(":", 1)[1]
    if not sGroup:
        sGroup = "1000"
    return f"{I_INTEGRITY_READ_UID}:{sGroup}"
